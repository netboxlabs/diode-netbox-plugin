#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Applier."""


import logging

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from rest_framework.exceptions import ValidationError as ValidationError

from .common import Change, ChangeSet, ChangeSetException, ChangeSetResult, ChangeType
from .plugin_utils import get_object_type_model, legal_fields
from .supported_models import get_serializer_for_model

logger = logging.getLogger(__name__)


def apply_changeset(change_set: ChangeSet) -> ChangeSetResult:
    """Apply a change set."""
    _validate_change_set(change_set)

    created = {}
    for i, change in enumerate(change_set.changes):
        change_type = change.change_type
        object_type = change.object_type

        if change_type == ChangeType.NOOP.value:
            continue

        try:
            model_class = get_object_type_model(object_type)
            data = _pre_apply(model_class, change, created)
            _apply_change(data, model_class, change, created)
        except ValidationError as e:
            raise _err_from_validation_error(e, f"changes[{i}]")
        except ObjectDoesNotExist:
            raise _err(f"{object_type} with id {change.object_id} does not exist", f"changes[{i}].object_id")
        # ConstraintViolationError ?
        # ...

    return ChangeSetResult(
        id=change_set.id,
        success=True,
    )

def _apply_change(data: dict, model_class: models.Model, change: Change, created: dict):
    serializer_class = get_serializer_for_model(model_class)
    change_type = change.change_type
    if change_type == ChangeType.CREATE.value:
        serializer = serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        created[change.ref_id] = instance

    elif change_type == ChangeType.UPDATE.value:
        if object_id := change.object_id:
            instance = model_class.objects.get(id=object_id)
            serializer = serializer_class(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        # create and update in a same change set
        elif change.ref_id and (instance := created[change.ref_id]):
            serializer = serializer_class(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

def _pre_apply(model_class: models.Model, change: Change, created: dict):
    data = change.data.copy()

    # resolve foreign key references to new objects
    for ref_field in change.new_refs:
        if isinstance(data[ref_field], (list, tuple)):
            ref_list = []
            for ref in data[ref_field]:
                if isinstance(ref, str):
                    ref_list.append(created[ref].pk)
                elif isinstance(ref, int):
                    ref_list.append(ref)
            data[ref_field] = ref_list
        else:
            data[ref_field] = created[data[ref_field]].pk

    # ignore? fields that are not in the data model (error?)
    allowed_fields = legal_fields(model_class)
    for key in list(data.keys()):
        if key not in allowed_fields:
            logger.warning(f"Field {key} is not in the diode data model, ignoring.")
            data.pop(key)

    return data

def _validate_change_set(change_set: ChangeSet):
    if not change_set.id:
        raise _err("Change set ID is required", "id")
    if not change_set.changes:
        raise _err("Changes are required", "changes")

    for i, change in enumerate(change_set.changes):
        if change.object_id is None and change.ref_id is None:
            raise _err("Object ID or Ref ID must be provided", f"changes[{i}]", "non_field_errors")
        if change.change_type not in ChangeType:
            raise _err(f"Unsupported change type '{change.change_type}'", f"changes[{i}]", "change_type")

def _err(message, object_name, field):
    return ChangeSetException(message, errors={object_name: {field: [message]}})

def _err_from_validation_error(e, object_name):
    errors = {}
    if e.detail:
        if isinstance(e.detail, dict):
            errors[object_name] = e.detail
        elif isinstance(e.detail, (list, tuple)):
            errors[object_name] = {
                "non_field_errors": e.detail
            }
        else:
            errors[object_name] = {
                "non_field_errors": [e.detail]
            }
    return ChangeSetException("validation error", errors=errors)
