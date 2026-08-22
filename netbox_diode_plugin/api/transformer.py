#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Object resolution for diffing."""

import copy
import datetime
import graphlib
import json
import logging
import re
from collections import defaultdict
from functools import lru_cache
from uuid import uuid4

from django.db.models.signals import post_delete, post_save
from django.utils.text import slugify
from extras.models.customfields import CustomField
from rest_framework import serializers

from .common import (
    MATCH_ONLY_TYPES,
    NON_FIELD_ERRORS,
    VC_MEMBER_HINT,
    AutoSlug,
    ChangeSetException,
    UnresolvedReference,
    harmonize_formats,
    sort_ints_first,
)
from .compat import apply_entity_migrations
from .field_policy import (
    apply_submitted_driver_field_policy,
    droppable_dependent_fields,
    prune_orphaned_nodes,
    referenced_uuids,
    release_rejected_edges,
)
from .matcher import (
    asserted_vc_identity,
    find_existing_object,
    fingerprints,
    partition_vc_identities,
    vc_unique_master_fingerprint,
)
from .plugin_utils import (
    CUSTOM_FIELD_OBJECT_REFERENCE_TYPE,
    apply_format_transformations,
    get_generic_object_variant,
    get_json_ref_info,
    get_object_type_model,
    get_primary_value,
    legal_fields,
)
from .profile import profiled

logger = logging.getLogger("netbox.diode_data")

# A field the running NetBox serializer accepts for write but that has no
# backing model field (create-time option, nested write helper). It cannot
# be ingested as object state, so it is dropped with a specific notice.
SERIALIZER_ONLY_FIELD_WARNING = (
    "Ignored field: accepted by the NetBox API but not supported by Diode "
    "ingestion (no corresponding model field)."
)

@lru_cache(maxsize=128)
def _camel_to_snake_case(name):
    """Convert camelCase string to snake_case."""
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()

@lru_cache(maxsize=1)
def _cable_terminable_types() -> frozenset:
    """
    Object types valid as a cable termination, per NetBox's own constant.

    Resolved from dcim.constants.CABLE_TERMINATION_MODELS so it tracks NetBox
    rather than a hand-maintained list. Cached: ContentType rows are stable.
    """
    from dcim.constants import CABLE_TERMINATION_MODELS
    from django.contrib.contenttypes.models import ContentType
    return frozenset(
        f"{ct.app_label}.{ct.model}"
        for ct in ContentType.objects.filter(CABLE_TERMINATION_MODELS)
    )

# Private context keys that DO land on the child node. The general rule below
# is that an underscore-prefixed context key contributes only ordering (its
# reference is added to the child's _refs, which is what _topo_sort reads) and
# is not copied as data. This set is the exception: a key here is copied too,
# because a matcher has to read it back.
#
# Only common.VC_MEMBER_HINT qualifies today, and both halves matter for it:
# the ordering half is what makes the value USEFUL (see the dcim.device entry
# in _NESTED_CONTEXT), and the data half is what makes it READABLE
# (matcher.VirtualChassisNameMatcher.resolve). Anything added here must be
# stripped again before changes are emitted -- see transform_proto_json.
_PRIVATE_CONTEXT_KEYS_KEPT = frozenset({VC_MEMBER_HINT})

# these are implied values pushed down to referenced objects.
_NESTED_CONTEXT = {
    "dcim.device": {
        # device.virtual_chassis -> the chassis node learns which member named
        # it. Two effects, and the entry is here for both:
        #
        #   DATA: matcher.VirtualChassisNameMatcher.resolve prefers, among
        #   several same-named chassis, the one this device ALREADY belongs to.
        #   That rule cannot live in the matcher on its own -- the matcher sees
        #   the VirtualChassis payload, which does not know the device. Here it
        #   does: this is the exact point where a device's payload nests the
        #   reference.
        #
        #   ORDERING: an UnresolvedReference in a context value adds the PARENT
        #   to the child's _refs, so _topo_sort emits the chassis node AFTER the
        #   member device. Without that the chassis node is resolved first and
        #   the hint is still an unresolved uuid, so an EXISTING device's pk --
        #   the only thing that can carry existing membership -- would never be
        #   known in time and the rule would silently never fire.
        #
        # The ordering has a visible cost, stated because it is a plan-shape
        # change and not an implementation detail: _handle_post_creates can no
        # longer merge the deferred device update back into the device's own
        # change for this shape (the chassis it references now sorts after the
        # device), so a member re-ingest plans device + chassis + deferred
        # update where it used to plan one device change carrying the chassis
        # inline. Both converge, and the deferred step is where position and
        # priority have to be asserted anyway (_POST_CREATE_COMPANIONS), but the
        # changeset is longer.
        #
        # A list, not a scalar: several members of one chassis in one batch
        # fingerprint-dedupe into a single chassis node, and _merge_nodes unions
        # this key so every member's evidence survives the merge. Dropping to
        # the first one would make the rule "prefer the chassis the FIRST-named
        # member belongs to", which is an arbitrary choice of exactly the kind
        # this whole path exists to remove.
        "virtual_chassis": lambda object_type, uuid: {
            VC_MEMBER_HINT: [UnresolvedReference(object_type=object_type, uuid=uuid)],
        },
    },
    "dcim.interface": {
        # interface.primary_mac_address -> mac_address.assigned_object = interface
        "primary_mac_address": lambda object_type, uuid: {
            "assigned_object_type": object_type,
            "assigned_object_id": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
    "virtualization.vminterface": {
        # interface.primary_mac_address -> mac_address.assigned_object = vinterface
        "primary_mac_address": lambda object_type, uuid: {
            "assigned_object_type": object_type,
            "assigned_object_id": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
    "virtualization.virtualmachine": {
        "primary_ip4": lambda object_type, uuid: {
            "__force_after": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
        "primary_ip6": lambda object_type, uuid: {
            "__force_after": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
    "dcim.virtualdevicecontext": {
        "primary_ip4": lambda object_type, uuid: {
            "__force_after": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
        "primary_ip6": lambda object_type, uuid: {
            "__force_after": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
}

def _no_context(object_type, uuid):
    return None

def _nested_context(object_type, uuid, field_name):
    return _NESTED_CONTEXT.get(object_type, {}).get(field_name, _no_context)(object_type, uuid)

_IS_CIRCULAR_REFERENCE = {
    "dcim.interface": frozenset(["primary_mac_address"]),
    "virtualization.vminterface": frozenset(["primary_mac_address"]),
    "dcim.device": frozenset(["primary_ip4", "primary_ip6", "oob_ip", "virtual_chassis"]),
    "dcim.virtualdevicecontext": frozenset(["primary_ip4", "primary_ip6"]),
    "virtualization.virtualmachine": frozenset(["primary_ip4", "primary_ip6"]),
    "circuits.provider": frozenset(["accounts"]),
    # dcim.modulebay carries both sides of the module/bay relation, and they are
    # here for opposite reasons:
    #   installed_module is the reverse of Module.module_bay. A bay that nests the
    #     module occupying it is named back by that module (Module.module_bay is a
    #     required OneToOneField), and the two bay nodes fingerprint-dedupe into
    #     one, so the cycle only appears at the SECOND _topo_sort -- which is why
    #     re-ingesting rows the database already agrees with failed as well.
    #     Deferring the reverse write orders it bay -> module -> bay update. Note
    #     what that buys: a plannable order, not a write. The module's own
    #     module_bay FK is what installs it; the deferred update re-asserts the
    #     same relation through Django's reverse-one-to-one accessor, which only
    #     mutates the related object in memory (and NetBox's serializer pops
    #     reverse relations before full_clean), so it persists nothing.
    #   module is the forward parent FK, and per NetBox's ModuleBay.clean() a bay
    #     may not be a sub-bay of the module installed in it. It is declared only
    #     so that payload reaches that model error instead of the opaque
    #     plan-time cycle error -- it is a better-error path, not a working shape.
    "dcim.modulebay": frozenset(["module", "installed_module"]),
}

def _is_circular_reference(object_type, field_name):
    return field_name in _IS_CIRCULAR_REFERENCE.get(object_type, frozenset())

# Scalar fields that MOVE to the deferred step when their companion ref is
# deferred: they only mean anything alongside that ref, so they have to be
# asserted in the same write as it.
#
# NetBox's assign_virtualchassis_master signal forces an inline master's
# vc_position to 1 when a VirtualChassis is created, so the deferred device
# update must re-assert the submitted position/priority after that create.
# Do NOT "fix" this back into a copy that also leaves the scalars on the main
# node -- that is where they do damage:
#   - on a device CREATE they are pointless there. The VC row is created after
#     the device, and the signal overwrites whatever position the device was
#     created with; only the deferred update can make the submitted position
#     stick.
#   - on a device UPDATE they are a bug. The main update runs BEFORE the
#     chassis change, so a device moving from chassis A to chassis B into a
#     position that is free in B but taken in A momentarily claims
#     (A, new_position) and NetBox's (virtual_chassis, vc_position) uniqueness
#     constraint rejects an otherwise legal move.
# When _handle_post_creates merges the deferred step back into the main node
# (it does whenever nothing that step references is ordered later) chassis and
# position land in one write again, which is equally correct.
_POST_CREATE_COMPANIONS = {
    ("dcim.device", "virtual_chassis"): ("vc_position", "vc_priority"),
}


def _move_deferred_companions(object_type, node, post_create):
    """Move companion scalars off the main node onto its deferred post-create node."""
    for (companion_type, ref_field), scalar_fields in _POST_CREATE_COMPANIONS.items():
        if companion_type != object_type or ref_field not in post_create:
            continue
        for scalar_field in scalar_fields:
            if scalar_field in node:
                post_create[scalar_field] = node.pop(scalar_field)

@profiled("transform")
def transform_proto_json(proto_json: dict, object_type: str, supported_models: dict) -> list[dict]: # noqa: C901
    """
    Transform keys of proto json dict to flattened dictionaries with model field keys.

    This also handles placing `_type` fields for generic references,
    a certain form of deduplication and resolution of existing objects.
    """
    entities = _transform_proto_json_1(proto_json, object_type, supported_models)

    entities = _topo_sort(entities)
    # Phase 1 of the driver field policy. It runs before _resolve_existing_references
    # because a dropped field can itself be a match criterion, and dropping it after the
    # lookup strands the entity as a CREATE that re-plans on every ingest. This pass
    # settles nodes that carry the contradiction on their own; duplicates that SPLIT it
    # only become contradictory as they merge, which _fingerprint_dedupe handles.
    # `referenced_before` is the reference graph before any drop, which the prune sweep
    # needs to tell a nested child from a root.
    referenced_before = referenced_uuids(entities)
    released = apply_submitted_driver_field_policy(entities)
    deduplicated, merge_released = _fingerprint_dedupe(entities)
    # Everything is merged and normalized, so a conflict dedupe held back is real.
    raise_unsettled_conflicts(deduplicated)
    deduplicated = _topo_sort(deduplicated)
    # ONE sweep, never inside a pass: see prune_orphaned_nodes.
    if released or merge_released:
        deduplicated = prune_orphaned_nodes(deduplicated, referenced_before)
    _set_auto_slugs(deduplicated, supported_models)
    _handle_cached_scope(deduplicated, supported_models)
    resolved = _resolve_existing_references(deduplicated)
    _strip_cached_scope(resolved)
    defaulted = _set_defaults(resolved, supported_models)

    # handle post-create steps
    output = _handle_post_creates(defaulted)

    _check_unresolved_refs(output)
    for entity in output:
        entity.pop('_refs', None)
        # Matching is done; the hint must not reach a change. differ pops the
        # private keys it knows by name, so anything left here is emitted as
        # change data -- where cleanup_unresolved_references would stringify an
        # unresolved device ref into it and report the hint as a new_ref.
        for private_key in _PRIVATE_CONTEXT_KEYS_KEPT:
            entity.pop(private_key, None)

    return output

def _transform_proto_json_1(proto_json: dict, object_type: str, supported_models: dict, context=None) -> list[dict]: # noqa: C901
    uuid = str(uuid4())
    node = {
        "_object_type": object_type,
        "_uuid": uuid,
        "_refs": set(),
        "_warnings": {},
    }

    # extract metadata before _ensure_snake_case strips it as an unknown field
    metadata = dict.pop(proto_json, "metadata", None)

    # handle camelCase protoJSON if provided...
    proto_json = _ensure_snake_case(proto_json, object_type)
    apply_format_transformations(proto_json, object_type)
    apply_entity_migrations(proto_json, object_type)

    # context pushed down from parent nodes
    if context is not None:
        for k, v in context.items():
            if k in _PRIVATE_CONTEXT_KEYS_KEPT:
                # Deep-copied, unlike the ordinary context values below: one
                # nested_context dict is reused for every item of a list-valued
                # reference, and _merge_nodes MUTATES this key (it unions the
                # lists), so siblings sharing one list object would accumulate
                # each other's members.
                node[k] = copy.deepcopy(v)
            elif not k.startswith("_"):
                node[k] = v
            for ref in (v if isinstance(v, list | tuple) else [v]):
                if isinstance(ref, UnresolvedReference):
                    node['_refs'].add(ref.uuid)

    nodes = [node]
    post_create = None

    # special handling for custom fields
    custom_fields = dict.pop(proto_json, "custom_fields", {})
    if custom_fields:
        custom_fields, custom_fields_refs, nested = _prepare_custom_fields(object_type, custom_fields, supported_models)
        node['custom_fields'] = custom_fields
        node['_refs'].update(custom_fields_refs)
        nodes += nested

    # process extracted metadata for PK-based matching
    if metadata and isinstance(metadata, dict):
        source_match = metadata.get("source_match", {})
        if isinstance(source_match, dict):
            netbox_id_raw = source_match.get("netbox_id")
            if netbox_id_raw is not None:
                try:
                    node['_netbox_id'] = int(netbox_id_raw)
                except (ValueError, TypeError):
                    node['_warnings']['metadata'] = [f"Invalid netbox_id: {netbox_id_raw}"]

    supported_fields = _supported_diode_fields(object_type, supported_models)
    def is_supported(field_name, ref_info):
        if ref_info is None:
            return field_name in supported_fields
        if ref_info.is_generic_object:
            return ref_info.field_name in legal_fields(object_type)
        if ref_info.object_type not in supported_models:
            return False
        if ref_info.is_generic:
            return ref_info.field_name + "_type" in supported_fields
        return ref_info.field_name in supported_fields

    serializer_only = supported_models.get(object_type, {}).get("serializer_only_fields", set())
    for key, value in proto_json.items():
        ref_info = get_json_ref_info(object_type, key)
        if not is_supported(key, ref_info):
            warning_key = ref_info.field_name if ref_info else key
            if warning_key in serializer_only:
                node['_warnings'][key] = [SERIALIZER_ONLY_FIELD_WARNING]
            else:
                node['_warnings'][key] = ["Ignored unsupported field."]
            continue

        if ref_info is None:
            node[key] = copy.deepcopy(value)
            continue

        nested_context = _nested_context(object_type, uuid, ref_info.field_name)
        field_name = ref_info.field_name
        is_circular = _is_circular_reference(object_type, field_name)

        if ref_info.is_generic:
            node[field_name + "_type"] = ref_info.object_type
            field_name = field_name + "_id"

        # Explicitly null reference — means "clear this FK".
        # An empty dict {} comes from protojson when the SDK sets a message field
        # to an empty message (e.g. Tenant{}) to signal "clear this field".
        if value is None or (isinstance(value, dict) and len(value) == 0):
            if ref_info.is_generic:
                node[ref_info.field_name + "_type"] = None
            if is_circular:
                if post_create is None:
                    post_create = {
                        "_uuid": str(uuid4()),
                        "_object_type": object_type,
                        "_refs": set(),
                        "_instance": node['_uuid'],
                        "_is_post_create": True,
                    }
                post_create[field_name] = None
                post_create['_refs'].add(node['_uuid'])
            else:
                node[field_name] = None
            continue

        refs = []
        ref_value = None
        if isinstance(value, list):
            ref_value = []
            for item in value:
                if ref_info.is_generic_object:
                    # Single-key GenericObject wrapper: the key names the
                    # variant, e.g. {"object_interface": {...}}.
                    if not isinstance(item, dict) or len(item) != 1:
                        node['_warnings'][field_name] = node['_warnings'].get(field_name, []) + [
                            f"Skipping malformed generic-object item (expected single-key dict): {item!r}"
                        ]
                        continue
                    raw_variant_key = next(iter(item))
                    # camelCase protoJSON is normalized at the top level only;
                    # the nested variant key needs its own normalization.
                    variant_key = _camel_to_snake_case(raw_variant_key)
                    concrete_type = get_generic_object_variant(variant_key)
                    if concrete_type is None:
                        node['_warnings'][field_name] = node['_warnings'].get(field_name, []) + [
                            f"Skipping unknown generic-object variant key: {variant_key!r}"
                        ]
                        continue
                    if concrete_type not in supported_models:
                        node['_warnings'][field_name] = node['_warnings'].get(field_name, []) + [
                            f"Skipping generic-object variant {variant_key!r}: "
                            f"{concrete_type} is not supported in this version."
                        ]
                        continue
                    # The GenericObject variant map spans every content type, but
                    # only NetBox's cable-terminable models are valid endpoints.
                    # Reject others up front rather than recursing to create the
                    # child object before the cable serializer rejects it.
                    if object_type == "dcim.cable" and concrete_type not in _cable_terminable_types():
                        node['_warnings'][field_name] = node['_warnings'].get(field_name, []) + [
                            f"Skipping generic-object variant {variant_key!r}: "
                            f"{concrete_type} is not a valid cable termination type."
                        ]
                        continue
                    item_payload = item[raw_variant_key]
                    nested = _transform_proto_json_1(item_payload, concrete_type, supported_models)
                    nodes += nested
                    ref_uuid = nested[0]['_uuid']
                    ref_value.append({
                        'object_type': concrete_type,
                        'object_id': UnresolvedReference(object_type=concrete_type, uuid=ref_uuid),
                    })
                    refs.append(ref_uuid)
                else:
                    nested = _transform_proto_json_1(item, ref_info.object_type, supported_models, nested_context)
                    nodes += nested
                    ref_uuid = nested[0]['_uuid']
                    ref_value.append(UnresolvedReference(
                        object_type=ref_info.object_type,
                        uuid=ref_uuid,
                    ))
                    refs.append(ref_uuid)
        else:
            nested = _transform_proto_json_1(value, ref_info.object_type, supported_models, nested_context)
            nodes += nested
            ref_uuid = nested[0]['_uuid']
            ref_value = UnresolvedReference(
                object_type=ref_info.object_type,
                uuid=ref_uuid,
            )
            refs.append(ref_uuid)

        if is_circular:
            if post_create is None:
                post_create = {
                    "_uuid": str(uuid4()),
                    "_object_type": object_type,
                    "_refs": set(),
                    "_instance": node['_uuid'],
                    "_is_post_create": True,
                }
            post_create[field_name] = ref_value
            post_create['_refs'].update(refs)
            post_create['_refs'].add(node['_uuid'])
            continue

        node[field_name] = ref_value
        node['_refs'].update(refs)

    if post_create:
        _move_deferred_companions(object_type, node, post_create)
        nodes.append(post_create)

    return nodes

def _ensure_snake_case(proto_json: dict, object_type: str) -> dict:
    fields = legal_fields(object_type)
    out = {}
    for k, v in proto_json.items():
        if k in fields or get_json_ref_info(object_type, k):
            out[k] = v
            continue
        snake_key = _camel_to_snake_case(k)
        if snake_key in fields or get_json_ref_info(object_type, snake_key):
            out[snake_key] = v
        else:
            # error?
            sanitized_k = k.replace('\n', '').replace('\r', '')
            sanitized_snake_key = snake_key.replace('\n', '').replace('\r', '')
            sanitized_object_type = object_type.replace('\n', '').replace('\r', '')
            logger.warning(f"Unknown field {sanitized_k}/{sanitized_snake_key} is not legal for {sanitized_object_type}, skipping...")
    return out


def _topo_sort(entities: list[dict]) -> list[dict]:
    """
    Topologically sort entities by reference.

    Within each topological level, entities are visited in a deterministic
    content-based order (object_type, primary identifier) so concurrent
    workers process shared lookup rows — Site, Region, DeviceRole, ... — in
    identical sequence. Without this, two workers can each insert a pair of
    shared lookups in opposite orders and Postgres detects an A↔B lock cycle
    on the unique index (e.g. dcim_devicerole_name), aborting one as a
    deadlock.

    graphlib.TopologicalSorter preserves insertion order at ties, so the
    pre-sort propagates into the topological output.
    """
    entities = sorted(entities, key=_stable_topo_key)
    by_uuid = {e['_uuid']: e for e in entities}
    graph = defaultdict(set)
    for entity in entities:
        graph[entity['_uuid']] = entity['_refs'].copy()

    try:
        ts = graphlib.TopologicalSorter(graph)
        order = tuple(ts.static_order())
        return [by_uuid[uuid] for uuid in order]
    except graphlib.CycleError as e:
        # TODO the cycle error references the cycle here ...
        raise ChangeSetException(f"Circular reference in entities: {e}", errors={
            NON_FIELD_ERRORS: {
                NON_FIELD_ERRORS: "Unable to resolve circular reference in entities",
            }
        })


# Fields tried in order to derive a stable, content-based sort key.
# Covers the natural primary identifier for most NetBox object types.
_STABLE_KEY_FIELDS = ("name", "slug", "model", "serial", "address", "mac_address")


def _stable_topo_key(entity: dict) -> tuple:
    """Deterministic content-based sort key for an entity at the same topo level."""
    ot = entity.get("_object_type", "")
    for k in _STABLE_KEY_FIELDS:
        v = entity.get(k)
        if isinstance(v, str):
            return (ot, k, v)
    # Last resort — UUIDs differ per request but at least within one request
    # this gives a stable ordering for entities lacking the common id fields.
    return (ot, "_uuid", entity.get("_uuid", ""))


def _set_defaults(entities: list[dict], supported_models: dict):
    out = []
    for entity in entities:
        entity = copy.deepcopy(entity)
        model_fields = supported_models.get(entity['_object_type'])
        if model_fields is None:
            raise serializers.ValidationError({
                NON_FIELD_ERRORS: [f"Model for object type {entity['_object_type']} is not supported"]
            })

        auto_slug = entity.pop("_auto_slug", None)
        if entity.get("_instance"):
            out.append(entity)
            continue

        if auto_slug:
            if auto_slug.field_name not in entity:
                entity[auto_slug.field_name] = auto_slug.value

        legal = legal_fields(entity['_object_type'])
        for field_name, field_info in model_fields.get('fields', {}).items():
            if field_name not in legal:
                continue
            if entity.get(field_name) is None and field_info.get("default") is not None:
                default = field_info["default"]
                if callable(default):
                    default = default()
                entity[field_name] = default
        set_custom_field_defaults(entity, model_fields['model'])
        out.append(harmonize_formats(entity))
    return out

def _handle_cached_scope(entities: list[dict], supported_models: dict):
    by_type_id = {
        (entity['_object_type'], entity['_uuid']): entity
        for entity in entities
    }
    for entity in entities:
        model = supported_models.get(entity['_object_type'], {}).get("model")
        if _has_cached_scope(model):
            _handle_cached_scope_1(entity, by_type_id)

def _strip_cached_scope(entities: list[dict]):
    for entity in entities:
        entity.pop("_region", None)
        entity.pop("_site_group", None)
        entity.pop("_site", None)
        entity.pop("_location", None)

@lru_cache(maxsize=256)
def _has_cached_scope(model):
    return  hasattr(model, "cache_related_objects") and hasattr(model, "scope")

def _handle_cached_scope_1(entity: dict, by_type_id: dict):
    # these are some auto-set fields that cache scope information,
    # some indexes rely on them. Here we attempt to emulate that behavior
    # for the purpose of matching.  These generally only exist after save.
    scope_type = entity.get("scope_type")
    scope_id = entity.get("scope_id")

    if scope_type and scope_id:
        scope = by_type_id.get((scope_type, scope_id.uuid))
        if scope_type == "dcim.region":
            _cache_region_ref(entity, scope_id)
        elif scope_type == "dcim.sitegroup":
            _cache_site_group_ref(entity, scope_id)
        elif scope_type == "dcim.site":
            _cache_site_ref(entity, scope_id)
            _cache_region_ref(entity, scope.get("region"))
            _cache_site_group_ref(entity, scope.get("group"))
        elif scope_type == "dcim.location":
            _cache_location_ref(entity, scope_id)
            site_ref = scope.get("site")
            if site_ref is not None and isinstance(site_ref, UnresolvedReference):
                _cache_site_ref(entity, site_ref)
                site_obj = by_type_id.get((site_ref.object_type, site_ref.uuid))
                if site_obj is not None:
                    _cache_region_ref(entity, site_obj.get("region"))
                    _cache_site_group_ref(entity, site_obj.get("group"))

def _cache_region_ref(entity: dict, ref: UnresolvedReference|None):
    if ref is None:
        return
    entity["_region"] = UnresolvedReference(
        object_type=ref.object_type,
        uuid=ref.uuid,
    )

def _cache_site_group_ref(entity: dict, ref: UnresolvedReference|None):
    if ref is None:
        return
    entity["_site_group"] = UnresolvedReference(
        object_type=ref.object_type,
        uuid=ref.uuid,
    )

def _cache_site_ref(entity: dict, ref: UnresolvedReference|None):
    if ref is None:
        return
    entity["_site"] = UnresolvedReference(
        object_type=ref.object_type,
        uuid=ref.uuid,
    )

def _cache_location_ref(entity: dict, ref: UnresolvedReference|None):
    if ref is None:
        return
    entity["_location"] = UnresolvedReference(
        object_type=ref.object_type,
        uuid=ref.uuid,
    )

@lru_cache(maxsize=256)
def _get_custom_fields_for_model(model):
    """Cached wrapper for CustomField.objects.get_for_model()."""
    return tuple(CustomField.objects.get_for_model(model))


def _on_custom_field_change(**kwargs):
    _get_custom_fields_for_model.cache_clear()


post_save.connect(_on_custom_field_change, sender=CustomField)
post_delete.connect(_on_custom_field_change, sender=CustomField)


def set_custom_field_defaults(entity: dict, model):
    """Set default values for custom fields in an entity."""
    custom_fields = _get_custom_fields_for_model(model)
    if custom_fields:
        custom_field_data = entity.get('custom_fields')
        if custom_field_data is None:
            custom_field_data = {}
            entity['custom_fields'] = custom_field_data
        for cf in custom_fields:
            if cf.name not in custom_field_data or custom_field_data[cf.name] is None:
                custom_field_data[cf.name] = cf.default

def _set_auto_slugs(entities: list[dict], supported_models: dict):
    for entity in entities:
        model_fields = supported_models.get(entity['_object_type'])
        if model_fields is None:
            raise serializers.ValidationError({
                NON_FIELD_ERRORS: [f"Model for object type {entity['_object_type']} is not supported"]
            })

        for field_name, field_info in model_fields.get('fields', {}).items():
            if field_info["type"] == "SlugField" and entity.get(field_name) is None:
                slug = _generate_slug(entity['_object_type'], entity)
                if slug is not None:
                    # this is provisionally set but will not be used
                    # if the entity is identified by other means...
                    entity['_auto_slug'] = AutoSlug(field_name=field_name, value=slug)

def _generate_slug(object_type, data):
    """Generate a slug for a model instance."""
    source_value = get_primary_value(data, object_type)
    if source_value is not None:
        return slugify(str(source_value))
    return None

def _canonical_uuids(entities: list[dict]) -> dict[str, str]:
    """
    Replay the dedupe's IDENTITY decisions only, merging nothing. uuid -> survivor uuid.

    _vc_identity_partition has to compare two chassis nodes' master references,
    and a reference is only comparable once it is CANONICAL: one device
    mentioned twice in a graph is two nodes with two uuids until dedupe merges
    them, so the real orb-agent shape -- the top-level chassis and every member
    reference carrying the SAME master stub -- would otherwise read as several
    different masters and split a chassis that must stay one. The main loop
    canonicalises as it goes (``_update_unresolved_refs`` before ``fingerprints``),
    which is exactly why the partition cannot be computed from the raw list.

    It replays four of the loop's five identity steps: it rewrites refs,
    computes the same fingerprints from the same incoming node, takes the first
    fingerprint that has been seen, and registers every fingerprint onto the
    survivor. The loop never recomputes a survivor's fingerprints after merging
    (it keys off the INCOMING node's), so not merging payloads here costs no
    accuracy. Post-create nodes are skipped because they neither merge nor
    register.

    The fifth step it does NOT replay is the identity-partition qualifier the
    loop puts on a split chassis node's name fingerprint, and it cannot: the
    partition is what this function is being called to help compute. That costs
    nothing, because the qualifier is only ever applied to dcim.virtualchassis
    nodes while the map returned here is consulted only for the DEVICE uuids a
    master reference points at. Two chassis nodes this replay merges and the
    real loop keeps apart therefore change no answer any caller reads -- but
    a future caller that wants a chassis uuid out of this map does not inherit
    that guarantee, so read it as being about device references.

    It runs on deepcopies: ``_update_dict_refs`` rewrites reference objects in
    place, and the shared UnresolvedReference instances belong to the caller.
    """
    by_fp = {}
    canonical = {}
    for entity in copy.deepcopy(entities):
        if entity.get('_is_post_create'):
            continue
        _update_unresolved_refs(entity, canonical)
        fps = fingerprints(entity, entity['_object_type'])
        uuid = entity['_uuid']
        primary = next((by_fp[fp] for fp in fps if fp in by_fp), uuid)
        if primary != uuid:
            canonical[uuid] = primary
        for fp in fps:
            by_fp[fp] = primary
    return canonical


def _canonical_vc_identity(entity: dict, canonical: dict[str, str]) -> dict:
    """The identity a VC node asserts, with its master reference canonicalised."""
    identity = asserted_vc_identity(entity)
    master = identity.get("master")
    if isinstance(master, UnresolvedReference):
        identity["master"] = UnresolvedReference(
            master.object_type, canonical.get(master.uuid, master.uuid)
        )
    return identity


def _vc_identity_partition(entities: list[dict]) -> dict[str, int]:
    """
    Which same-named dcim.virtualchassis nodes are DIFFERENT chassis. uuid -> group.

    VirtualChassisNameMatcher.fingerprint is keyed on the name alone, and
    deliberately: within one graph a member's name-only chassis node and the
    master-bearing one have to merge into a single create, and gating that
    fingerprint on master leaves two nodes and the split chassis of issue #183.
    The cost of the name-only key, unmeasured until now, is that two same-named
    nodes asserting DIFFERENT identity also merge -- and then _merge_nodes
    rejects the whole entity, so a payload with two same-named stacks in one
    graph (VirtualChassis.master is unique, so different masters prove they are
    two) could never be ingested at all, on any retry.

    So the name bucket keeps its name key and is partitioned INSIDE, by
    matcher.partition_vc_identities. This returns a group index only for buckets
    that actually split; everything else, which is every payload without a
    duplicated chassis name, is untouched and pays for one dict pass.
    """
    buckets: dict[str, list[dict]] = {}
    for entity in entities:
        if entity.get('_object_type') != 'dcim.virtualchassis':
            continue
        if entity.get('_is_post_create'):
            continue
        name = entity.get('name')
        if isinstance(name, str) and name:
            buckets.setdefault(name, []).append(entity)
    buckets = {name: nodes for name, nodes in buckets.items() if len(nodes) > 1}
    if not buckets:
        return {}

    canonical = _canonical_uuids(entities)
    partition = {}
    for nodes in buckets.values():
        identities = [_canonical_vc_identity(node, canonical) for node in nodes]
        groups = partition_vc_identities(identities)
        if len(set(groups)) < 2:
            continue
        for node, group in zip(nodes, groups, strict=True):
            partition[node['_uuid']] = group
    return partition


@profiled("fingerprint_dedupe")
def _fingerprint_dedupe(entities: list[dict]) -> tuple[list[dict], bool]: # noqa: C901
    """
    Deduplicates/merges entities by fingerprint.

    *list must be in topo order by reference already*

    Also returns whether normalizing a merged node released any reference edge, i.e.
    whether the caller's prune sweep has anything to do.
    """
    by_uuid = {}
    by_fp = {}
    deduplicated = []
    new_refs = {} # uuid -> uuid
    refs_released = False
    partition = _vc_identity_partition(entities)

    for entity in entities:
        if entity.get('_is_post_create'):
            # Post-create nodes are never dedupe candidates and must not
            # register fingerprints: reusing the previous entity's fps here
            # (or leaving fps unbound on the first entity) corrupts by_fp.
            fps = []
            existing_uuid = None
        else:
            _update_unresolved_refs(entity, new_refs)
            fps = fingerprints(entity, entity['_object_type'])
            group = partition.get(entity['_uuid'])
            if group is not None:
                # Keep the groups apart, rather than teaching this loop a second,
                # type-aware notion of "is this the same node". Two nodes in one
                # group are qualified identically and merge exactly as before; two
                # in different groups no longer meet.
                #
                # Every key EXCEPT unique_master. That one is a DB unique
                # constraint, so two nodes naming one master are one row whatever
                # their names or groups say and must still meet -- qualifying it
                # separated a partitioned node from every UNPARTITIONED one (the
                # qualifier is bucket-local, assigned only inside a name bucket
                # that actually splits), so two nodes naming ONE master under
                # DIFFERENT names stopped merging and the second create bound the
                # first row. Qualifying ONLY the name key was equally wrong the
                # other way: the whole-payload key ignores private fields, so two
                # nodes differing only in _netbox_id hashed identically and merged
                # straight back together, dropping one addressed row.
                # ...unless this node ADDRESSES a row. The exemption rests on
                # "two nodes naming one master are one row", which is true only
                # while neither says which row it is. Two nodes explicitly
                # addressing DIFFERENT rows have said they are two, so a shared
                # master is contradictory data rather than evidence of sameness
                # -- and merging them dropped one addressed row silently,
                # because _merge_nodes ignores conflicts in private fields, so
                # nothing reported the _netbox_id disagreement. Qualified, they
                # stay apart and the impossible request (one unique master on
                # two rows) is refused by the constraint that owns it.
                keep = (None if entity.get('_netbox_id') is not None
                        else vc_unique_master_fingerprint(entity))
                fps = [fp if keep is not None and fp == keep else (fp, group)
                       for fp in fps]
            for fp in fps:
                existing_uuid = by_fp.get(fp)
                if existing_uuid is not None:
                    break

        if existing_uuid is None:
            new_entity = copy.deepcopy(entity)
            _update_unresolved_refs(new_entity, new_refs)
            primary_uuid = new_entity['_uuid']
            for fp in fps:
                by_fp[fp] = primary_uuid
            by_uuid[primary_uuid] = new_entity
            deduplicated.append(primary_uuid)
        else:
            existing = by_uuid[existing_uuid]
            new_refs[entity['_uuid']] = existing['_uuid']
            refs_before = existing['_refs'] | entity['_refs']
            merged = _merge_nodes(existing, entity)
            # A deferred conflict releases the rejected value's edges, which the caller's
            # prune sweep must hear about too -- not only the drops below.
            if merged['_refs'] != refs_before:
                refs_released = True
            _update_unresolved_refs(merged, new_refs)
            # Normalize NOW, before the next duplicate is compared against this node. A
            # merge is where duplicates each carrying half a contradiction become
            # contradictory, and leaving it there makes the outcome depend on how many
            # duplicates follow. Drops only: the prune is the caller's one sweep.
            refs_released = apply_submitted_driver_field_policy([merged]) or refs_released
            for fp in fps:
                by_fp[fp] = existing_uuid
            by_uuid[existing_uuid] = merged
            deduplicated.append(existing_uuid)

    return [by_uuid[u] for u in deduplicated], refs_released

def _union_private_context(merged: dict, a: dict, b: dict) -> None:
    """
    Union the kept private context keys instead of preferring a's.

    Private keys are otherwise "prefer a's value", which for the member-device
    hint would discard every member but the first. The rule it feeds -- prefer
    the chassis a referencing member already belongs to -- is only as good as the
    evidence it can see, and each merged node brought its own.
    """
    for key in _PRIVATE_CONTEXT_KEYS_KEPT:
        if key not in a and key not in b:
            continue
        union = list(a.get(key) or [])
        for item in (b.get(key) or []):
            if item not in union:
                union.append(item)
        merged[key] = union


def _merge_nodes(a: dict, b: dict) -> dict:
    """
    Merges two nodes.

    If there are any conflicts, an error is raised.
    Ignores conflicts in fields that start with an underscore,
    preferring a's value.
    """
    merged = copy.deepcopy(a)
    merged['_refs'] = a['_refs'] | b['_refs']
    _union_warnings(merged, a, b)

    _union_private_context(merged, a, b)
    deferred = dict(a.get('_deferred_conflicts') or {})
    rejected = []
    for k, v in b.items():
        if k.startswith("_"):
            continue
        if k in merged and merged[k] != v:
            error = _conflict_error(a, k, merged[k], v)
            # Consulted only on an actual conflict: the gate reads the content-type
            # table, and a conflict-free duplicate merge must not depend on it.
            if k not in droppable_dependent_fields(a.get('_object_type') or ''):
                raise serializers.ValidationError(error)
            # A driver value on a duplicate not reached yet can delete this field and
            # settle the disagreement, so raising now would make the outcome depend on
            # the order the duplicates arrive in. See raise_unsettled_conflicts.
            deferred.setdefault(k, error)
            rejected.append(v)
            continue
        merged[k] = v
    if rejected:
        release_rejected_edges(merged, rejected)
    if deferred:
        merged['_deferred_conflicts'] = deferred
    return merged


def _union_warnings(merged: dict, a: dict, b: dict) -> None:
    """Union both nodes' _warnings per field instead of preferring a's."""
    # Underscore keys are otherwise "prefer a's value", which would discard a drop b
    # recorded before dedupe -- and a drop nobody hears about defeats the warning.
    merged_warnings = copy.deepcopy(a.get('_warnings') or {})
    for field, msgs in (b.get('_warnings') or {}).items():
        for msg in msgs:
            if msg not in merged_warnings.setdefault(field, []):
                merged_warnings[field].append(msg)
    if merged_warnings:
        merged['_warnings'] = merged_warnings


def _conflict_error(a: dict, field: str, mine, theirs) -> dict:
    """The error body for two duplicate nodes disagreeing about ``field``."""
    ov = {
        ok: v for ok, v in a.items()
        if ok != field and not ok.startswith("_")
    }
    return {
        NON_FIELD_ERRORS: [
            f"Conflicting values for '{field}' merging duplicate {a.get('_object_type')},"
            f" `{mine}` != `{theirs}` other values : {ov}"]
    }


def raise_unsettled_conflicts(entities: list[dict]) -> None:
    """
    Raise the duplicate-merge conflicts no driver value settled.

    A conflict on a droppable field is held during dedupe; by here the graph is merged
    and normalized, so a field still present was never dropped and the disagreement was
    real after all.
    """
    for entity in entities:
        deferred = entity.pop('_deferred_conflicts', None)
        if not deferred:
            continue
        for field, error in deferred.items():
            if field in entity:
                raise serializers.ValidationError(error)


def _update_unresolved_refs(entity, new_refs):
    if entity.get('_is_post_create'):
        instance_uuid = entity['_instance']
        entity['_instance'] = new_refs.get(instance_uuid, instance_uuid)

    entity['_refs'] = {new_refs.get(r,r) for r in entity['_refs']}
    _update_dict_refs(entity, new_refs)


def _update_dict_refs(data, new_refs):
    for k, v in data.items():
        if isinstance(v, UnresolvedReference) and v.uuid in new_refs:
            v.uuid = new_refs[v.uuid]
        elif isinstance(v, list | tuple):
            for item in v:
                if isinstance(item, UnresolvedReference) and item.uuid in new_refs:
                    item.uuid = new_refs[item.uuid]
                elif isinstance(item, dict):
                    # rewrite refs nested in list-of-dict items too
                    # (e.g. cable termination {object_type, object_id})
                    _update_dict_refs(item, new_refs)
        elif isinstance(v, dict):
            _update_dict_refs(v, new_refs)


@profiled("resolve_refs")
def _mark_seen(data, object_type, existing, seen):
    """Record a resolved (object_type, id) for in-batch dedup, warning on clash."""
    fp = (object_type, existing.id)
    if fp in seen:
        logger.warning(f"objects resolved to the same existing id after deduplication: {seen[fp]} and {data}")
    else:
        seen[fp] = data


def _resolve_by_netbox_id(data, object_type, seen, new_refs, resolved) -> bool:
    """
    Resolve a node via metadata netbox_id (PK lookup).

    Returns True if the node had a netbox_id and was handled (caller should
    skip further resolution); False if no netbox_id was present. Raises if the
    netbox_id does not resolve to an existing object.
    """
    netbox_id = data.pop('_netbox_id', None)
    if netbox_id is None:
        return False
    model_class = get_object_type_model(object_type)
    existing = model_class.objects.filter(pk=netbox_id).first()
    if existing is None:
        raise ChangeSetException(
            f"Object not found for {object_type} with netbox_id={netbox_id}",
            errors={NON_FIELD_ERRORS: [f"No {object_type} found with id {netbox_id}"]}
        )
    new_refs[data['_uuid']] = existing.id
    if object_type in MATCH_ONLY_TYPES:
        # pure reference target: resolved to the existing pk, no change emitted
        return True
    _mark_seen(data, object_type, existing, seen)
    data['id'] = existing.id
    data['_instance'] = existing
    resolved.append(data)
    return True


def _resolve_existing_references(entities: list[dict]) -> list[dict]:
    seen = {}
    new_refs = {}
    resolved = []

    for data in entities:
        object_type = data['_object_type']
        data = copy.deepcopy(data)
        _update_resolved_refs(data, new_refs)

        if data.get('_is_post_create'):
            resolved.append(data)
            continue

        if _resolve_by_netbox_id(data, object_type, seen, new_refs, resolved):
            continue

        existing = find_existing_object(data, object_type)
        if existing is not None:
            new_refs[data['_uuid']] = existing.id
            if object_type in MATCH_ONLY_TYPES:
                # Pure reference target: resolve the parent's reference to the
                # existing pk and emit NO change for this node. Match-only types
                # (users.user) are never created or updated via ingest, and a
                # change for them would fail validation anyway (e.g. NetBox's
                # User requires a password we never carry).
                continue
            _mark_seen(data, object_type, existing, seen)
            data['id'] = existing.id
            data['_instance'] = existing
            resolved.append(data)
        elif object_type in MATCH_ONLY_TYPES:
            primary = get_primary_value(data, object_type)
            raise ChangeSetException(
                f"{object_type} not found for match-only reference",
                errors={object_type: {NON_FIELD_ERRORS: [
                    f"No existing {object_type} matches {primary!r}; this type is "
                    f"resolved against existing objects only and is not created via ingest."
                ]}},
            )
        else:
            data['id'] = UnresolvedReference(object_type, data['_uuid'])
            _update_resolved_refs(data, new_refs)
            resolved.append(data)
    return resolved

def _update_resolved_refs(data, new_refs):
    for k, v in list(data.items()):
        if isinstance(v, UnresolvedReference) and v.uuid in new_refs:
            data[k] = new_refs[v.uuid]
        elif isinstance(v, list | tuple):
            new_items = []
            has_refs = False
            for item in v:
                if isinstance(item, UnresolvedReference) and item.uuid in new_refs:
                    new_items.append(new_refs[item.uuid])
                    has_refs = True
                elif isinstance(item, dict):
                    _update_resolved_refs(item, new_refs)
                    new_items.append(item)
                else:
                    new_items.append(item)
            if has_refs:
                data[k] = sort_ints_first(new_items)
        elif isinstance(v, dict):
            _update_resolved_refs(v, new_refs)

def _cleanup_list_refs(key: str, values, unresolved: set) -> list:
    """Stringify unresolved refs in a list, indexing paths for dict items."""
    items = []
    for i, item in enumerate(values):
        if isinstance(item, UnresolvedReference):
            unresolved.add(key)
            items.append(str(item))
        elif isinstance(item, dict):
            for uu in cleanup_unresolved_references(item):
                unresolved.add(f"{key}.{i}.{uu}")
            items.append(item)
        else:
            items.append(item)
    return items


def cleanup_unresolved_references(data: dict) -> list[str]:
    """Find and stringify unresolved references in fields."""
    unresolved = set()
    for k, v in data.items():
        if isinstance(v, UnresolvedReference):
            if k != 'id':
                unresolved.add(k)
            data[k] = str(v)
        elif isinstance(v, list | tuple):
            data[k] = _cleanup_list_refs(k, v, unresolved)
        elif isinstance(v, dict):
            for uu in cleanup_unresolved_references(v):
                unresolved.add(f"{k}.{uu}")
    return sorted(unresolved)

def _handle_post_creates(entities: list[dict]) -> list[str]:
    """Merges any unnecessary post-create steps for existing objects."""
    by_uuid = {e['_uuid']: (i, e) for i, e in enumerate(entities)}
    out = []
    for entity in entities:
        is_post_create = entity.pop('_is_post_create', False)
        if not is_post_create:
            out.append(entity)
            continue

        instance = entity.get('_instance')
        prior_index, prior_entity = by_uuid[instance]

        # A post-create can only be merged into its object's main change when
        # nothing it references is ordered after that object in the change
        # set. That the referenced objects already exist is not enough: an
        # existing object may itself be updated later in the same change set
        # (e.g. an IP address that only gets assigned to an interface further
        # down), and a merged reference would be applied before that update.
        can_merge = all(
            by_uuid[r][0] <= prior_index
            for r in entity['_refs']
        )

        if can_merge:
            prior_entity.update([x for x in entity.items() if not x[0].startswith('_')])
        else:
            entity['id'] = prior_entity['id']
            # When the object already exists, diff the deferred step against
            # its real state rather than the submitted node data (which never
            # carries the deferred fields), so an already-converged reference
            # NOOPs instead of re-diffing as an update on every ingest.
            prior_instance = prior_entity.get('_instance')
            if prior_instance is not None:
                entity['_instance'] = prior_instance
            out.append(entity)

    return out

def _check_unresolved_refs(entities: list[dict]) -> list[str]:
    seen = set()
    for e in entities:
        seen.add((e['_object_type'], e['_uuid']))
        for k, v in e.items():
            if isinstance(v, UnresolvedReference):
                if (v.object_type, v.uuid) not in seen:
                    raise ChangeSetException(
                        f"Unresolved reference {v} in {e} does not refer to a prior created object (circular reference?)",
                        errors={
                            e['_object_type']: {
                                k: ["unable to resolve reference"],
                            }
                        }
                    )


def _prepare_custom_fields(object_type: str, custom_fields: dict, supported_models: dict) -> tuple[dict, set, list]: # noqa: C901
    """Prepare custom fields for transformation."""
    out = {}
    refs = set()
    nodes = []
    for key, value in custom_fields.items():
        keyname = key
        try:
            value_type, value = _pop_custom_field_type_and_value(value)
            if value_type in ("text", "long_text", "decimal", "boolean", "datetime", "selection", "url", "multiple_selection"):
                out[key] = value
            elif value_type == "date":
                # truncate to YYYY-MM-DD
                try:
                    out[key] = datetime.datetime.fromisoformat(value).strftime("%Y-%m-%d")
                except Exception:
                    out[key] = value
            elif value_type == "integer":
                out[key] = int(value)
            elif value_type == "json":
                out[key] = _prepare_custom_json(value)
            elif value_type == "object":
                nested = _prepare_custom_ref(value, supported_models)
                ref = nested[0]
                refs.add(ref['_uuid'])
                nodes += nested
                out[key] = UnresolvedReference(
                    object_type=ref['_object_type'],
                    uuid=ref['_uuid'],
                )
            elif value_type == "multiple_objects":
                vals = []
                for i, item in enumerate(value):
                    keyname = f"{key}[{i}]"
                    nested = _prepare_custom_ref(item, supported_models)
                    ref = nested[0]
                    refs.add(ref['_uuid'])
                    nodes += nested
                    vals.append(UnresolvedReference(
                        object_type=ref['_object_type'],
                        uuid=ref['_uuid'],
                    ))
                out[key] = vals
            else:
                raise serializers.ValidationError({
                    keyname: [f"Custom field {keyname} has unknown type: {value_type}"]
                })
        except ValueError as e:
            raise ChangeSetException(
                f"Custom field {keyname} is invalid: {value}",
                errors={
                    object_type: {keyname: [str(e)]},
                }
            )
    return out, refs, nodes


def _prepare_custom_json(data: dict) -> dict:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        raise ValueError("failed to parse as JSON")


def _pop_custom_field_type_and_value(data: dict):
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError("custom field value must be a dictionary with a single key")
    value_type, value = data.popitem()
    return value_type, value


def _prepare_custom_ref(data: dict, supported_models: dict) -> list[dict]:
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError("must be a dictionary with a single key")

    field_name, value = data.popitem()
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dictionary")
    ref_info = get_json_ref_info(CUSTOM_FIELD_OBJECT_REFERENCE_TYPE, field_name)
    if ref_info is None:
        raise ValueError(f"{field_name} is not a supported custom field reference type")

    object_type = ref_info.object_type
    return _transform_proto_json_1(value, object_type, supported_models)

def _supported_diode_fields(object_type, supported_models: dict) -> list[str]:
    """
    Get the supported diode fields for a model.

    This excludes fields that are not supported by the current version of NetBox
    that the plugin is installed in. i.e. fields from older or newer versions of
    NetBox that are also supported by the plugin.
    """
    model = supported_models.get(object_type)
    if not model:
        raise serializers.ValidationError({
            NON_FIELD_ERRORS: [f"{object_type} is not supported in this version."]
        })
    model_fields = set(model.get("fields", {}).keys())
    diode_fields = set(legal_fields(object_type))
    return list(model_fields.intersection(diode_fields))
