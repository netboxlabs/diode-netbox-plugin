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

from django.utils.text import slugify
from extras.models.customfields import CustomField
from rest_framework import serializers

from .common import NON_FIELD_ERRORS, AutoSlug, ChangeSetException, UnresolvedReference, harmonize_formats, sort_ints_first
from .compat import apply_entity_migrations
from .matcher import find_existing_object, fingerprints
from .plugin_utils import (
    CUSTOM_FIELD_OBJECT_REFERENCE_TYPE,
    apply_format_transformations,
    get_json_ref_info,
    get_primary_value,
    legal_fields,
)

logger = logging.getLogger("netbox.diode_data")

@lru_cache(maxsize=128)
def _camel_to_snake_case(name):
    """Convert camelCase string to snake_case."""
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()

# these are implied values pushed down to referenced objects.
_NESTED_CONTEXT = {
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
    "dcim.device": frozenset(["primary_ip4", "primary_ip6", "oob_ip"]),
    "dcim.virtualdevicecontext": frozenset(["primary_ip4", "primary_ip6"]),
    "virtualization.virtualmachine": frozenset(["primary_ip4", "primary_ip6"]),
    "circuits.provider": frozenset(["accounts"]),
    "dcim.modulebay": frozenset(["module"]), # this isn't  allowed to be circular, but gives a better error
}

def _is_circular_reference(object_type, field_name):
    return field_name in _IS_CIRCULAR_REFERENCE.get(object_type, frozenset())

def transform_proto_json(proto_json: dict, object_type: str, supported_models: dict) -> list[dict]: # noqa: C901
    """
    Transform keys of proto json dict to flattened dictionaries with model field keys.

    This also handles placing `_type` fields for generic references,
    a certain form of deduplication and resolution of existing objects.
    """
    entities = _transform_proto_json_1(proto_json, object_type, supported_models)

    entities = _topo_sort(entities)
    deduplicated = _fingerprint_dedupe(entities)
    deduplicated = _topo_sort(deduplicated)
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

    return output

def _transform_proto_json_1(proto_json: dict, object_type: str, supported_models: dict, context=None) -> list[dict]: # noqa: C901
    uuid = str(uuid4())
    node = {
        "_object_type": object_type,
        "_uuid": uuid,
        "_refs": set(),
        "_warnings": {},
    }

    # handle camelCase protoJSON if provided...
    proto_json = _ensure_snake_case(proto_json, object_type)
    apply_format_transformations(proto_json, object_type)
    apply_entity_migrations(proto_json, object_type)

    # context pushed down from parent nodes
    if context is not None:
        for k, v in context.items():
            if not k.startswith("_"):
                node[k] = v
            if isinstance(v, UnresolvedReference):
                node['_refs'].add(v.uuid)

    nodes = [node]
    post_create = None

    # special handling for custom fields
    custom_fields = dict.pop(proto_json, "custom_fields", {})
    if custom_fields:
        custom_fields, custom_fields_refs, nested = _prepare_custom_fields(object_type, custom_fields, supported_models)
        node['custom_fields'] = custom_fields
        node['_refs'].update(custom_fields_refs)
        nodes += nested

    supported_fields = _supported_diode_fields(object_type, supported_models)
    def is_supported(field_name, ref_info):
        if ref_info is None:
            return field_name in supported_fields
        if ref_info.object_type not in supported_models:
            return False
        if ref_info.is_generic:
            return ref_info.field_name + "_type" in supported_fields
        return ref_info.field_name in supported_fields

    for key, value in proto_json.items():
        ref_info = get_json_ref_info(object_type, key)
        if not is_supported(key, ref_info):
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

        refs = []
        ref_value = None
        if isinstance(value, list):
            ref_value = []
            for item in value:
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
    """Topologically sort entities by reference."""
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

def set_custom_field_defaults(entity: dict, model):
    """Set default values for custom fields in an entity."""
    custom_fields = CustomField.objects.get_for_model(model)
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

def _fingerprint_dedupe(entities: list[dict]) -> list[dict]: # noqa: C901
    """
    Deduplicates/merges entities by fingerprint.

    *list must be in topo order by reference already*
    """
    by_uuid = {}
    by_fp = {}
    deduplicated = []
    new_refs = {} # uuid -> uuid

    for entity in entities:
        if entity.get('_is_post_create'):
            fp = entity['_uuid']
            existing_uuid = None
        else:
            _update_unresolved_refs(entity, new_refs)
            fps = fingerprints(entity, entity['_object_type'])
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
            merged = _merge_nodes(existing, entity)
            _update_unresolved_refs(merged, new_refs)
            for fp in fps:
                by_fp[fp] = existing_uuid
            by_uuid[existing_uuid] = merged
            deduplicated.append(existing_uuid)

    return [by_uuid[u] for u in deduplicated]

def _merge_nodes(a: dict, b: dict) -> dict:
    """
    Merges two nodes.

    If there are any conflicts, an error is raised.
    Ignores conflicts in fields that start with an underscore,
    preferring a's value.
    """
    merged = copy.deepcopy(a)
    merged['_refs'] = a['_refs'] | b['_refs']

    for k, v in b.items():
        if k.startswith("_"):
            continue
        if k in merged and merged[k] != v:
            ov = {
                ok: v for ok, v in a.items()
                if ok != k and not ok.startswith("_")
            }
            raise serializers.ValidationError({
                NON_FIELD_ERRORS: [
                    f"Conflicting values for '{k}' merging duplicate {a.get('_object_type')},"
                    f" `{merged[k]}` != `{v}` other values : {ov}"]
            })
        merged[k] = v
    return merged


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
        elif isinstance(v, dict):
            _update_dict_refs(v, new_refs)


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

        existing = find_existing_object(data, object_type)
        if existing is not None:
            fp = (object_type, existing.id)
            if fp in seen:
                logger.warning(f"objects resolved to the same existing id after deduplication: {seen[fp]} and {data}")
            else:
                seen[fp] = data
            data['id'] = existing.id
            data['_instance'] = existing
            new_refs[data['_uuid']] = existing.id
            resolved.append(data)
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
                else:
                    new_items.append(item)
            if has_refs:
                data[k] = sort_ints_first(new_items)
        elif isinstance(v, dict):
            _update_resolved_refs(v, new_refs)

def cleanup_unresolved_references(data: dict) -> list[str]:
    """Find and stringify unresolved references in fields."""
    unresolved = set()
    for k, v in data.items():
        if isinstance(v, UnresolvedReference):
            if k != 'id':
                unresolved.add(k)
            data[k] = str(v)
        elif isinstance(v, list | tuple):
            items = []
            for item in v:
                if isinstance(item, UnresolvedReference):
                    unresolved.add(k)
                    items.append(str(item))
                else:
                    items.append(item)
            data[k] = items
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

        # a post create can be merged whenever the entities it relies on
        # already exist (were resolved) or there are no dependencies between
        # the object being updated and the post-create.
        can_merge = all(
            by_uuid[r][1].get('_instance') is not None
            for r in entity['_refs']
        ) or sorted(by_uuid[r][0] for r in entity['_refs'])[-1] == prior_index

        if can_merge:
            prior_entity.update([x for x in entity.items() if not x[0].startswith('_')])
        else:
            entity['id'] = prior_entity['id']
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
