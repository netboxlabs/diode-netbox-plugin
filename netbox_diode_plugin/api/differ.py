#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Differ."""

import copy
import datetime
import logging
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
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
from .plugin_utils import get_primary_value, legal_fields
from .supported_models import extract_supported_models
from .transformer import cleanup_unresolved_references, set_custom_field_defaults, transform_proto_json

logger = logging.getLogger(__name__)

def prechange_data_from_instance(instance) -> dict: # noqa: C901
    """Convert model instance data to a dictionary format for comparison."""
    prechange_data = {}

    if instance is None:
        return prechange_data

    model_class = instance.__class__
    object_type = f"{model_class._meta.app_label}.{model_class._meta.model_name}"

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

    if hasattr(instance, "get_custom_fields"):
        custom_field_values = instance.get_custom_fields()
        cfmap = {}
        for cf, value in custom_field_values.items():
            if isinstance(value, datetime.datetime | datetime.date):
                cfmap[cf.name] = value
            else:
                cfmap[cf.name] = cf.serialize(value)
        prechange_data["custom_fields"] = cfmap
    prechange_data = harmonize_formats(prechange_data)

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
        new_refs = cleanup_unresolved_references(entity)
        object_type = entity.pop("_object_type")
        _ = entity.pop("_uuid")
        instance = entity.pop("_instance", None)
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
