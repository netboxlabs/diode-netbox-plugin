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
from .matcher import (
    find_existing_object,
    invalidate_find_obj_entry,
    pre_save_match_binds_only,
    requires_pre_save_match,
)
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


def _try_pre_save_match(data: dict, object_type: str, serializer_class, request):
    """
    Resolve a CREATE onto the row it duplicates, or return None to insert.

    Two treatments, chosen by type, and the choice is why this seam exists
    rather than a flag threaded through either of them:

    - BIND-ONLY, for matcher._PRE_SAVE_MATCH_BIND_ONLY: hand back the row and
      write nothing to it. The payload is still validated against that row --
      binding declines the write, not the error. See
      _try_bind_existing_instance.
    - FIND-AND-UPDATE: apply the CREATE's payload to the matched row. This is
      what the auto-created-component path is for -- a component NetBox
      instantiated from a device or module template holds template defaults and
      the ingest payload is the authority that must overwrite them -- and it is
      what every non-bind-only entry in matcher._REQUIRES_PRE_SAVE_MATCH has
      always done. See _try_find_and_update_existing_instance.
    """
    if pre_save_match_binds_only(object_type):
        return _try_bind_existing_instance(data, object_type, serializer_class, request)
    return _try_find_and_update_existing_instance(data, object_type, serializer_class, request)


def _try_bind_existing_instance(data: dict, object_type: str, serializer_class, request):
    """
    Resolve a CREATE onto an existing row WITHOUT applying its payload.

    The bind-only treatment. What the pre-save match exists to prevent is a
    duplicate INSERT (matcher._REQUIRES_PRE_SAVE_MATCH): two planners that
    could not see each other's row each emit CREATE for the same logical row,
    and with no DB unique constraint both inserts succeed. Returning the
    existing row is the whole of that cure -- the change resolves to that row,
    nothing is inserted, and later changes in the same changeset that reference
    it resolve against it through created[ref_id].

    Pushing the CREATE's payload onto the row is a separate act, and for a type
    whose match criteria carry no DB uniqueness it is not a safe one: the row
    may be a different, converged object that merely matches. Refusing the
    WRITE rather than the MATCH is what keeps both properties at once, and it
    is strictly a reduction -- refusing the match instead would insert the
    duplicate row this whole path exists to avoid, and leave it behind forever
    (nothing deletes it and no diff mentions it).

    The payload is still VALIDATED against the matched row, and the save is
    then discarded. Dropping the serializer along with the save was a
    regression against the parent commit rather than a simplification:
    is_valid(raise_exception=True) is the only thing that reports a bad
    payload, so an invalid masterless CREATE that matched an existing row
    answered 200 with errors null while storing nothing -- measured with a
    400-character description against a max_length=200 column, and again with
    an over-length domain, where the parent commit answered 400. Two things
    follow from that misreport: the reconciler is told a change applied when
    nothing was stored, and the change no longer aborts its own changeset, so
    companion changes the parent rolled back are left half-landed. Building the
    serializer exactly as _try_find_and_update_existing_instance does (the
    matched instance, this data, partial=True) and never calling save()
    restores the parent's 400 verbatim at no cost to the no-write guarantee:
    validation reads, it does not write.

    No snapshot and no save, so no changelog entry and no last_updated churn:
    nothing happened to the row. invalidate_find_obj_entry is likewise not
    called -- the cached match is still accurate, precisely because nothing was
    written.

    That guarantee covers the CREATE path, and only it. It is not a promise
    that no changeset can write this row. _apply_change's UPDATE branch that
    resolves through created[ref_id] takes an ordinary serializer.save(), so a
    hand-built changeset pairing a create with a ref_id-only update of the SAME
    object_type (no object_id) writes the update's payload onto the row this
    bind chose. The parent commit does the same -- note the scoping, which is
    exact: for a ref_id-only update of a DIFFERENT object_type the parent and
    this branch also agree, but only because _instance_for_deferred_update
    declines the re-read there; without it the write lands on an unrelated row
    of the update's own type. origin/develop does not do either, because it
    inserts a duplicate and writes to that instead -- so the same-type case is
    a divergence from develop in the destructive direction and it is stated
    rather than implied. Nothing plannable reaches it: dcim.virtualchassis is
    absent from transformer._IS_CIRCULAR_REFERENCE, so generate-diff never
    emits a VC update without an object_id (0 of 960 planned changes across
    three matrix runs). test_bind_only_types_are_not_circular_references pins
    exactly that, so adding the type there cannot turn this gap reachable in
    silence.

    The lookup is _find_existing_object_or_none rather than a bare
    find_existing_object for the same reason its other callers use it: a
    malformed reference in the payload must mean "no match", not a ValueError
    out of query construction that apply_changeset turns into a 500.
    """
    instance = _find_existing_object_or_none(data, object_type)
    if instance is None:
        return None
    # Validate against the matched row, then discard the result. No save(), so
    # no write, no changelog row, no last_updated bump -- only the payload
    # errors the parent commit reported.
    serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    return instance


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


def _coerce_pk(value):
    """
    Normalise a payload reference to an int pk, or None when it is not one.

    A direct POST to apply-change-set (or bulk-apply) skips the transformer
    that resolves references, and ChangeSet.validate pops relation fields
    before it instantiates the model, so whatever the wire put in an FK field
    arrives here untouched: a resolved model instance, an int, a numeric string
    -- or garbage. Garbage must not reach the ORM, because it raises out of
    query construction (ValueError for "abc", TypeError for a list) and
    apply_changeset's handler chain turns neither into a per-entity error, so
    it escapes as a 500 instead of the structured 400 the serializer already
    reports for the offending field. Returning None here declines whatever the
    caller was about to do and leaves the create path -- and that serializer
    error -- to handle it, the same treatment
    _try_find_and_update_existing_instance gives its own lookup with
    `except (ValueError, TypeError)`.

    bool is rejected deliberately: it is an int subclass, so True would
    otherwise mean pk 1 and adopt an unrelated row.
    """
    value = getattr(value, "pk", value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class _MasterAttach(Enum):
    """
    Why an adopted VC's named master is, or is not, a member of it.

    Four outcomes, never fewer: the reasons NOT to attach are semantically
    opposite and each caller branch is a different contract.

    - ATTACHED: the membership the payload implies now exists.
    - IN_OTHER_CHASSIS: the device is real, is a plain MEMBER of another
      chassis, and masters nothing. A VC payload is not authority to relocate
      it, so master is deferred to the device's own payload and the adoption
      otherwise succeeds.
    - MASTERS_OTHER_CHASSIS: the device already MASTERS a different chassis.
      VirtualChassis.master is a DB unique constraint, so that row -- not the
      same-named masterless one this adoption was about to write -- is the row
      the payload identifies. The adoption must decline outright: writing the
      payload onto a masterless same-named decoy applies it to a row the
      payload never referred to (its name matches, its identity is
      contradicted) and leaves the real row untouched. Deferring master and
      saving the rest, the IN_OTHER_CHASSIS treatment, is exactly that
      mis-write, which is why this cannot share that branch.
    - DEVICE_MISSING: the pk resolves to no device at all (planned against a
      device deleted before this apply). Nothing later can converge a dangling
      reference, so it has to surface as a rejected apply, exactly as it did
      before adoption existed. Collapsing this into IN_OTHER_CHASSIS turns that
      hard error into a silent success.
    """

    ATTACHED = "attached"
    IN_OTHER_CHASSIS = "in_other_chassis"
    MASTERS_OTHER_CHASSIS = "masters_other_chassis"
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

    master is dropped for exactly ONE reason: the device is a plain MEMBER of a
    DIFFERENT chassis. That case keeps the deviation visible instead of
    silently relocating a device on the strength of a VC payload; the device's
    own payload owns its membership and a later ingest of THAT does converge
    it. A device that already MASTERS another chassis, and a master pk with no
    device behind it, are different things entirely and must not share that
    handling -- see _MasterAttach.

    Known bound, stated because the blast radius is wider than "bind the row a
    member-first ingest left". VirtualChassis.name carries no unique
    constraint, so the same-named masterless row adopted here may belong to an
    entirely different stack -- another site's, left masterless by its own
    member-first ingest or by this function's own IN_OTHER_CHASSIS branch.
    Adoption then attaches THIS payload's master to THAT stack and drags the
    device into it. That is the intended behaviour and it is what keeps a
    duplicate row out of a table with nothing to dedupe it later, but it is a
    guess about identity rather than a fact, and no subsequent diff reports the
    join. The pre-save match declines to WRITE such a row for exactly this
    reason (matcher._PRE_SAVE_MATCH_BIND_ONLY); adoption cannot decline in the
    same way, because a master-bearing payload has nowhere else to land.
    """
    name = data.get("name")
    master = data.get("master")
    if not isinstance(name, str) or not name or master is None:
        return None
    master_pk = _coerce_pk(master)
    if master_pk is None:
        # Nothing to adopt BY: a master that is not a usable pk cannot pick a
        # candidate row, and must not reach the queryset below. See _coerce_pk.
        return None
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
            case _MasterAttach.MASTERS_OTHER_CHASSIS:
                # Decline the adoption entirely. master is a DB unique key and
                # another chassis already holds this one, so that row -- not
                # this same-named masterless candidate -- is the row the
                # payload identifies. Writing the payload here would apply it
                # to a row the payload never referred to and leave the real one
                # untouched, with nothing to converge it. Returning None hands
                # the change back to the normal create path, whose
                # unique-master recovery (_create_or_find_instance ->
                # _find_existing_object_or_none) resolves it to that row. No
                # save has happened yet, so the snapshot taken above is simply
                # discarded with this instance.
                return None
            case _MasterAttach.DEVICE_MISSING:
                # Deliberately NOT the branch above. master stays in the update
                # so the VC serializer rejects the dangling pk (NetBox's own
                # "Related object not found using the provided numeric ID",
                # reported on field master) and the whole apply rolls back.
                # Dropping it here would report a reference to an object that
                # does not exist as a successfully applied CREATE, and leave the
                # chassis saved-but-masterless with nothing left to re-plan.
                pass
            case unexpected:  # pragma: no cover - exhaustiveness guard
                # The enum documents three outcomes and the branches above cover
                # them. A fourth member added without a branch here would fall
                # straight through this match and silently inherit whatever the
                # surrounding code does next, which is how the collapsed
                # `device is None or ...` guard this enum replaced went wrong.
                raise AssertionError(f"unhandled master-attach outcome: {unexpected}")
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

    Refuses (IN_OTHER_CHASSIS) when the device is a plain member of another
    chassis. Membership is asserted by the DEVICE payload
    (Device.virtual_chassis); a VC payload naming a master is not authority to
    pull a device out of a chassis it is already in.

    Reports separately (MASTERS_OTHER_CHASSIS) when the device already MASTERS
    another chassis. That is not "a membership the payload may not assert" but
    "the payload names a row that already exists": VirtualChassis.master is a
    DB unique constraint, so the chassis holding this master IS the payload's
    row, and NetBox refuses to move a master out of its chassis anyway
    (Device.clean). The caller must decline the adoption rather than defer
    master and write the same-named masterless candidate, which would apply the
    payload to the wrong row -- see _MasterAttach.

    A pk that matches no device is reported separately again (DEVICE_MISSING):
    it is a dangling reference, not a membership the payload may not assert.
    The order of the three checks is therefore missing-device, then
    masters-elsewhere, then member-elsewhere: the unique-master collision is
    decided from the VirtualChassis table, not from Device.virtual_chassis_id,
    so it is caught even for the (anomalous) row whose master is not one of its
    own members.

    The position is provisional: Device.clean requires a member to have one and
    the payload carries none, so this mirrors the position NetBox picks when it
    attaches a new chassis's master (1), stepping past positions the adopted
    row already uses. The device's own payload asserts the real position.
    """
    device_model = get_object_type_model("dcim.device")
    device = device_model.objects.filter(pk=master_pk).first()
    if device is None:
        return _MasterAttach.DEVICE_MISSING
    virtualchassis_model = get_object_type_model("dcim.virtualchassis")
    if virtualchassis_model.objects.filter(master_id=master_pk).exclude(
        pk=virtual_chassis.pk
    ).exists():
        return _MasterAttach.MASTERS_OTHER_CHASSIS
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


# Types whose CREATE needs an apply-time adoption pass that the matcher cannot
# express, keyed by object type so this stays a table rather than a branch --
# the shape _is_auto_created_component and _strip_matched_cable_terminations
# already use in this file, and _NESTED_CONTEXT / _IS_CIRCULAR_REFERENCE use in
# the transformer.
#
# An adopter runs only after the pre-save match has missed, and only for a
# CREATE. It exists for the case where the row to bind can only be chosen from
# live database state -- for dcim.virtualchassis, "the same-named masterless
# chassis, preferring the one this master already belongs to" is a preference
# find_existing_object cannot express, since it returns the first matcher hit
# ordered by pk.
_CREATE_ADOPTERS = {
    "dcim.virtualchassis": _try_adopt_masterless_virtualchassis,
}


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


def _carry_forward_relation_cache(stale, fresh) -> None:
    """
    Re-attach to a re-read row the related objects the stale instance had loaded.

    The deferred-update branch below re-reads its row so the counter machinery
    sees a truthful before-state. A freshly loaded instance starts with an
    empty _state.fields_cache, so every forward FK the serializer's validators
    or the model's own save() touch is fetched again -- for dcim.interface that
    is a full dcim_device row plus, through the _site denormalisation
    Interface.save() inherits, a full dcim_site row. That is two queries per
    deferred update charged to every mac-bearing interface -- measured, 82 -> 80
    on a single interface and 2902 -> 2806 on 48 of them -- on a path shared
    with twelve other (type, field) shapes across six other types
    (transformer._IS_CIRCULAR_REFERENCE), for a counter fix that needs none of
    it.

    The row's OWN column values must come from the database. The rows its FKs
    point AT need not, and did not before this branch re-read anything: the
    CREATE's instance had them attached and the update read them from there.
    Carrying them over restores exactly that. Only _state.fields_cache is
    written, never a model attribute, so the fresh instance's change tracker --
    the whole point of re-reading -- stays empty.

    What the guard covers, stated precisely because it is narrower than it
    looks. `cached.pk == getattr(fresh, field.attname)` compares the FK COLUMN,
    so a column the database has since repointed is never paired with the
    object it used to point to. It does NOT notice the target ROW's own
    contents changing after the stale instance loaded it, and that exposure is
    real rather than theoretical: NetBox's ComponentModel.save() (dcim/models/
    device_components.py, line 129/133/132 on 4.4/4.5/4.6) does
    `self._site = self.device.site`, and `self._location` / `self._rack` from
    the same device, so a carried-forward device whose site moved underneath
    would persist a stale _site onto the component. Note the class: it is
    ComponentModel, the base of EVERY device component, not just the modular
    ones -- ModularComponentModel defines no save() of its own. No
    end-to-end trigger for it could be constructed -- inside a changeset the
    device's own update is planned BEFORE the component create -- so the
    optimisation stands, but it is not a guarantee that a carried object is
    fresh.
    """
    for field in fresh._meta.concrete_fields:
        if not field.is_relation or not field.is_cached(stale):
            continue
        cached = field.get_cached_value(stale)
        if cached is not None and cached.pk == getattr(fresh, field.attname):
            field.set_cached_value(fresh, cached)


def _carry_forward_prechange_snapshot(stale, fresh) -> None:
    """
    Move the CREATE's prechange snapshot onto the re-read instance.

    The changelog record is the third thing the re-read silently changed, and
    the only one that was not intended. ``snapshot_for_apply`` attaches
    ``_prechange_snapshot`` to the instance it is given, and the CREATE path
    calls it whenever a CREATE resolves onto an existing row
    (_try_find_and_update_existing_instance, and the VC adoption). The parent
    commit's deferred UPDATE then saved THAT instance, so NetBox's
    ``to_objectchange`` found the snapshot and recorded prechange_data. A
    freshly read instance has no such attribute, so the same update recorded
    prechange_data null -- and because ``ObjectChange.has_changes`` compares
    prechange with postchange, an update that persists nothing went from being
    dropped to being recorded.

    Measured on the plan-reachable shape, not a hand-built one: a device whose
    device type carries an eth0 InterfaceTemplate, ingested with an interface
    and a primary_mac_address, plans [create device, create interface, create
    macaddress, update interface(ref_id)] and the interface CREATE matches the
    row the device's own save auto-created. The parent commit records that
    deferred update with a 46-field prechange (diff: description,
    primary_mac_address); without this carry-forward it records prechange null.
    The same shape with a payload that persists nothing records no changelog
    row at all on the parent commit and one row without it.

    Copying the attribute is exactly what the parent did, because it is the
    same dict on the same row: no query, and no decision about whether these
    updates OUGHT to carry a prechange -- which is a real question, and a
    separate one this branch deliberately does not answer (see the ref_id
    branch's comment on snapshot_for_apply).
    """
    snapshot = getattr(stale, "_prechange_snapshot", None)
    if snapshot is not None:
        fresh._prechange_snapshot = snapshot


def _instance_for_deferred_update(created_instance, model_class):
    """
    The row a ref_id-only UPDATE should be applied to.

    Normally that is a FRESH READ of the created row: see _apply_change's
    ref_id branch for why the in-memory instance is stale by this point.

    The re-read is a PK lookup, though, and a pk only identifies a row WITHIN a
    type. A hand-built changeset may point a ref_id-only UPDATE at a CREATE of
    a DIFFERENT object_type, and then created[ref_id].pk is a pk from the other
    type's sequence: re-reading it as THIS type lands on whatever unrelated row
    happens to carry that number, and the payload is written onto that
    bystander. Measured -- [create dcim.virtualchassis ref "1", update
    dcim.site ref "1" {description X}] with a Site planted at the pk the new
    chassis takes wrote X onto that Site, where the parent commit wrote it onto
    the chassis the ref actually names.

    Nothing plannable reaches the mismatch: differ.diff_to_change takes ref_id
    from the entity node's own id, so a create and its deferred update are one
    node and therefore one type. Declining the re-read there is not an attempt
    to make a malformed changeset meaningful -- it keeps that case
    byte-identical to the parent commit, so the re-read stays confined to the
    staleness it was added for, which only exists for the type that was
    created.
    """
    if not isinstance(created_instance, model_class):
        return created_instance
    instance = model_class.objects.get(pk=created_instance.pk)
    _carry_forward_relation_cache(created_instance, instance)
    _carry_forward_prechange_snapshot(created_instance, instance)
    return instance


def _find_existing_object_or_none(data: dict, object_type: str):
    """
    find_existing_object, but a malformed reference means "no match".

    Matchers interpolate payload values straight into an ORM filter, and one is
    auto-derived for every unique field, so a unique FK (VirtualChassis.master)
    gets a matcher that filters on whatever the payload carried. A malformed
    value therefore raises ValueError/TypeError out of query construction
    instead of returning nothing -- and apply_changeset does not report either
    as a per-entity error, so it escapes as a 500. There is no object to find
    in that case anyway; the caller's own serializer error is what should
    surface. This is the guard _try_find_and_update_existing_instance already
    puts around its lookup, which is why only that path stayed a clean 400.
    """
    try:
        return find_existing_object(data, object_type)
    except (ValueError, TypeError) as e:
        logger.debug(f"malformed reference in {object_type} match lookup: {e}")
        return None


def _create_or_find_instance(data: dict, object_type: str, serializer_class, request):
    """Create new instance or find existing one on conflict."""
    serializer = serializer_class(data=data, context={"request": request})
    try:
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            return serializer.save()
    except (ValidationError, IntegrityError) as e:
        instance = _find_existing_object_or_none(data, object_type)
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
        # The payload is passed because for some of those types only part of
        # the payload space may take this route -- find-first is an UPDATE of
        # an existing row, and dcim.virtualchassis restricts it to masterless
        # payloads so a CREATE can never rewrite a chassis it did not name
        # (matcher._virtualchassis_pre_save_match_applies). What a matched row
        # is then allowed to receive is a second, separate question, which
        # _try_pre_save_match answers per type.
        instance = None
        if _is_auto_created_component(change.object_type) or requires_pre_save_match(change.object_type, data):
            instance = _try_pre_save_match(data, change.object_type, serializer_class, request)

        if not instance and (adopt := _CREATE_ADOPTERS.get(change.object_type)):
            instance = adopt(data, model_class, serializer_class, request)

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
        elif change.ref_id and (created_instance := created[change.ref_id]):
            # Re-read the row instead of updating the in-memory instance the
            # CREATE returned earlier in this changeset. Between the two,
            # NetBox's own signals write to that row directly -- the natural
            # VirtualChassis shape (create device R, create chassis mastered by
            # R, update device R with its position) has
            # dcim.signals.assign_virtualchassis_master set the device's
            # virtual_chassis_id by a plain save on a DIFFERENT instance -- so
            # the object here is stale by the time it is saved.
            #
            # Stale is not merely "writes an old value back": for any field a
            # utilities.counters CounterCacheField tracks it corrupts a counter.
            # utilities.counters.post_save_receiver decides from
            # TrackingModelMixin's tracker, which records the field's value as
            # of THIS instance's load. On the stale object virtual_chassis_id
            # reads None, so assigning the chassis looks like a first
            # assignment and the receiver increments VirtualChassis.member_count
            # a SECOND time for a membership already counted -- leaving
            # member_count permanently one above the real member count, with no
            # later plan able to notice, because member_count is not a field
            # ingest ever diffs. It fired on the PR's headline natural shape and
            # on the plan-ahead race path alike -- 3 for a two-member chassis,
            # in both apply orders -- so the re-read fixes a drift that two
            # independent shapes could produce, not one.
            #
            # refresh_from_db() would NOT fix it: it assigns through
            # TrackingModelMixin.__setattr__, so the None -> chassis transition
            # it performs is itself recorded in the tracker and the receiver
            # still double-counts. It would not even be cheaper -- it clears
            # every cached relation it finds, exactly as a fresh load has none.
            # A row loaded fresh has an empty tracker, which is also exactly
            # what the object_id branch above operates on.
            #
            # This branch is an ordinary serializer.save(), and it is NOT
            # covered by the bind-only no-write guarantee
            # (_try_bind_existing_instance), which is a property of the CREATE
            # path alone. A hand-built changeset that pairs a create with a
            # ref_id-only update of the same type writes the update's payload
            # onto whatever row the create bound -- including a row the create
            # itself refused to touch. Unreachable from generate-diff today,
            # and test_bind_only_types_are_not_circular_references keeps it
            # that way by failing the moment a bind-only type is added to
            # transformer._IS_CIRCULAR_REFERENCE.
            #
            # No prechange snapshot is taken here, and that is a decision
            # rather than an omission. snapshot_for_apply does not only add
            # prechange_data: because NetBox's ObjectChange.has_changes drops
            # an update whose prechange equals its postchange, populating it
            # also decides whether the row is recorded AT ALL -- measured on
            # the modulebay/installed_module shape, whose deferred write
            # persists nothing, where ['create', 'update'] became ['create'].
            # Whether these deferred updates ought to carry a prechange is a
            # real question and a separate one from this branch's staleness, so
            # the changelog behaviour is left exactly as it was for every
            # shape the differ can plan -- which the re-read alone did NOT do,
            # because the snapshot the CREATE path
            # takes when it resolves onto an existing row lives on the instance
            # the re-read replaces. _carry_forward_prechange_snapshot moves it
            # across; DeferredUpdateChangelogTests measures both halves of what
            # dropping it changed. Not "every shape" full stop: the bind-only
            # route takes no snapshot at all by design, so a HAND-BUILT ref_id
            # update following a bind has no prechange to carry and records one
            # row with prechange null where base recorded two populated ones.
            # dcim.virtualchassis is absent from _IS_CIRCULAR_REFERENCE, so the
            # differ cannot plan that shape (test_bind_only_types_are_not_
            # circular_references pins it), and the row it leaves is correct --
            # it is the audit trail that is thinner, in exchange for not making
            # the destructive write at all.
            # Re-read only when the ref resolves to this change's own type;
            # _instance_for_deferred_update has the cross-type case.
            instance = _instance_for_deferred_update(created_instance, model_class)
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

