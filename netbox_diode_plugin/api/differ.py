#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Differ."""

import contextvars
import copy
import datetime
import logging
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from extras.choices import CustomFieldTypeChoices
from rest_framework import serializers
from utilities.data import shallow_compare_dict

from .common import (
    NON_FIELD_ERRORS,
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeSetResult,
    ChangeType,
    error_from_validation_error,
    harmonize_formats,
    sort_ints_first,
)
from .field_policy import normalize_changeset
from .matcher import _get_active_branch_schema
from .plugin_utils import get_primary_value, legal_fields
from .profile import profiled
from .supported_models import extract_supported_models
from .transformer import _get_custom_fields_for_model, cleanup_unresolved_references, set_custom_field_defaults, transform_proto_json

logger = logging.getLogger(__name__)


_prechange_cache = contextvars.ContextVar("diode_prechange_cache", default=None)


def enter_prechange_cache():
    """Activate a request-scoped prechange data cache."""
    return _prechange_cache.set({})


def exit_prechange_cache(token):
    """Deactivate the request-scoped prechange data cache."""
    _prechange_cache.reset(token)


@profiled("prechange_data")
def prechange_data_from_instance(instance) -> dict: # noqa: C901
    """Convert model instance data to a dictionary format for comparison."""
    prechange_data = {}

    if instance is None:
        return prechange_data

    model_class = instance.__class__
    object_type = f"{model_class._meta.app_label}.{model_class._meta.model_name}"

    cache = _prechange_cache.get(None)
    if cache is not None:
        cache_key = (_get_active_branch_schema(), object_type, instance.pk)
        cached = cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    supported_models = extract_supported_models()
    model = supported_models.get(object_type)
    if not model:
        raise serializers.ValidationError({
            NON_FIELD_ERRORS: [f"{object_type} is not supported in this version."]
        })

    fields = model.get("fields", {})
    if not fields:
        raise serializers.ValidationError({
            NON_FIELD_ERRORS: [f"Model {model_class.__name__} has no fields"]
        })

    diode_fields = legal_fields(model_class)

    for field_name, field_info in fields.items():
        # permit only diode fields and the primary key
        if field_name not in diode_fields and field_name != "id":
            continue

        # aliased wire fields read through their backing model attribute
        source = field_info.get("source", field_name)
        if not hasattr(instance, source):
            continue

        value = getattr(instance, source)
        if hasattr(value, "all"):  # Handle many-to-many and many-to-one relationships
            # For any relationship that has an 'all' method, get all related objects' primary keys
            prechange_data[field_name] = (
                sorted([_pk_or_content_type_ref(item) for item in value.all()] if value is not None else [])
            )
        elif hasattr(value, "pk"):
            # Handle regular related fields (ForeignKey, OneToOne) andContentType fields
            prechange_data[field_name] = _pk_or_content_type_ref(value)
        else:
            prechange_data[field_name] = value

    # Cable terminations are model properties (reverse `terminations`
    # relation), not Django fields, so the loop above omits them and every
    # re-diff of an applied cable would spuriously report an update. Capture
    # them in the transformer's {object_type, object_id} shape, sorted for
    # stable comparison (the relation has no ordering guarantee).
    for term_field in ("a_terminations", "b_terminations"):
        if term_field not in diode_fields or not hasattr(instance, term_field):
            continue
        terminations = getattr(instance, term_field)
        prechange_data[term_field] = _sorted_termination_refs([
            {
                "object_type": f"{term.__class__._meta.app_label}.{term.__class__._meta.model_name}",
                "object_id": term.pk,
            }
            for term in (terminations or [])
        ])

    if hasattr(instance, "get_custom_fields"):
        # NetBox's instance.get_custom_fields() calls CustomField.objects.get_for_model
        # which uses a request-scoped query_cache - one DB hit per unique model per
        # request. For /bulk-plan-apply touching dozens of unique models per batch,
        # that's still 30-50 extras_customfield queries per call. Use the
        # transformer-level lru_cache instead (process-wide, signal-invalidated)
        # to make it once-per-process-per-model. Inlined logic matches NetBox's
        # get_custom_fields() exactly: raw JSON value -> field.deserialize() so
        # callers see datetimes/object instances/etc. rather than primitives.
        cfmap = {}
        for cf in _get_custom_fields_for_model(instance._meta.model):
            value = cf.deserialize(instance.custom_field_data.get(cf.name))
            if isinstance(value, datetime.datetime | datetime.date):
                cfmap[cf.name] = value
            else:
                serialized = cf.serialize(value)
                if isinstance(serialized, list) and cf.type in (
                    CustomFieldTypeChoices.TYPE_MULTIOBJECT,
                    CustomFieldTypeChoices.TYPE_MULTISELECT,
                ):
                    serialized = sort_ints_first(serialized)
                cfmap[cf.name] = serialized
        prechange_data["custom_fields"] = cfmap
    prechange_data = harmonize_formats(prechange_data)

    if cache is not None:
        cache[cache_key] = prechange_data
        return copy.deepcopy(prechange_data)

    return prechange_data

def _pk_or_content_type_ref(value):
    if isinstance(value, ContentType):
        return f"{value.app_label}.{value.model}"
    # For regular related fields, get the primary key
    return  value.pk if value is not None else None

# CREATE data drops None values (the serializer supplies defaults), which
# erases the distinction between "field not submitted" and "field
# explicitly null". For the types below, apply-path semantics depend on
# that distinction for the named keys -- the rack pre-save gate and the
# located-rack adopter must be able to tell an asserted null location from
# an absent one -- so an explicitly-submitted null survives into CREATE
# change data.
_CREATE_PRESERVED_NULL_KEYS = {
    "dcim.rack": ("location",),
}


def clean_diff_data(data: dict, exclude_empty_values: bool = True) -> dict:
    """Clean diff data by removing null values."""
    result = {}
    for k, v in data.items():
        if exclude_empty_values:
            if v is None:
                continue
            if isinstance(v, list) and len(v) == 0:
                continue
            if isinstance(v, dict):
                if len(v) == 0:
                    continue
                v = clean_diff_data(v, exclude_empty_values)
            if isinstance(v, str) and v == "":
                continue
        result[k] = v
    return result


def diff_to_change(
    object_type: str,
    prechange_data: dict,
    postchange_data: dict,
    changed_attrs: list[str],
    unresolved_references: list[str],
) -> Change:
    """Convert a diff to a change."""
    change_type = ChangeType.UPDATE if len(prechange_data) > 0 else ChangeType.CREATE
    if change_type == ChangeType.UPDATE and not len(changed_attrs) > 0:
        change_type = ChangeType.NOOP
    primary_value = str(get_primary_value(prechange_data | postchange_data, object_type))
    if primary_value is None:
        primary_value = "(unnamed)"

    prior_id = prechange_data.get("id")
    ref_id = None
    if prior_id is None:
        ref_id = postchange_data.pop("id", None)

    # For updates, preserve explicitly-provided empty values (empty strings, None)
    # so that the apply changeset endpoint can clear fields the user intended to reset.
    # For creates, strip empty values to avoid sending noise — the serializer handles defaults.
    preserve_empty = change_type == ChangeType.UPDATE

    change = Change(
        change_type=change_type,
        before=_tidy(prechange_data),
        data={},
        object_type=object_type,
        object_id=prior_id if isinstance(prior_id, int) else None,
        ref_id=ref_id,
        object_primary_value=primary_value,
        new_refs=unresolved_references,
    )

    if change_type != ChangeType.NOOP:
        change.data = _tidy(postchange_data, exclude_empty_values=not preserve_empty)
        if change_type == ChangeType.CREATE:
            for key in _CREATE_PRESERVED_NULL_KEYS.get(object_type, ()):
                if key in postchange_data and postchange_data[key] is None:
                    change.data[key] = None
            change.data = sort_dict_recursively(change.data)

    return change

def _tidy(data: dict, exclude_empty_values: bool = True) -> dict:
    return sort_dict_recursively(clean_diff_data(data, exclude_empty_values=exclude_empty_values))

def sort_dict_recursively(d):
    """Recursively sorts a dictionary by keys."""
    if isinstance(d, dict):
        return {k: sort_dict_recursively(v) for k, v in sorted(d.items())}
    if isinstance(d, list):
        return [sort_dict_recursively(item) for item in d]
    return d

def generate_changeset(entity: dict, object_type: str) -> ChangeSetResult:
    """Generate a changeset for an entity."""
    try:
        return _generate_changeset(entity, object_type)
    except ChangeSetException:
        raise
    except serializers.ValidationError as e:
        raise error_from_validation_error(e, object_type)
    except Exception as e:
        logger.error(f"Unexpected error generating changeset: {e}")
        raise

@profiled("generate_changeset")
def _generate_changeset(entity: dict, object_type: str) -> ChangeSetResult:
    """Generate a changeset for an entity."""
    change_set = ChangeSet()

    warnings = {}
    supported_models = extract_supported_models()
    entities = transform_proto_json(entity, object_type, supported_models)
    by_uuid = {x['_uuid']: x for x in entities}
    for entity in entities:
        prechange_data = {}
        changed_attrs = []
        _canonicalize_termination_order(entity)
        new_refs = cleanup_unresolved_references(entity)
        object_type = entity.pop("_object_type")
        _ = entity.pop("_uuid")
        instance = entity.pop("_instance", None)
        entity.pop("_netbox_id", None)
        _merge_warnings(warnings, object_type, entity.pop("_warnings", None))
        if instance:
            # the prior state is another new object...
            if isinstance(instance, str):
                prechange_data = copy.deepcopy(by_uuid[instance])
            # prior state is a model instance
            else:
                prechange_data = prechange_data_from_instance(instance)
                # merge the prior state that we don't want to overwrite with the new state
                # this is also important for custom fields because they do not appear to
                # respsect paritial update serialization.
                entity = _partially_merge(prechange_data, entity, instance)
                _align_cable_ends(prechange_data, entity)
                _apply_merge_semantics(object_type, prechange_data, entity)
                normalize_changeset(object_type, prechange_data, entity)
            changed_data = shallow_compare_dict(
                prechange_data, entity,
            )
            changed_attrs = sorted(changed_data.keys())
        change = diff_to_change(
            object_type,
            prechange_data,
            entity,
            changed_attrs,
            new_refs,
        )

        change_set.changes.append(change)

    has_any_changes = False
    for change in change_set.changes:
        if change.change_type != ChangeType.NOOP:
            has_any_changes = True
            break

    if not has_any_changes:
        change_set.changes = []
    if errors := change_set.validate():
        raise ChangeSetException("Invalid change set", errors)

    if warnings:
        change_set.warnings = warnings

    cs = ChangeSetResult(
        id=change_set.id,
        change_set=change_set,
    )
    return cs

def _partially_merge(prechange_data: dict, postchange_data: dict, instance) -> dict:
    """Merge lists and custom_fields rather than replacing the full value..."""
    result = {}
    for key, value in postchange_data.items():
        # currently we only merge tags, but this could be extended to other reference lists?
        if key == "tags":
            result[key] = _merge_reference_list(prechange_data.get(key, []), value)
        elif key in ("a_terminations", "b_terminations") and isinstance(value, list):
            # termination lists are sets of endpoints; sort both sides the
            # same way so positional list comparison stays meaningful
            result[key] = _sorted_termination_refs(value)
        else:
            result[key] = value

    # these are fully merged in from the prechange state because
    # they don't respect partial update serialization.
    if "custom_fields" in postchange_data:
        for key, value in prechange_data.get("custom_fields", {}).items():
            if value is not None and key not in postchange_data["custom_fields"]:
                result["custom_fields"][key] = value
        set_custom_field_defaults(result, instance)
    return result

def _canonicalize_termination_order(entity: dict) -> None:
    """
    Sort termination lists in place before new_refs index paths are computed.

    _partially_merge later re-sorts these lists with the same key; sorting
    first makes that a no-op, so index paths like "a_terminations.0.object_id"
    stay aligned with the data the applier resolves (a later re-sort would
    strand an unresolved ref at an index no path names).
    """
    for term_field in ("a_terminations", "b_terminations"):
        terms = entity.get(term_field)
        if isinstance(terms, list) and terms:
            entity[term_field] = _sorted_termination_refs(terms)


# Wire fields whose NetBox serializer merges a non-empty update payload into
# the stored value instead of replacing it (AttributesField). The planned
# postchange must predict that merge, or a payload omitting stored keys keeps
# re-diffing as the same UPDATE forever.
_MERGE_SEMANTICS_FIELDS = {
    "dcim.moduletype": ("attributes",),
}


def _apply_merge_semantics(object_type: str, prechange_data: dict, entity: dict) -> None:
    """
    Pre-merge stored values into submitted ones for merge-semantics fields.

    Mirrors AttributesField.to_internal_value: a non-empty dict submitted on
    an update is merged over the stored dict; an empty payload is left alone
    because the serializer applies it as a replacement (clear).
    """
    for field_name in _MERGE_SEMANTICS_FIELDS.get(object_type, ()):
        submitted = entity.get(field_name)
        if not submitted or not isinstance(submitted, dict):
            continue
        stored = prechange_data.get(field_name)
        if isinstance(stored, dict) and stored:
            entity[field_name] = {**stored, **submitted}


def _align_cable_ends(prechange_data: dict, entity: dict) -> None:
    """
    Keep the existing A/B end assignment when the submitted ends are swapped.

    Cable identity is A/B-insensitive, so a feed that reports the same two
    termination sets with the ends flipped is the same cable; without this,
    each such re-ingest diffs as an UPDATE that only flips cable_end, and
    alternating feeds toggle forever instead of converging. Only a pure swap
    is realigned (both submitted ends exactly equal the opposite existing
    ends), which also guarantees all refs are resolved pks.
    """
    pre_a = prechange_data.get("a_terminations")
    pre_b = prechange_data.get("b_terminations")
    post_a = entity.get("a_terminations")
    post_b = entity.get("b_terminations")
    if not (pre_a and pre_b and post_a and post_b):
        return
    if post_a != pre_a and post_a == pre_b and post_b == pre_a:
        entity["a_terminations"] = post_b
        entity["b_terminations"] = post_a


def _sorted_termination_refs(refs: list) -> list:
    """
    Sort a list of {object_type, object_id} termination refs for stable comparison.

    object_id may be an int (resolved) or a string (still-unresolved
    `new_object:...` reference after cleanup_unresolved_references), so the
    sort key coerces it to str to avoid cross-type comparison errors.
    """
    return sorted(
        refs,
        key=lambda t: (t.get("object_type", ""), str(t.get("object_id", "")))
        if isinstance(t, dict)
        else (str(t),),
    )


def _merge_reference_list(prechange_list: list, postchange_list: list) -> list:
    """Merge reference lists rather than replacing the full value."""
    result = set(prechange_list)
    result.update(postchange_list)
    return sort_ints_first(result)

def _merge_warnings(warnings: dict, object_type: str, entity_warnings: dict):
    """Merge warnings for an object type."""
    if not entity_warnings:
        return

    if object_type not in warnings:
        warnings[object_type] = defaultdict(list)
    for key, value in entity_warnings.items():
        warnings[object_type][key] += value
