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

        if not hasattr(instance, field_name):
            continue

        value = getattr(instance, field_name)
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

    # Cable.a_terminations / b_terminations are model *properties* (backed by
    # the reverse `terminations` relation), not real Django model fields, so
    # they never appear in `fields.items()` above and are silently omitted
    # from prechange_data. Without this, every re-diff of an already-applied
    # cable spuriously reports an update (postchange always carries the
    # resolved terminations; prechange never does), breaking idempotency.
    # Mirror the {object_type, object_id} shape the transformer produces so
    # shallow_compare_dict's `!=` sees them as equal when nothing changed.
    # Sort by (object_type, object_id) rather than relying on the reverse
    # relation's incidental ordering: for multi-termination cables NetBox
    # gives no ordering guarantee on `terminations`, and postchange data
    # (transformer/apply-resolved) is not guaranteed to line up positionally
    # with whatever order the DB happens to return. A stable sort on both
    # sides keeps list equality in shallow_compare_dict meaningful.
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
        change.data = _tidy(postchange_data)

    return change

def _tidy(data: dict) -> dict:
    return sort_dict_recursively(clean_diff_data(data))

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
            # Cable termination lists carry no meaningful order (they are a set
            # of endpoints, not a sequence), but shallow_compare_dict does a
            # positional list comparison. prechange_data_from_instance() sorts
            # its side by (object_type, object_id); sort postchange the same
            # way here so idempotent re-diffs don't spuriously report a change
            # when the DB's reverse `terminations` relation and the submitted
            # payload happen to order multi-termination endpoints differently.
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

    cleanup_unresolved_references emits index paths (e.g.
    "a_terminations.0.object_id") and _partially_merge later re-sorts these
    lists with the same key for stable prechange/postchange comparison;
    sorting here first makes that re-sort a no-op, so the index paths stay
    aligned with the data the applier resolves. Without this, an UPDATE
    (netbox_id-matched) whose end mixes an already-resolved pk (int) with a
    new unresolved ref re-sorts after the paths are computed, and the
    unresolved ref at its new index is never resolved.
    str(UnresolvedReference) equals the "new_object:..." string cleanup
    writes, so this sort and the post-cleanup sort order identically.
    """
    for term_field in ("a_terminations", "b_terminations"):
        terms = entity.get(term_field)
        if isinstance(terms, list) and terms:
            entity[term_field] = _sorted_termination_refs(terms)


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
