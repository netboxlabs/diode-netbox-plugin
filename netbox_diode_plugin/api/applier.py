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
    annotate_vc_member_counts,
    contradicting_vc_discriminator,
    find_existing_object,
    invalidate_find_obj_entry,
    narrow_vc_candidates,
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
    warnings: list[dict] = []
    for change in change_set.changes:
        change_type = change.change_type
        object_type = change.object_type

        if change_type == ChangeType.NOOP:
            continue

        try:
            model_class = get_object_type_model(object_type)
            data = _pre_apply(model_class, change, created)
            _apply_change(data, model_class, change, created, request, change_set, warnings)
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
        warnings=warnings or None,
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


def _try_pre_save_match(data: dict, object_type: str, serializer_class, request,
                        warnings: list | None = None):
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
        return _try_bind_existing_instance(
            data, object_type, serializer_class, request, warnings)
    return _try_find_and_update_existing_instance(data, object_type, serializer_class, request)


def _try_bind_existing_instance(data: dict, object_type: str, serializer_class, request,
                               warnings: list | None = None):
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
    # Snapshot BEFORE validating. Validation mutates the in-memory instance --
    # measured: after is_valid the row object already carries the submitted
    # description -- so comparing against it afterwards reports nothing dropped.
    # Nothing is written either way; only this object's attributes change.
    before = _bind_snapshot(instance, data) if warnings is not None else None
    serializer = serializer_class(instance, data=data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    if warnings is not None:
        _warn_bind_discarded_fields(warnings, instance, object_type, serializer, before)
    return instance


# A value that could not be read, so it compares equal to nothing and its field
# is reported as dropped. Under-reporting a lost write is the failure that
# matters; over-reporting one is noise.
_BIND_UNREADABLE = object()


def _bind_comparable(value):
    """Reduce a field value to something two sides can be compared by."""
    if hasattr(value, "all"):
        return {obj.pk for obj in value.all()}
    if isinstance(value, (list, tuple, set)):
        return {getattr(item, "pk", item) for item in value}
    return getattr(value, "pk", value)


def _bind_snapshot(instance, data: dict) -> dict:
    """The row's current values for the fields this payload submits."""
    snapshot = {}
    for name in data:
        if name.startswith("_"):
            continue
        try:
            snapshot[name] = _bind_comparable(getattr(instance, name))
        except Exception:
            snapshot[name] = _BIND_UNREADABLE
    return snapshot


def _warn_bind_discarded_fields(warnings: list, instance, object_type: str, serializer,
                                before: dict) -> None:
    """
    Say that a bind happened and which submitted values it did not store.

    A bind answers 200 having deliberately written nothing, so without this the
    caller is told its change applied when its payload was discarded. A producer
    that replays its state converges on the next pass, through the object-id
    UPDATE the row's existence now makes plannable; a one-shot or push-on-change
    producer never sends that pass and would otherwise never learn.

    Only fields whose submitted value differs from what the row held are named --
    the ones actually dropped -- against a snapshot taken before validation,
    because validating mutates the in-memory instance. A field that could not be
    read is named rather than skipped.
    """
    dropped = []
    for name, value in (serializer.validated_data or {}).items():
        try:
            submitted = _bind_comparable(value)
        except Exception:
            dropped.append(name)
            continue
        if before.get(name, _BIND_UNREADABLE) != submitted:
            dropped.append(name)
    dropped.sort()
    if not dropped:
        return
    warnings.append({
        "object_type": object_type,
        "object_id": instance.pk,
        "fields": dropped,
        "message": (
            f"Bound this create to the existing {object_type} with id {instance.pk} "
            f"instead of inserting a duplicate, but did not apply "
            f"{', '.join(dropped)}: the match is not authoritative, because the "
            f"criteria it used carry no database uniqueness, so the row may be a "
            f"different object that merely matches. Re-plan this entity to apply "
            f"them through an update addressed by object_id."
        ),
    })


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
      it, so the request CONFLICTS and is reported as such. It used to drop
      master and apply the rest -- a partial interpretation returned as
      success. The rationale was that the device's own payload owns the
      membership move, which is true, and it is why this is not a relocation;
      but it is not a reason to answer 200. A standalone VirtualChassis
      payload carries a name and a master and nothing else, so if no device
      payload ever arrives, every identical re-ingest re-plans the same CREATE
      against a row that stays masterless -- measured over three plan+apply
      cycles, each 200 with errors null, master never set. Reporting the
      conflict is what makes the deviation visible to the producer instead of
      leaving it to be inferred from a plan that never empties.

      SCOPE, because the sentence above used to overstate it: this is what
      ADOPTION does, and adoption is only reached when a same-named masterless
      row was chosen. Where no row is adopted -- no candidate at all, or a
      candidate whose identity is not strong enough to adopt -- the ordinary
      create path inserts the payload's own chassis and NetBox's own
      dcim.signals.assign_virtualchassis_master then moves the named master into
      it, out of whatever chassis it was in, and answers 200. Enumerated on
      v4.5.5 and v4.4.10 over the 1824 cells of
      VirtualChassisAdoptionMatrixTests: 32 cells report this conflict and 288
      relocate through the create path (336 cells relocate in total, the other
      48 through an adoption that was licensed to move the master). That is
      develop's behaviour too (08af3fb relocates in every one of those cells,
      measured), so it is not a regression and it is deliberately not "fixed"
      here -- refusing on the create path would refuse payloads develop
      accepts. It IS the reason this enum member does not claim more than
      "adoption will not relocate a device THIS CHANGESET HAS NOT ALREADY
      ASKED TO MOVE" -- the 48 adopting cells counted above are exactly the
      ones where _changeset_plans_membership licensed the move, and an
      unqualified "adoption will not relocate a device" would be false for
      every one of them.
    - MASTERS_OTHER_CHASSIS: the device already MASTERS a different chassis.
      VirtualChassis.master is a DB unique constraint, so that row -- not the
      same-named masterless one this adoption was about to write -- is the row
      the payload identifies. The adoption must decline outright and hand the
      change back to the create path, which resolves it onto that row: writing
      the payload onto a masterless same-named decoy applies it to a row the
      payload never referred to (its name matches, its identity is
      contradicted) and leaves the real row untouched. It stays separate from
      IN_OTHER_CHASSIS even now that both refuse the write, because the two
      refusals differ in outcome: this one is a DEFERRAL TO ANOTHER PATH that
      still applies the payload to the right row, while IN_OTHER_CHASSIS has no
      right row to fall back to and must be reported.
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


def _changeset_plans_membership(change: Change, change_set: ChangeSet, created: dict, device_pk: int) -> bool:
    """
    Does THIS changeset already plan making ``device_pk`` a member of this chassis?

    Evidence that the producer asked for the membership at all, and nothing
    more than that. Read the scope exactly, because an earlier revision of this
    docstring (and of the commit message that introduced it) overstated it:

    - what the device change asserts is ``virtual_chassis = <this CREATE's
      ref>`` -- the chassis this change is about to CREATE. It is a real
      assertion, in the preview, made by the object that owns its own
      membership.
    - what it does NOT assert is membership of the pre-existing row that
      adoption may redirect that create onto. Nothing in a device payload names
      a row by id. So "the changeset plans the membership" answers the review's
      objection ("the mutation is not represented as a Device change in the
      planned changeset") for the row the PREVIEW names, and only for that row.

    So this predicate separates two SHAPES of payload rather than two rows. It
    is true for a device payload nesting virtual_chassis -- the member-first
    shape, which plans exactly the pair "create chassis mastered by R, then
    update R with its chassis and position" -- and false for a STANDALONE
    dcim.virtualchassis payload, which plans no device change at all. That is
    the line _choose_adoption_candidate's rule 4 draws, and its limit is stated
    there: within the member-first shape it cannot tell this producer's own
    earlier pass from another producer's identically named stack, because the
    difference is a fact about the pre-existing row and the change names only
    the planned one.

    Matching is by ref string, not by pk, because the chassis does not have one
    yet: differ.diff_to_change puts the create's stringified UnresolvedReference
    in ref_id and the referencing device change carries the identical string in
    its own virtual_chassis field. The device side is matched by pk either way:
    object_id for a device that already exists, created[ref_id] for one this
    changeset just made (already applied -- it is ordered before the chassis,
    which is why its instance is in ``created``).
    """
    ref = change.ref_id
    if not ref:
        return False
    for other in change_set.changes:
        if other.object_type != "dcim.device":
            continue
        if other.change_type not in (ChangeType.CREATE, ChangeType.UPDATE):
            # Only a change that will actually WRITE the membership authorizes
            # the move. apply_changeset skips NOOP outright, so a NOOP device
            # change asserting this chassis promises a membership that never
            # lands -- and the licence it bought moved the device anyway.
            # Measured on a hand-built changeset (reachable through
            # apply-change-set and bulk-apply, not through generate-diff): a
            # device sitting in another producer's chassis was relocated into
            # the adopted row and made its master, 200, errors null, with the
            # IN_OTHER_CHASSIS conflict that a standalone payload gets bypassed
            # entirely. Nothing legitimate is lost by this: a device that
            # really is already a member never reaches
            # _attach_master_to_virtualchassis, because the caller checks
            # existing.members first.
            continue
        if (other.data or {}).get("virtual_chassis") != ref:
            continue
        if other.object_id is not None:
            if other.object_id == device_pk:
                return True
            continue
        instance = created.get(other.ref_id) if other.ref_id else None
        if instance is not None and getattr(instance, "pk", None) == device_pk:
            return True
    return False


def _choose_adoption_candidate(model_class, candidates, data, master_pk, change, change_set, created):
    """
    Pick the row a master-bearing CREATE may adopt, or decline and let it create.

    Two answers only: the chosen row, or None for "adopt nothing, create the
    chassis this plan already asked for". There is deliberately no third,
    refusing answer. An ambiguous name is a reason not to touch anybody's row;
    it is not a reason to reject the payload, because the payload's own CREATE
    is a lossless alternative that always exists -- and a refusal here is
    permanent for the producer that provoked it. Measured on v4.5.5 with the
    real 154-entity orb-agent snmp-discovery capture replayed against a
    masterless, populated, same-named chassis:

      - refusing: the standalone chassis entity and both member entities are
        rejected on every pass, forever -- 400 each at apply-change-set, 207
        aggregated at bulk-plan-apply. Nothing in the capture changes, so
        nothing converges: the stack never gets a chassis, and the other two
        switches reach NetBox only through their interface entities, which
        create them with virtual_chassis NULL. orb-agent emits no ``domain``
        at all (its device_name builder sends name + master), so the remedy a
        refusal could name is not one this producer can take.
      - declining and creating: 200, the stack gets its OWN chassis with its
        three members at 1/2/3, the foreign row is byte-identical afterwards,
        and the re-diff is empty. That is also exactly what develop (08af3fb,
        which has no adoption at all) does with the same input, so it is not a
        regression on the branch point either.

    Declining is not free either, and the cost is downstream of this function.
    It leaves TWO populated rows sharing one name, and a name that matches two
    populated rows is what matcher.VirtualChassisNameMatcher.resolve refuses.
    Measured on v4.5.5: a producer sending name-only member references builds
    one masterless row; a standalone master-bearing chassis entity for the same
    name then declines and creates its own; from there an EXISTING member
    re-ingests fine (the member hint answers, rule 1), but a NEW member with a
    name-only reference is a 400 at generate-diff on that pass and every later
    one, and its device is not created until an operator merges the rows or
    places the device. That is the price of not touching a row this payload
    cannot identify, and it is the right price -- it is reported, it names what
    to do, and nothing has been written to anybody's stack -- but it is a
    price, not a free decline.

    Declining also makes the two halves of the same collision agree. A MASTERED
    same-named row already led to "create a second chassis, 200, converges" --
    the unique-master matcher answers first and the payload lands on its own
    row. A MASTERLESS one refused. Same name, same payload, opposite outcomes
    decided by a field of the colliding row that the payload never mentions;
    that asymmetry was the tell that the refusal was the wrong answer.

    What is bounded is ADOPTION, not the request. A row is adopted only where
    identity is strong enough that no device is moved OUT of a chassis it
    already belongs to on the strength of a name. Read that as the guarantee it
    is and not a stronger one: rule 4 below can still let a chassis-LESS device
    JOIN a same-named row another producer owns, and that residual is stated
    under it rather than counted as identity:

    1. the requested master is ALREADY a member of exactly one candidate. The
       database has already agreed with the payload; nothing moves. This is the
       convergence path for a member-first ingest whose member landed first,
       and it is the ONE rule that may take a row which already has a master
       (see the candidate query in _try_adopt_masterless_virtualchassis): an
       operator who re-elects a stack's master leaves a row that no matcher
       resolves any more -- the name matcher is gated off by the payload's
       master stub and unique_master looks for a device that no longer masters
       anything -- and without this the next ingest CREATES a second chassis
       and splits the stack. Measured on v4.5.5 and v4.4.10 with the full
       154-entity capture: 200 and one row with it, 207 forever and a stack
       split 1 + 2 without it (see
       test_the_capture_reconverges_after_an_operator_re_elects_the_master).
       Rules 2-4 never see a mastered row; only this one does.
    2. a NON-EMPTY discriminator the payload asserted
       (matcher.narrow_vc_candidates) leaves exactly one candidate. Name plus
       domain is a claim about which row, rather than a guess between rows. A
       value that matches NO candidate is not ambiguity at all -- the payload
       describes a chassis that does not exist yet, so it is created. An
       explicitly empty ``domain: ""`` narrows (it excludes the rows that DO
       carry a domain) but never identifies, because every row that never set a
       domain carries it: see narrow_vc_candidates.
    3. exactly one candidate and it is EMPTY -- no master, no members. Adopting
       it is indistinguishable in outcome from the CREATE the plan asked for
       (nothing is relocated, no existing membership is disturbed, the device
       ends up master of a stack containing only itself) except that no
       duplicate row is left behind. This is the plan-ahead race the pre-save
       match covers for masterless payloads, reaching the master-bearing half.
    There is deliberately no fourth rule. A POPULATED candidate matched on the
    name alone is never adopted -- not even when this changeset plans the
    device's membership of the chassis it is creating. That Device change says
    virtual_chassis = <the new CREATE reference>; it does not say <this
    pre-existing row>. Adoption is the step that would redirect the reference
    onto the row, so reading the reference back as evidence about the row is
    circular: it proves the producer wants membership of the chassis it is
    creating, and proves nothing about which same-named row that create may
    take over.

    It WAS a rule, and it is worth recording what it allowed, because the shape
    is ordinary rather than exotic. An existing "access-stack" holds
    building-a-sw2 and building-a-sw3 and has no master. A second producer sends
    building-b-sw1 nesting its own chassis of the same name. There is one
    populated masterless candidate and the changeset plans the membership, so
    building-b-sw1 joined the building-A stack and became its master -- 200, no
    duplicate row, no ambiguity, nothing for an operator to notice. Silent
    wrong-row mutation is the worst outcome available on this path, worse than
    either a duplicate or a refusal.

    Declining costs a name-only producer its automatic member-first
    convergence: it gets its own row instead, which is exactly what develop
    (08af3fb, no adoption at all) does with the same input. A visible duplicate
    can be merged by a human; a silent merge of two real stacks cannot be
    undone by one who never learns of it. What may take a populated row is real
    identity -- rule 1's existing membership, rule 2's asserted discriminator,
    or source-owned chassis identity once that exists -- and not a plan-shaped
    proxy for it.

    An earlier attempt to keep the rule and narrow it was tried and reverted:
    vetoing a candidate whose members sit outside the master's site broke
    legitimate cross-site member-first convergence (measured: 400 forever where
    the branch previously converged), and a VirtualChassis legitimately spans
    sites, so the site of its members is not evidence about its identity. The
    lesson taken here is that the rule could not be narrowed into safety, not
    that a narrower version was needed.
    """
    # A NON-EMPTY asserted discriminator excludes rows before ANY rule below
    # reads them -- the same order matcher.resolve uses, and for the same
    # reason. It is the payload saying "not that row", and rule 1 below is not
    # entitled to overrule it: a device can sit in a chassis the payload is not
    # talking about.
    #
    # Measured before this filter existed, on a row named "pa-stack" carrying
    # domain "building-a", masterless, holding the requested master: a CREATE
    # asserting domain "building-b" was adopted by rule 1 and then WRITTEN --
    # the row came back labelled "building-b", 200, errors null. That is
    # somebody's stack silently relabelled by a payload describing a different
    # one. Adoption writes its payload (serializer.save() below), so getting
    # the row wrong here is worse than getting it wrong in the matcher, which
    # binds without writing.
    #
    # An EMPTY assertion is excluded here as it is in resolve: `domain: ""`
    # narrows but never identifies, so it stays with narrow_vc_candidates below.
    candidates = [c for c in candidates if not contradicting_vc_discriminator(c, data)]
    if not candidates:
        return None

    holders = set(
        model_class.objects.filter(
            pk__in=[c.pk for c in candidates], members__pk=master_pk
        ).values_list("pk", flat=True)
    )
    strong = [c for c in candidates if c.pk in holders]
    if len(strong) == 1:
        return strong[0]
    if len(strong) > 1:  # pragma: no cover - Device.virtual_chassis is one FK
        # Unreachable while membership is a single FK on Device: a device
        # belongs to at most one chassis, so at most one candidate can hold it.
        # Kept as an explicit decline rather than dropped, because the fallback
        # for an unhandled len(strong) > 1 would be to continue to the rules
        # below and possibly bind one of them -- picking among rows that each
        # claim the master is exactly what must not happen if the data model
        # ever allows it. Declining leaves the create path to resolve the
        # payload, and the master it names is already in a chassis, so
        # _MasterAttach reports that conflict rather than inventing an answer.
        return None

    candidates = [c for c in candidates if c.master_id is None]
    if not candidates:
        return None

    candidates, contradicted, identified = narrow_vc_candidates(candidates, data)
    if contradicted:
        return None
    if identified and len(candidates) == 1:
        return candidates[0]
    if len(candidates) != 1:
        # Several rows are equally consistent with this payload and none holds
        # the master. Nothing here tells them apart, so nothing here is
        # adopted; the create path gives the payload its own row.
        return None

    only = candidates[0]
    if only.master_id is None and not only._diode_member_count:
        # An empty masterless row is not a stack anyone owns: binding it merges
        # no membership and relocates no device, so the name is enough.
        return only
    # A POPULATED row matched on the name alone is never adopted, even when this
    # changeset plans the master's membership of the chassis it is creating.
    # That Device change says virtual_chassis = <the new CREATE reference>; it
    # does not say <this particular pre-existing row>. It proves the producer
    # wants membership in the chassis being created, and proves nothing about
    # which same-named row that create may safely take over -- adoption is what
    # would redirect the reference onto this row, so reading the reference back
    # as evidence for the row is circular.
    #
    # The failure it allowed was the worst kind available here: an existing
    # "access-stack" holding building-a-sw2 and building-a-sw3, a new producer
    # sending building-b-sw1 with its own master-bearing chassis of the same
    # name, and the plugin attaching building-b-sw1 to the building-A stack and
    # making it that stack's master -- reporting success, leaving no duplicate
    # and no ambiguity for anyone to find. Declining costs automatic member-
    # first convergence for a name-only producer, and that is the cheaper loss:
    # it creates the payload's own row, which is what develop (08af3fb) does
    # with the same input, and a duplicate is visible where a silent merge of
    # two real stacks is not. Real identity, not a plan-shaped proxy for it, is
    # what may take a populated row: rules 1 and 2 above, or source-owned
    # chassis identity when that exists.
    return None


def _try_adopt_masterless_virtualchassis(data: dict, model_class, serializer_class, request,
                                         change, change_set, created):
    """
    Adopt a same-named existing VirtualChassis for a master-bearing CREATE.

    A member-first ingest ordering (or a replaced master) can leave a VC whose
    master is unset; a later master-bearing CREATE should bind that row rather
    than create a same-named duplicate.

    Candidates are the same-named rows that are MASTERLESS, plus the one extra
    shape rule 1 exists for: a row that already holds the requested master as a
    member while carrying a DIFFERENT master. That is an operator re-election,
    and it is admitted because the requested master's own membership -- already
    in the database -- identifies the row without appeal to the name. A row
    that already carries the requested master is excluded: unique_master
    resolves that one at plan time, so a CREATE arriving here for it is a stale
    plan the create path must settle without rewriting the row (see
    test_master_already_owning_a_chassis_declines_adoption_of_a_decoy). The
    function keeps its name for now; "masterless" describes every candidate but
    that one. WHICH row, and whether any row may be
    bound at all, is decided by _choose_adoption_candidate and is the whole
    identity question -- it is not settled by name plus creation order, which is
    what this function used to do (prefer a candidate already containing the
    master, else the oldest).

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

    What is NOT deferred any more: a master that is a plain MEMBER of a
    DIFFERENT chassis. That used to drop master and apply the rest, which
    reported a payload requesting VirtualChassis(name=X, master=Y) as
    successfully applied while Y was not made master of anything -- a partial
    interpretation dressed as success, and one that need never converge. It is
    now the structured conflict _attach_master_to_virtualchassis's caller
    raises. A master pk with no device behind it stays a hard serializer error;
    a device that already MASTERS another chassis still declines the adoption
    outright, because that other row IS the row the payload identifies (master
    is a DB unique key) -- see _MasterAttach.

    Bounds, stated as bounds rather than as accepted collateral. Adoption can
    bind a row this payload only described: rule 2 (name plus a non-empty
    discriminator the payload asserted), rule 3 (a single EMPTY row) and rule 4
    (a single populated row whose membership this changeset plans). Rule 1 is
    not among them -- it binds only a row the requested master is already IN,
    which the payload does not merely describe. Rule 4 is
    the one that can still land a member-first payload on a same-named row
    another producer owns, and _choose_adoption_candidate says why no narrowing
    of it survived measurement. What adoption never does is bind a row on a
    NAME alone: where identity is not strong it returns None and the payload
    gets its own chassis from the ordinary create path, which is what the
    pre-save match does for masterless payloads by declining to write the row
    it matched (matcher._PRE_SAVE_MATCH_BIND_ONLY). Nothing is refused for
    ambiguity at either door, because the create is always available and a
    refusal a producer cannot act on never converges.
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
    candidates = list(
        annotate_vc_member_counts(
            model_class.objects.filter(name=name).filter(
                models.Q(master__isnull=True)
                | (models.Q(members__pk=master_pk) & ~models.Q(master_id=master_pk))
            ).distinct()
        ).order_by("pk")
    )
    if not candidates:
        return None
    existing = _choose_adoption_candidate(
        model_class, candidates, data, master_pk, change, change_set, created
    )
    if existing is None:
        return None
    update_data = dict(data)
    snapshot_for_apply(existing)
    if not existing.members.filter(pk=master_pk).exists():
        # A device change in this same changeset asserting this chassis IS authority
        # for the membership; without it, a VC payload is not.
        move_is_planned = _changeset_plans_membership(change, change_set, created, master_pk)
        match _attach_master_to_virtualchassis(
            existing, master_pk, request, move_is_planned=move_is_planned
        ):
            case _MasterAttach.ATTACHED:
                # The attach bumped VirtualChassis.member_count through a direct
                # UPDATE (utilities.counters), invisible to this instance. Re-read
                # before the serializer saves it, or the full save writes the stale
                # in-memory counter back over the new one.
                existing.refresh_from_db()
            case _MasterAttach.IN_OTHER_CHASSIS:
                # A conflict, not a defer. The payload asks for master=device;
                # the device is a member of a different chassis and a
                # VirtualChassis payload is not authority to relocate it. Saving
                # the rest and answering 200 reports a request that was only
                # partly carried out as one that succeeded, and nothing in a
                # standalone VC payload can ever converge it.
                raise _master_in_other_chassis_error(existing, master_pk)
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
                # The enum documents four outcomes and the branches above cover
                # them. A fifth member added without a branch here would fall
                # straight through this match and silently inherit whatever the
                # surrounding code does next, which is how the collapsed
                # `device is None or ...` guard this enum replaced went wrong.
                raise AssertionError(f"unhandled master-attach outcome: {unexpected}")
    serializer = serializer_class(existing, data=update_data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    result = serializer.save()
    invalidate_find_obj_entry("dcim.virtualchassis", existing.id)
    return result


def _master_in_other_chassis_error(virtual_chassis, master_pk):
    """The structured conflict for "the named master lives in another chassis"."""
    device_model = get_object_type_model("dcim.device")
    device = device_model.objects.filter(pk=master_pk).select_related("virtual_chassis").first()
    device_label = f"{device.name!r} (id {master_pk})" if device is not None else f"id {master_pk}"
    holder = getattr(device, "virtual_chassis", None)
    holder_label = (
        f"{holder.name!r} (id {holder.pk})" if holder is not None else "another virtual chassis"
    )
    return _err(
        f"Cannot designate device {device_label} as master of VirtualChassis "
        f"{virtual_chassis.name!r} (id {virtual_chassis.pk}): the device is a member of "
        f"{holder_label}. A dcim.virtualchassis payload is not authority to move a device "
        f"between chassis. Move the device in NetBox -- out of {holder_label} and into "
        f"{virtual_chassis.name!r} (id {virtual_chassis.pk}) -- and this payload applies "
        f"unchanged on the next pass; or name a different master.",
        "dcim.virtualchassis", "master",
    )


def _attach_master_to_virtualchassis(
    virtual_chassis, master_pk, request, move_is_planned: bool = False
) -> _MasterAttach:
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
    chassis and ``move_is_planned`` is false. Membership is asserted by the
    DEVICE payload (Device.virtual_chassis); a VC payload naming a master is not
    authority to pull a device out of a chassis it is already in. The caller
    turns that refusal into a reported conflict rather than a quiet drop of
    master -- _try_adopt_masterless_virtualchassis, and _MasterAttach on why.

    ``move_is_planned`` says this changeset already carries the device change
    that asserts the membership (_changeset_plans_membership). Then the move is
    not this payload's invention and refusing it is wrong: the device change is
    in the preview, it is ordered after the chassis it names, and rejecting the
    apply rolls it back so every identical re-ingest fails the same way. The
    membership write still has to happen here rather than being left to that
    deferred update, because NetBox makes membership a precondition of setting
    VirtualChassis.master.

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
    if device.virtual_chassis_id is not None and not move_is_planned:
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
# live database state AND from the rest of the changeset -- for
# dcim.virtualchassis, "the same-named masterless chassis that already holds
# this master, or that an explicit discriminator identifies, or that is empty,
# or that a planned device change names" is not something find_existing_object
# can express: it answers from the payload alone and it may not raise a
# conflict.
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


def _apply_change(data: dict, model_class: models.Model, change: Change, created: dict, request,
                  change_set: ChangeSet, warnings: list | None = None):
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
            instance = _try_pre_save_match(
                data, change.object_type, serializer_class, request, warnings)

        if not instance and (adopt := _CREATE_ADOPTERS.get(change.object_type)):
            # The whole changeset is passed, not just this change: whether a row
            # may be adopted can depend on what else the changeset already plans
            # (_changeset_plans_membership). ``created`` carries the instances
            # of the changes applied before this one, which is how a device
            # created earlier in the same changeset is identified by pk.
            instance = adopt(data, model_class, serializer_class, request,
                             change, change_set, created)

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

