#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Applier."""


import copy
import logging
from enum import Enum

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.utils import IntegrityError
from rest_framework.exceptions import ValidationError as ValidationError

from .change_log_buffer import snapshot_for_apply
from .common import (
    MATCH_ONLY_TYPES,
    NON_FIELD_ERRORS,
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeSetResult,
    ChangeType,
    error_from_validation_error,
)
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
            # Only intercept a missing "new_object:..." reference lookup
            # (a dangling ref that was never created); surface it as a clean
            # per-entity error. Any other KeyError is a real bug — re-raise.
            key = e.args[0] if e.args else None
            if not (isinstance(key, str) and key.startswith("new_object:")):
                raise
            logger.error(f"unresolved reference applying {object_type}: {e}")
            raise _err(f"unresolved reference {key} applying {object_type}", object_type, "__all__")

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
            update_data = _strip_matched_cable_terminations(data, object_type, instance)
            serializer = serializer_class(instance, data=update_data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            result = serializer.save()
            invalidate_find_obj_entry(object_type, instance.id)
            return result
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not find existing {object_type}: {e}")
    return None


class _MasterAttach(Enum):
    """
    Why an adopted VC's named master is, or is not, a member of it.

    Three outcomes, never two: the reasons NOT to attach are semantically
    opposite and each caller branch is a different contract.

    - ATTACHED: the membership the payload implies now exists.
    - IN_OTHER_CHASSIS: the device is real but sits in another chassis. A VC
      payload is not authority to relocate it, so master is deferred to the
      device's own payload and the adoption otherwise succeeds.
    - DEVICE_MISSING: the pk resolves to no device at all (planned against a
      device deleted before this apply). Nothing later can converge a dangling
      reference, so it has to surface as a rejected apply, exactly as it did
      before adoption existed. Collapsing this into IN_OTHER_CHASSIS turns that
      hard error into a silent success.
    """

    ATTACHED = "attached"
    IN_OTHER_CHASSIS = "in_other_chassis"
    DEVICE_MISSING = "device_missing"


def _try_adopt_masterless_virtualchassis(data: dict, model_class, serializer_class, request):
    """
    Adopt a same-named masterless VirtualChassis for a master-bearing CREATE.

    A member-first ingest ordering (or a replaced master) can leave a VC whose
    master is unset; a later master-bearing CREATE must bind that row rather
    than create a same-named duplicate. When several same-named masterless
    rows exist, the one the master device already belongs to is preferred;
    otherwise the oldest is adopted.

    NetBox allows setting VC.master only once that device is a member
    (VirtualChassis.clean), so adoption establishes the membership itself --
    see _attach_master_to_virtualchassis. It must NOT be left to "a later
    ingest": a standalone dcim.virtualchassis payload carries a name and a
    master and nothing else, so no re-ingest of it would ever attach the
    device. Dropping master unconditionally left the row masterless forever
    while every identical re-ingest re-planned the same CREATE -- an ingest
    that never converges. Attaching is also exactly what NetBox does for a
    real VC create (dcim.signals.assign_virtualchassis_master), and adoption
    stands in for that create, so both paths end in the same state.

    master is dropped for exactly ONE reason: the device already belongs to a
    DIFFERENT chassis. That case keeps the deviation visible instead of
    silently relocating a device on the strength of a VC payload; the device's
    own payload owns its membership and a later ingest of THAT does converge
    it. A master pk with no device behind it is a different thing entirely and
    must not share that handling -- see _MasterAttach.
    """
    name = data.get("name")
    master = data.get("master")
    if not isinstance(name, str) or not name or master is None:
        return None
    master_pk = getattr(master, "pk", master)
    candidates = model_class.objects.filter(name=name, master__isnull=True).order_by("pk")
    existing = candidates.filter(members__pk=master_pk).first() or candidates.first()
    if existing is None:
        return None
    update_data = dict(data)
    snapshot_for_apply(existing)
    if not existing.members.filter(pk=master_pk).exists():
        match _attach_master_to_virtualchassis(existing, master_pk, request):
            case _MasterAttach.ATTACHED:
                # The attach bumped VirtualChassis.member_count through a direct
                # UPDATE (utilities.counters), invisible to this instance. Re-read
                # before the serializer saves it, or the full save writes the stale
                # in-memory counter back over the new one.
                existing.refresh_from_db()
            case _MasterAttach.IN_OTHER_CHASSIS:
                # Deliberate defer: the device's own payload owns its membership.
                # Applying the rest of the payload is the intended outcome.
                update_data.pop("master", None)
            case _MasterAttach.DEVICE_MISSING:
                # Deliberately NOT the branch above. master stays in the update
                # so the VC serializer rejects the dangling pk (NetBox's own
                # "Related object not found using the provided numeric ID",
                # reported on field master) and the whole apply rolls back.
                # Dropping it here would report a reference to an object that
                # does not exist as a successfully applied CREATE, and leave the
                # chassis saved-but-masterless with nothing left to re-plan.
                pass
    serializer = serializer_class(existing, data=update_data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    result = serializer.save()
    invalidate_find_obj_entry("dcim.virtualchassis", existing.id)
    return result


def _attach_master_to_virtualchassis(virtual_chassis, master_pk, request) -> _MasterAttach:
    """
    Make an adopted VC's named master a member of it. Reports which case this was.

    The payload names this device as the chassis master and NetBox's data model
    makes membership a precondition of that, so the membership write is implied
    by the payload rather than invented here. It cannot be planned by the
    differ instead: at plan time the master-bearing payload finds no match (the
    name matcher is gated on master absence, the unique_master matcher misses a
    masterless row), so it is planned as a CREATE and the adoption is purely an
    apply-time dedupe decision.

    Refuses (IN_OTHER_CHASSIS) when the device already belongs to another
    chassis. Membership is asserted by the DEVICE payload
    (Device.virtual_chassis); a VC payload naming a master is not authority to
    pull a device out of a chassis it is already in, and when that device is the
    other chassis's master NetBox refuses the move outright (Device.clean).

    A pk that matches no device is reported separately (DEVICE_MISSING) rather
    than as another refusal to attach: it is a dangling reference, not a
    membership the payload may not assert. See _MasterAttach.

    The position is provisional: Device.clean requires a member to have one and
    the payload carries none, so this mirrors the position NetBox picks when it
    attaches a new chassis's master (1), stepping past positions the adopted
    row already uses. The device's own payload asserts the real position.
    """
    device_model = get_object_type_model("dcim.device")
    device = device_model.objects.filter(pk=master_pk).first()
    if device is None:
        return _MasterAttach.DEVICE_MISSING
    if device.virtual_chassis_id is not None:
        return _MasterAttach.IN_OTHER_CHASSIS
    device_serializer_class = get_serializer_for_model(device_model)
    snapshot_for_apply(device)
    serializer = device_serializer_class(
        device,
        data={
            "virtual_chassis": virtual_chassis.pk,
            "vc_position": _lowest_free_vc_position(virtual_chassis),
        },
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    invalidate_find_obj_entry("dcim.device", device.pk)
    return _MasterAttach.ATTACHED


def _lowest_free_vc_position(virtual_chassis) -> int:
    """Lowest vc_position not already used in this chassis, counting from 1."""
    taken = set(
        virtual_chassis.members.exclude(vc_position__isnull=True).values_list("vc_position", flat=True)
    )
    position = 1
    while position in taken:
        position += 1
    return position


def _strip_matched_cable_terminations(data: dict, object_type: str, instance) -> dict:
    """
    Drop termination fields from a pre-save-matched cable's update.

    The cable matcher finds an existing cable by its A/B-insensitive
    termination SET, so the set is identical by construction. Strip the
    terminations ONLY when the submitted A/B grouping equals the existing one
    up to a whole-end swap (identical, within-end reorder, or a pure swap) --
    re-saving those would only churn CableTermination.cable_end (and let a
    stale, end-swapped CREATE toggle it). A genuine repartition (same set,
    different grouping) is passed through so the serializer applies it, keeping
    this path consistent with the differ UPDATE path (which applies it too).
    """
    if object_type != "dcim.cable":
        return data
    if _cable_partition_matches(instance, data):
        return {k: v for k, v in data.items() if k not in ("a_terminations", "b_terminations")}
    return data


def _cable_partition_matches(instance, data: dict) -> bool:
    """True if data's A/B grouping equals the instance's, up to a whole-end swap."""
    def _submitted(field):
        return frozenset(
            (t["object_type"], t["object_id"])
            for t in data.get(field, [])
            if isinstance(t, dict) and isinstance(t.get("object_id"), int)
        )

    def _existing(objs):
        return frozenset(
            (f"{o._meta.app_label}.{o._meta.model_name}", o.pk) for o in objs
        )

    sub_a, sub_b = _submitted("a_terminations"), _submitted("b_terminations")
    exist_a, exist_b = _existing(instance.a_terminations), _existing(instance.b_terminations)
    return (sub_a == exist_a and sub_b == exist_b) or (sub_a == exist_b and sub_b == exist_a)


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

    # Match-only types (e.g. users.user) are resolved against existing rows and
    # never created OR updated via ingest. The transformer guards the plan path;
    # this guards the direct apply-change-set / bulk-apply path, which bypasses
    # the transformer, so a client changeset can't create or rename a user.
    if change.object_type in MATCH_ONLY_TYPES and change_type in (ChangeType.CREATE, ChangeType.UPDATE):
        raise _err(
            f"{change.object_type} is match-only and cannot be created or updated via ingest",
            change.object_type, "__all__",
        )

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

        if not instance and change.object_type == "dcim.virtualchassis":
            instance = _try_adopt_masterless_virtualchassis(data, model_class, serializer_class, request)

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
    keys = path.split(".")
    for key in keys[:-1]:
        data = data[_path_key(data, key)]
    data[_path_key(data, keys[-1])] = value

def _get_path(data, path):
    v = data
    for p in path.split("."):
        v = v[_path_key(v, p)]
    return v

def _path_key(container, key):
    # Coerce an all-digit segment to a list index ONLY when the container is a
    # list (e.g. cable "a_terminations.0.object_id"). Dicts keep string keys so
    # a custom field whose name is all digits still indexes correctly.
    if isinstance(container, list | tuple) and key.isdigit():
        return int(key)
    return key

def _pre_apply(model_class: models.Model, change: Change, created: dict):
    # deep copy: ref resolution mutates nested list/dict containers
    # (e.g. "a_terminations.0.object_id"); a shallow copy would corrupt
    # change.data for retries of this same change.
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
                # already a resolved pk; nothing to resolve
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

