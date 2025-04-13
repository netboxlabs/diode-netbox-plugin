#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Applier."""


import logging

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from rest_framework.exceptions import ValidationError as ValidationError

from .common import NON_FIELD_ERRORS, Change, ChangeSet, ChangeSetException, ChangeSetResult, ChangeType, error_from_validation_error
from .plugin_utils import get_object_type_model, legal_fields
from .supported_models import get_serializer_for_model

logger = logging.getLogger(__name__)


def apply_changeset(change_set: ChangeSet, request) -> ChangeSetResult:
    """Apply a change set."""
    _validate_change_set(change_set)

    created = {}
    for change in change_set.changes:
        change_type = change.change_type
        object_type = change.object_type

        if change_type == ChangeType.NOOP.value:
            continue

        try:
            model_class = get_object_type_model(object_type)
            data = _pre_apply(model_class, change, created)
            _apply_change(data, model_class, change, created, request)
        except ValidationError as e:
            raise error_from_validation_error(e, object_type)
        except ObjectDoesNotExist:
            raise _err(f"{object_type} with id {change.object_id} does not exist", object_type, "object_id")
        except TypeError as e:
            # this indicates a problem in model validation (should raise ValidationError)
            # but raised non-validation error (TypeError) -- we don't know which field trigged it.
            logger.error(f"invalid data type for unspecified field (validation raised non-validation error): {data}: {e}")
            raise _err("invalid data type for field", object_type, "__all__")
        # ConstraintViolationError ?
        # ...

    return ChangeSetResult(
        id=change_set.id,
    )

def _apply_change(data: dict, model_class: models.Model, change: Change, created: dict, request):
    serializer_class = get_serializer_for_model(model_class)
    change_type = change.change_type
    if change_type == ChangeType.CREATE.value:
        serializer = serializer_class(data=data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        created[change.ref_id] = instance

    elif change_type == ChangeType.UPDATE.value:
        if object_id := change.object_id:
            instance = model_class.objects.get(id=object_id)
            serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            serializer.save()
        # create and update in a same change set
        elif change.ref_id and (instance := created[change.ref_id]):
            serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            serializer.save()

def _set_path(data, path, value):
    path = path.split(".")
    key = path.pop(0)
    while len(path) > 0:
        data = data[key]
        key = path.pop(0)
    data[key] = value

def _get_path(data, path):
    path = path.split(".")
    v = data
    for p in path:
        v = v[p]
    return v

def _pre_apply(model_class: models.Model, change: Change, created: dict):
    data = change.data.copy()

    # resolve foreign key references to new objects
    for ref_field in change.new_refs:
        v = _get_path(data, ref_field)
        if isinstance(v, (list, tuple)):
            ref_list = []
            for ref in v:
                if isinstance(ref, str):
                    ref_list.append(created[ref].pk)
                elif isinstance(ref, int):
                    ref_list.append(ref)
            _set_path(data, ref_field, ref_list)
        else:
            _set_path(data, ref_field, created[v].pk)

    # ignore? fields that are not in the data model (error?)
    allowed_fields = legal_fields(model_class)
    for key in list(data.keys()):
        if key not in allowed_fields:
            logger.warning(f"Field {key} is not in the diode data model, ignoring.")
            data.pop(key)

    return data

def _validate_change_set(change_set: ChangeSet):
    if not change_set.id:
        raise _err("Change set ID is required", "changeset","id")
    if not change_set.changes:
        raise _err("Changes are required", "changeset", "changes")

    for change in change_set.changes:
        if change.object_id is None and change.ref_id is None:
            raise _err("Object ID or Ref ID must be provided", change.object_type, NON_FIELD_ERRORS)
        if change.change_type not in ChangeType:
            raise _err(f"Unsupported change type '{change.change_type}'", change.object_type, "change_type")

def _err(message, object_name, field):
    if not object_name:
        object_name = "__all__"
    return ChangeSetException(message, errors={object_name: {field: [message]}})

