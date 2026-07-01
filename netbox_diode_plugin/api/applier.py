#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Applier."""


import copy
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.utils import IntegrityError
from rest_framework.exceptions import ValidationError as ValidationError

from .change_log_buffer import snapshot_for_apply
from .common import NON_FIELD_ERRORS, Change, ChangeSet, ChangeSetException, ChangeSetResult, ChangeType, error_from_validation_error
from .matcher import find_existing_object, invalidate_find_obj_entry, requires_pre_save_match
from .plugin_utils import get_object_type_model, legal_fields
from .profile import profiled
from .supported_models import get_serializer_for_model

logger = logging.getLogger(__name__)


@profiled("apply_changeset")
def apply_changeset(change_set: ChangeSet, request) -> ChangeSetResult:
    """Apply a change set."""
    _validate_change_set(change_set)

    created = {}
    for change in change_set.changes:
        change_type = change.change_type
        object_type = change.object_type

        if change_type == ChangeType.NOOP:
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
            import traceback
            traceback.print_exc()
            logger.error(f"validation raised TypeError error on unspecified field of {object_type}: {data}: {e}")
            logger.error(traceback.format_exc())
            raise _err("invalid data type for field (TypeError)", object_type, "__all__")
        except IntegrityError as e:
            logger.error(f"Integrity error {object_type}: {e} {data}")
            raise _err(f"created a conflict with an existing {object_type}", object_type, "__all__")
        except KeyError as e:
            # A new_refs path referenced an object that was never created
            # (e.g. a dangling or rewritten termination reference). Surface a
            # clean per-entity deviation instead of an uncaught 500 -- the
            # OBS-1080 failure class this feature set out to eliminate.
            logger.error(f"unresolved reference applying {object_type}: {e}")
            raise _err(f"unresolved reference applying {object_type}", object_type, "__all__")

    return ChangeSetResult(
        id=change_set.id,
    )

def _is_auto_created_component(object_type: str) -> bool:
    """Check if the object type is auto-created from templates."""
    auto_created_components = [
        "dcim.consoleport",
        "dcim.consoleserverport",
        "dcim.powerport",
        "dcim.poweroutlet",
        "dcim.interface",
        "dcim.rearport",
        "dcim.frontport",
        "dcim.modulebay",
        "dcim.devicebay",
        "dcim.inventoryitem",
    ]
    return object_type in auto_created_components


def _try_find_and_update_existing_instance(data: dict, object_type: str, serializer_class, request):
    """Try to find existing auto-created instance and update it."""
    try:
        instance = find_existing_object(data, object_type)
        if instance:
            snapshot_for_apply(instance)
            serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            result = serializer.save()
            invalidate_find_obj_entry(object_type, instance.id)
            return result
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not find existing {object_type}: {e}")
    return None


def _create_or_find_instance(data: dict, object_type: str, serializer_class, request):
    """Create new instance or find existing one on conflict."""
    serializer = serializer_class(data=data, context={"request": request})
    try:
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            return serializer.save()
    except (ValidationError, IntegrityError) as e:
        instance = find_existing_object(data, object_type)
        if not instance:
            raise e
        return instance


def _apply_change(data: dict, model_class: models.Model, change: Change, created: dict, request):
    serializer_class = get_serializer_for_model(model_class)
    change_type = change.change_type

    if change_type == ChangeType.CREATE:
        # For component types that may be auto-created from e.g. DeviceType or ModuleType templates,
        # try to find existing object first before attempting to create.
        # This prevents duplicates when components are instantiated during Device/Module save()
        # The same find-first path also handles types whose logical match
        # criteria are not enforced by a DB unique constraint (see
        # matcher._REQUIRES_PRE_SAVE_MATCH): concurrent planners would
        # otherwise each emit CREATE for the same logical row and both
        # inserts would succeed without IntegrityError to fall back on.
        instance = None
        if _is_auto_created_component(change.object_type) or requires_pre_save_match(change.object_type):
            instance = _try_find_and_update_existing_instance(data, change.object_type, serializer_class, request)

        if not instance:
            instance = _create_or_find_instance(data, change.object_type, serializer_class, request)

        # Always add the instance to created dict so it can be referenced by subsequent changes
        if change.ref_id:
            created[change.ref_id] = instance

    elif change_type == ChangeType.UPDATE:
        if object_id := change.object_id:
            instance = model_class.objects.get(id=object_id)
            snapshot_for_apply(instance)
            serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            serializer.save()
            invalidate_find_obj_entry(change.object_type, instance.id)
        # create and update in a same change set
        elif change.ref_id and (instance := created[change.ref_id]):
            serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            serializer.save()
            invalidate_find_obj_entry(change.object_type, instance.id)

def _set_path(data, path, value):
    path = path.split(".")
    key = path.pop(0)
    if key.isdigit():
        key = int(key)
    while len(path) > 0:
        data = data[key]
        key = path.pop(0)
        if key.isdigit():
            key = int(key)
    data[key] = value

def _get_path(data, path):
    path = path.split(".")
    v = data
    for p in path:
        if p.isdigit():
            p = int(p)
        v = v[p]
    return v

def _pre_apply(model_class: models.Model, change: Change, created: dict):
    # `_set_path` mutates in place along the traversed path, and `new_refs`
    # paths may descend into lists of dicts (e.g. "a_terminations.0.object_id").
    # A shallow copy would leave those nested containers shared with
    # `change.data`, so resolving refs here would permanently overwrite the
    # original string refs with resolved pks -- breaking any retry of this
    # same change (e.g. on deadlock) which needs the original refs again.
    # Deep-copy just enough (dicts/lists) to isolate mutations.
    data = copy.deepcopy(change.data)

    # resolve foreign key references to new objects
    for ref_field in change.new_refs:
        v = _get_path(data, ref_field)
        if isinstance(v, list | tuple):
            ref_list = []
            for ref in v:
                if isinstance(ref, str):
                    ref_list.append(created[ref].pk)
                elif isinstance(ref, int):
                    ref_list.append(ref)
            _set_path(data, ref_field, ref_list)
        else:
            if isinstance(v, int):
                # `v` is already a resolved pk. This happens when a termination
                # list is re-sorted (differ) after new_refs paths were computed,
                # moving an already-resolved entry into an index that new_refs
                # still lists. Nothing to resolve.
                continue
            _set_path(data, ref_field, created[v].pk)

    # ignore? fields that are not in the data model (error?)
    allowed_fields = legal_fields(model_class)
    for key in list(data.keys()):
        if key not in allowed_fields:
            if key != "id":
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
        if not isinstance(change.change_type, ChangeType):
            raise _err(f"Unsupported change type '{change.change_type}'", change.object_type, "change_type")

def _err(message, object_name, field):
    if not object_name:
        object_name = "__all__"
    return ChangeSetException(message, errors={object_name: {field: [message]}})

