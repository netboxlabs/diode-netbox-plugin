#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Object matching utilities."""

import contextvars
import hashlib
import logging
import time
from dataclasses import dataclass
from functools import cache, lru_cache

import netaddr
from django.contrib.contenttypes.fields import ContentType
from django.core.cache import cache as django_cache
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import F, Value
from django.db.models.fields import SlugField
from django.db.models.lookups import Exact
from django.db.models.query_utils import Q
from django.db.models.signals import post_delete, post_save
from extras.models.customfields import CustomField
from netbox.plugins import get_plugin_config

from .common import NON_FIELD_ERRORS, VC_MEMBER_HINT, ChangeSetException, UnresolvedReference
from .compat import in_version_range
from .plugin_utils import content_type_id, get_object_type, get_object_type_model
from .profile import get_profile_ctx

logger = logging.getLogger(__name__)

_request_obj_cache = contextvars.ContextVar("diode_request_obj_cache", default=None)


def enter_request_obj_cache():
    """
    Activate a request-scoped object lookup cache.

    Only positive (found-instance) results are stored. A miss is left
    uncached so that subsequent lookups against the same key correctly
    pick up a row another worker (or this request's apply phase) has
    inserted in the meantime. This is what makes the cache safe to share
    across plan and apply phases within the same request.
    """
    return _request_obj_cache.set({})


def exit_request_obj_cache(token):
    """Deactivate the request-scoped object lookup cache."""
    _request_obj_cache.reset(token)


def _get_find_obj_cache_ttl() -> int:
    return get_plugin_config("netbox_diode_plugin", "find_obj_cache_ttl")


def _get_active_branch_schema() -> str | None:
    try:
        from netbox_branching.contextvars import active_branch
    except ImportError:
        return None
    branch = active_branch.get()
    if branch is not None:
        return branch.schema_id
    return None


def _find_obj_rev_key(object_type: str, object_id: int) -> str:
    branch_schema = _get_active_branch_schema()
    if branch_schema:
        return f"diode:fobj:rev:{branch_schema}:{object_type}:{object_id}"
    return f"diode:fobj:rev:{object_type}:{object_id}"


def invalidate_find_obj_entry(object_type: str, object_id: int):
    """
    Delete a cached find_existing_object result by PK.

    Uses a reverse-index (PK → cache key) to find and delete the
    lookup cache entry. Call this after updating an existing object.
    """
    rev_key = _find_obj_rev_key(object_type, object_id)
    lookup_key = django_cache.get(rev_key)
    if lookup_key:
        django_cache.delete(lookup_key)
        django_cache.delete(rev_key)

#
# these matchers are not driven by netbox unique constraints,
# but are logical criteria that may be used to match objects.
# These should represent the likely intent of a user when
# matching existing objects.
#
# Object types whose logical match criteria are NOT backed by a DB
# unique constraint. For CREATE changes on these types the applier
# must call find_existing_object BEFORE serializer.save(); otherwise
# concurrent planners can each emit CREATE for the same logical row
# and both inserts succeed, producing duplicates. The standard
# IntegrityError fallback in _create_or_find_instance cannot catch
# this because save() does not fail.
#
# The specific gaps (see _LOGICAL_MATCHERS below for the criteria):
#   - dcim.macaddress: NetBox has no unique constraint on
#     (mac_address, assigned_object_type, assigned_object_id).
#   - dcim.modulebay: matched by (name, device); NetBox's parent-aware
#     constraint does not catch unscoped duplicates the matcher dedupes.
#   - dcim.virtualchassis: matched by name when the payload has no master;
#     NetBox has no uniqueness on VC name at all. Only the MASTERLESS half of
#     this type's payload space takes the pre-save match -- see
#     _virtualchassis_pre_save_match_applies for why a master-bearing CREATE
#     must not, and _PRE_SAVE_MATCH_PAYLOAD_GATES for how it is excluded.
#     That same absent uniqueness means a row matched by name may be a
#     different stack that merely shares it, so this type's match BINDS the row
#     and writes nothing to it, while still validating the payload against it
#     -- see _PRE_SAVE_MATCH_BIND_ONLY. And when the name matches SEVERAL rows
#     that nothing separates, it matches NOTHING and says so:
#     VirtualChassisNameMatcher.resolve raises AmbiguousObjectMatch rather than
#     returning the oldest, because the caller would point a Device's
#     virtual_chassis at whatever came back.
#   - ipam.prefix: NetBox has no unique constraint on prefix, nor on
#     (prefix, vrf) - Prefix.Meta carries only ordering and indexes.
#     Duplicate detection lives solely in Prefix.clean(), behind
#     ENFORCE_GLOBAL_UNIQUE or the VRF's enforce_unique flag, and the
#     applier saves through DRF serializers without calling full_clean(),
#     so that check never runs either.
#   - ipam.vlan: NetBox's (group, vid) constraint does not enforce
#     uniqueness when group is NULL.
#   - ipam.vlangroup: NetBox does not enforce uniqueness of name when
#     scope_type is NULL.
#   - ipam.vrf: NetBox enforces uniqueness on rd, not name; multiple
#     VRFs with rd=NULL and the same name are otherwise allowed.
#   - virtualization.cluster: NetBox does not enforce uniqueness of
#     name across scopes; matched by (name, scope_type, scope_id) or
#     by name alone when unscoped and unparented.
#   - virtualization.virtualmachine: NetBox does not enforce uniqueness
#     of name when cluster is NULL.
#   - wireless.wirelesslan: NetBox does not enforce ssid uniqueness.
#
# The list also covers a DB-constraint-backed type where find-first replaces
# a lossy, noisy IntegrityError recovery with a real adopt-update:
#   - dcim.module: plan-ahead topologies (plan-all-then-apply-all) emit a
#     second CREATE for an occupied module bay; find-first adopts it and
#     applies the payload instead of discarding it after a failed INSERT.
#
# This closes the common race (concurrent plan, sequential apply).
# It does not close TOCTOU under truly concurrent apply across
# replicas - that would require a DB unique constraint or a
# coordinating lock. That bound is measured, not assumed: with a
# threading.Barrier released inside find_existing_object so both
# applies pass the lookup before either saves, dcim.virtualchassis
# ends at TWO rows with the pre-save match and two rows without it.
# Read any claim about this routing as covering concurrent PLAN plus
# sequential APPLY, and nothing wider.
_REQUIRES_PRE_SAVE_MATCH = frozenset({
    "dcim.cable",
    "dcim.macaddress",
    "dcim.module",
    "dcim.modulebay",
    "dcim.virtualchassis",
    "ipam.prefix",
    "ipam.vlan",
    "ipam.vlangroup",
    "ipam.vrf",
    "virtualization.cluster",
    "virtualization.virtualmachine",
    "wireless.wirelesslan",
})


def _virtualchassis_pre_save_match_applies(data: dict) -> bool:
    """
    Pre-save match a dcim.virtualchassis CREATE only when it carries no master.

    What the routing is FOR, concretely -- the plan-ahead race. Two
    generate-diff calls are issued before either apply, one per member device,
    each carrying virtual_chassis: {"name": "X"} at a moment when no chassis
    named X exists. Neither planner can match anything (there is no row to
    match yet), so BOTH plans contain a create dcim.virtualchassis
    {"name": "X"} with no master. Applied in sequence, the second create
    inserts a SECOND row: a split chassis, half the members on each, one half
    orphaned. Nothing else catches it -- VirtualChassis.name has no unique
    constraint, so the INSERT succeeds and _create_or_find_instance's
    IntegrityError fallback never runs; and no matcher can help at plan time
    because the row genuinely does not exist yet. Looking the name up
    immediately BEFORE the save is the only thing that collapses the two plans
    onto one row.

    What that covers is the MASTERLESS half of the race, which is the half this
    gate admits: a member-only payload names the chassis and nothing more, so
    the create it plans carries no master. It is NOT the whole race. The PR's
    headline natural shape plans a MASTER-BEARING create dcim.virtualchassis,
    and two such plans naming DIFFERENT masters split the chassis permanently:
    two rows, member_count 1 each, and empty re-diffs afterwards, so nothing
    converges them. That split is a pre-existing bound rather than a cost of
    this gate -- it reproduces on the parent commit, on origin/develop, and
    even with the two ingests run fully sequentially, because each row's master
    is a legitimately different device and VirtualChassis.name is not unique.
    It is stated here so the gate is not read as closing more than it does.

    A master-bearing CREATE is deliberately EXCLUDED, and it matters which
    hazard that still buys and which one it no longer does. With master present
    the matcher that answers is the auto-derived unique_master one
    (VirtualChassisNameMatcher gates itself off -- see its docstring), so a
    CREATE of "the chassis named NEW, mastered by device D" resolves onto
    whichever chassis D already masters. When the pre-save match still applied
    the payload, that RENAMED an existing, unrelated chassis on a create; with
    bind-only in force (_PRE_SAVE_MATCH_BIND_ONLY) it would instead silently
    bind the CREATE to that other chassis, so the chassis the payload named is
    never created and every later reference in the changeset resolves to the
    wrong row. Both are worth excluding; only the first is a write.

    The second reason to exclude them is a SEAM PIN, and is best not read as
    more: it keeps master out of an ORM pk filter, where a bool or a
    non-integral float coerces silently (True -> pk 1, 7.5 -> pk 7) and would
    select an unrelated row. Since bind-only landed this gate changes no row's
    state, so mutating it away fails only this gate's own seam test -- it is
    not a behavioural guarantee that the mis-coercion is "unreachable". The
    place a master value is genuinely coerced is the ADOPTION path,
    applier._try_adopt_masterless_virtualchassis, which guards it explicitly
    with applier._coerce_pk. That distinction has been got wrong once already;
    keep it straight here.

    Nothing is lost by excluding them. VirtualChassis.master IS a DB unique
    constraint, so a duplicate master-bearing insert raises IntegrityError and
    _create_or_find_instance recovers; and a same-named MASTERLESS row left by
    a member-first ingest is bound by
    applier._try_adopt_masterless_virtualchassis, which chooses its row from
    live database state and guards the same malformed-pk hazard explicitly.
    "Chooses" is bounded there and the bound is the point: it adopts only a row
    whose identity is established (it already holds the requested master, an
    explicit discriminator names it, it is empty, or a device change in the same
    changeset asserts the membership) and otherwise declines, letting the
    payload create its own chassis rather than binding a same-named row on the
    strength of the name.

    An explicit null counts as absent, matching VirtualChassisNameMatcher's own
    gate: the transformer emits master: None for a member-only payload.
    """
    return data.get("master") is None


# Payload-level narrowing for entries in _REQUIRES_PRE_SAVE_MATCH whose
# pre-save match is safe for only PART of their type's payload space. A type
# with no gate here takes the pre-save match for every CREATE.
_PRE_SAVE_MATCH_PAYLOAD_GATES = {
    "dcim.virtualchassis": _virtualchassis_pre_save_match_applies,
}


# Types whose pre-save match may BIND an existing row but must not WRITE to it.
#
# The pre-save match does two separable things in one code path: it decides
# that a CREATE names a row that already exists (a dedupe -- the only thing
# _REQUIRES_PRE_SAVE_MATCH is for), and it then applies the CREATE's payload to
# that row (an update). The second is safe only where the match criteria really
# do identify the row. For an auto-created component they do: NetBox
# instantiated it from the very device or module the payload names, and the
# payload is the authority meant to overwrite the template's defaults.
#
# For dcim.virtualchassis they do not. The criterion is the name alone and
# VirtualChassis.name carries no unique constraint, so the matched row may be a
# different, converged stack that another source owns. Writing there was
# measurably destructive: a stale plan's description and domain landed on
# another site's live chassis, no error, and no later diff mentioning it -- two
# sources then flap those fields indefinitely. Binding without writing keeps
# the dedupe the plan-ahead race needs.
#
# It is NOT free, and an earlier draft of this comment claiming it "gives up
# nothing else" was wrong. Binding costs ONE INGEST ROUND on the row that is
# genuinely correct, not only on a foreign one. A member-first ingest leaves a
# bare chassis row; when the chassis's own entity is then ingested ONCE, from a
# plan built before that row existed, the apply returns 200 and binds -- and
# description, domain, comments, tags and custom_fields stay unset until the
# next pass, where the matcher finds the row and the plan is an UPDATE
# addressing it by id, which is the path that may write. The parent commit
# landed all five on that same apply. For a steady-state producer that is one
# round of latency; for a one-shot backfill, or a push-on-change producer that
# will not re-send, the data waits for the next full re-ingest. The trade is
# still worth taking -- a wrong write into another source's live stack is
# silent and unrepairable, a delayed write is neither -- but it is a trade.
#
# Why this set holds dcim.virtualchassis ALONE, when the no-uniqueness argument
# above applies just as well to the nine other types this file's own gap list
# names for absent uniqueness -- dcim.macaddress, dcim.modulebay, ipam.prefix,
# ipam.vlan, ipam.vlangroup, ipam.vrf, virtualization.cluster,
# virtualization.virtualmachine, wireless.wirelesslan (dcim.module is the
# separate DB-constraint-backed entry above, and dcim.cable is matched by its
# terminations): because those were measured to behave the same way, and to do
# so already. ipam.vlan, ipam.vrf, ipam.prefix and
# virtualization.cluster each let a CREATE overwrite a same-keyed row's own
# fields through the pre-save match, IDENTICALLY on the parent commit and on
# origin/develop, so none of it is a regression this branch introduces, and
# widening the set here would change behaviour for four more types well outside
# a VirtualChassis ingest PR. dcim.virtualchassis is in this set because this
# branch is what put VC CREATEs on the pre-save path at all -- not because VC
# is uniquely exposed. Broadening it is filed separately.
#
# Read narrowly, because this does NOT make a same-named row safe from ingest
# generally. Once the row exists the matcher finds it by name, so the next
# generate-diff plans an UPDATE onto it and that update is applied; two sources
# naming one chassis still flap its description and domain. That flap comes
# from name matching in the DIFF path and reproduces on the parent commit and
# on origin/develop alike -- it is out of this seam's reach. What changes here
# is narrower and worth having on its own: a CREATE no longer writes a row it
# only guessed at, and no duplicate row is left behind when it declines.
#
# Binding declines the WRITE, not the payload's errors: the applier still
# validates the CREATE against the matched row and discards the save, so an
# invalid payload is still the 400 it was before this seam existed. And the
# no-write property belongs to the CREATE path only -- see
# applier._try_bind_existing_instance for the ref_id-update gap it does not
# cover.
_PRE_SAVE_MATCH_BIND_ONLY = frozenset({
    "dcim.virtualchassis",
})


def pre_save_match_binds_only(object_type: str) -> bool:
    """Whether a pre-save-matched CREATE binds its row without writing to it."""
    return object_type in _PRE_SAVE_MATCH_BIND_ONLY


def requires_pre_save_match(object_type: str, data: dict | None = None) -> bool:
    """
    Whether the applier must look up an existing row before CREATE.

    ``data`` is the CREATE payload. It is optional only so a caller can ask the
    type-level question; a type carrying a payload gate (see
    _PRE_SAVE_MATCH_PAYLOAD_GATES) answers False without one. That default is
    the safe direction: the pre-save match resolves a CREATE onto an existing
    row, and for every type outside _PRE_SAVE_MATCH_BIND_ONLY it then writes
    the payload there, so taking that route unproven is the direction that does
    damage, while declining it only forgoes a dedupe. (For the one gated type
    today, dcim.virtualchassis, the match is bind-only and writes nothing --
    so the default is conservative about the resolution itself, not about a
    write.)
    """
    if object_type not in _REQUIRES_PRE_SAVE_MATCH:
        return False
    gate = _PRE_SAVE_MATCH_PAYLOAD_GATES.get(object_type)
    if gate is None:
        return True
    if data is None:
        return False
    return gate(data)


class AmbiguousObjectMatch(ChangeSetException):
    """
    A lookup found several rows and no rule could tell which one was meant.

    find_existing_object otherwise answers "an instance or None", and both
    answers are actionable: an instance resolves the reference, None creates.
    Ambiguity is neither, and the two ways of forcing it into that pair are
    both wrong. Returning None creates a duplicate of a row that already
    exists; returning one of the candidates picks an identity out of the air --
    for dcim.virtualchassis that means pointing a Device's virtual_chassis at
    an arbitrary same-named stack, which is a data move made on the strength of
    a name.

    So it is raised, as a ChangeSetException carrying the per-entity error shape
    the rest of the API already speaks ({object_type: {field: [message]}}).
    That subclassing is what makes ONE raise cover BOTH boundaries: the plan
    path (transformer._resolve_existing_references, surfaced by
    differ.generate_changeset and the generate-diff / bulk-plan views) and the
    apply path (applier._try_bind_existing_instance and
    _create_or_find_instance, reached by apply-change-set and bulk-apply, which
    bypass the transformer entirely). Neither boundary needs its own
    translation, and neither can grow a hole the other does not.

    It is deliberately NOT a ValueError or TypeError. Three call sites --
    _find_existing_object_or_none, _try_find_and_update_existing_instance and
    the applier's own handler chain -- swallow those two to turn a malformed
    reference into "no match", which is right for a payload the ORM cannot even
    query and exactly wrong for a payload that queried fine and matched too
    much.
    """

    def __init__(self, message, object_type, field=NON_FIELD_ERRORS):
        """Build the per-entity error for one ambiguous lookup."""
        super().__init__(message, errors={object_type: {field: [message]}})
        self.object_type = object_type
        self.field = field


# Fields a payload may carry that DISCRIMINATE between same-named
# VirtualChassis rows. A value here is matched against the candidate rows; it
# is never used to guess, and a value no candidate carries means "not one of
# these rows" rather than "pick one anyway".
#
# domain is the only one today. It is NetBox's own grouping field on
# VirtualChassis, it is part of the SDK shape a producer already sends, and it
# is the discriminator the review named. site is deliberately absent:
# VirtualChassis has no site of its own (its members do), so filtering on it
# would mean inferring identity from member rows, which is the same guess by a
# longer route.
_VC_DISCRIMINATORS = ("domain",)


def _vc_row_value(candidate, field) -> str:
    """A candidate's discriminator value, normalised to a string."""
    # domain is a non-null CharField, so this is "" for a row that never set
    # one. It is normalised rather than read raw because the comparison below
    # must not decide that None != "" and treat a domainless row as carrying a
    # value the payload contradicts.
    return getattr(candidate, field, None) or ""


def asserted_vc_discriminators(data: dict) -> dict:
    """
    The discriminators this payload ASSERTS, empty strings included.

    "Asserts" is deliberately not "has a truthy value for". An explicitly
    submitted ``domain: ""`` is a value -- the producer saying this chassis has
    no domain -- and dropping it was the defect this function exists to remove:
    it let a payload that asserts domainlessness bind a domain-BEARING row and
    then write "" over it, destroying the one field the ambiguity refusals point
    at. Absence (no key, null, or a non-string) asserts
    nothing, because the ingest applies only the fields it was given.
    """
    return {
        field: data[field]
        for field in _VC_DISCRIMINATORS
        if isinstance(data.get(field), str)
    }


def describe_vc_assertions(data: dict) -> str:
    """The asserted discriminators, for a refusal message. "" reads as empty."""
    asserted = asserted_vc_discriminators(data)
    return ", ".join(f"{field} {value!r}" for field, value in asserted.items())


def narrow_vc_candidates(candidates, data) -> tuple[list, bool, bool]:
    """
    Narrow same-named candidates by what the payload asserts. ONE implementation.

    Returns ``(candidates, contradicted, identified)``:

    - ``contradicted`` -- an asserted value that NO candidate carries. That is
      not ambiguity: the payload describes a chassis none of these rows is, so
      the caller creates it rather than binding a row whose own discriminator
      says otherwise.
    - ``identified`` -- at least one NON-EMPTY value was asserted and matched.
      Emptiness is the whole distinction: ``domain: "dc-a"`` is a claim about
      WHICH row, while ``domain: ""`` is shared by every row that never set a
      domain and so tells none of them apart. So "" narrows (it excludes the
      rows that do carry a domain) but never identifies, and a caller that
      treats identification as permission to bind a POPULATED row -- see
      applier._choose_adoption_candidate rule 2 -- must not be handed that
      permission by an empty string.

    Both call sites had a copy of this loop and the copies disagreed about ""
    in the same wrong direction. It lives here, once, next to
    _VC_DISCRIMINATORS, because the semantics of a discriminator are a property
    of the discriminator and not of the caller.
    """
    identified = False
    for field, value in asserted_vc_discriminators(data).items():
        narrowed = [c for c in candidates if _vc_row_value(c, field) == value]
        if not narrowed:
            return [], True, identified
        if value:
            identified = True
        candidates = narrowed
    return candidates, False, identified


def asserted_vc_identity(data: dict) -> dict:
    """
    What this payload CLAIMS about WHICH chassis it is: master, then discriminators.

    One notion of VirtualChassis identity, extended by exactly one field.
    ``narrow_vc_candidates`` answers "which of these ROWS is the payload talking
    about"; this answers "do these two PAYLOAD NODES talk about the same
    chassis", which is what dedupe needs (transformer._vc_identity_partition).
    Both read the same _VC_DISCRIMINATORS, so a domain can never tell rows apart
    and nodes not apart, or the other way round -- two notions of VC identity
    that can disagree is how this branch earned several of its earlier bugs.

    ``_netbox_id`` is the strongest identity of all and is read first: it names
    a database row outright, so two nodes carrying different ones are different
    chassis whatever else they agree on. It has to be read HERE because dedupe
    runs before _resolve_existing_references, which is the only other place
    that looks at it -- without it, two same-named nodes explicitly addressing
    two different rows merged into one before anything consulted their ids.

    master is here and NOT in _VC_DISCRIMINATORS, and the split is about where
    the comparison happens rather than about what identity means.
    VirtualChassis.master is a DB UNIQUE constraint, so it is the strongest
    identity a payload can carry that is not simply the row's own id: two nodes
    naming different masters are different stacks
    even with no domain anywhere, and two naming the same master are one stack
    whatever else they say. But it cannot narrow live rows the way a
    discriminator does -- at transform time it is an UnresolvedReference to a
    device that may not exist yet, so there is no value to match a row's
    master_id against, and matcher's own rows-side gates deliberately keep
    master out of an ORM filter (see _virtualchassis_pre_save_match_applies).
    Node-against-node, both sides are payload, so it compares cleanly.

    "Asserts" carries the same meaning as in asserted_vc_discriminators: an
    explicit ``domain: ""`` is a value, and absence asserts nothing. For master,
    absence includes an explicit null -- the transformer emits ``master: None``
    for a member-only payload, which claims nothing about which stack this is.
    """
    identity = {}
    netbox_id = data.get("_netbox_id")
    if netbox_id is not None:
        identity["_netbox_id"] = netbox_id
    master = data.get("master")
    if master is not None:
        identity["master"] = master
    identity.update(asserted_vc_discriminators(data))
    return identity


# A group field its own members contradict each other about. It equals no
# asserted value, so such a field stops telling anything apart instead of
# silently answering with whichever member was seen first.
_VC_CONTESTED = object()


def vc_identities_conflict(a: dict, b: dict) -> bool:
    """
    True when two asserted identities CANNOT be the same chassis.

    Compatibility, not equality: a node that asserts nothing conflicts with
    nothing, which is what keeps a member's name-only chassis node and the
    master-bearing one merging into a single create (issue #183).

    master decides alone when both sides carry one, in BOTH directions. Same
    master means one chassis even if the domains disagree -- the unique
    constraint says there is only one row, so a domain disagreement there is a
    field conflict for _merge_nodes to report, not licence to plan a second row
    that could not be inserted. Different masters mean different chassis even if
    the domains agree.
    """
    if "_netbox_id" in a and "_netbox_id" in b:
        # An explicit row id outranks everything, including master. Two nodes
        # naming ONE row are one chassis and any other disagreement between
        # them is a field conflict; two naming DIFFERENT rows are different
        # chassis even if they also claim one master, and letting that reach
        # the unique constraint reports it, where merging them would silently
        # drop one node's addressed row.
        return a["_netbox_id"] != b["_netbox_id"]
    if "master" in a and "master" in b:
        return a["master"] != b["master"]
    for field, value in a.items():
        if field in ("master", "_netbox_id"):
            continue
        if field in b and b[field] != value:
            return True
    return False


def _vc_identity_key(identity: dict) -> tuple:
    """A hashable form of an asserted identity: nodes making one claim group as one."""
    return tuple(sorted(identity.items(), key=lambda item: item[0]))


def _absorb_vc_identity(group: dict, identity: dict) -> None:
    """Fold a node's assertions into its group's; a contradiction CONTESTS the field."""
    for field, value in identity.items():
        if field in group and group[field] != value:
            group[field] = _VC_CONTESTED
        else:
            group.setdefault(field, value)


def _seed_vc_groups(identities: list[dict], assigned: list) -> tuple[list[dict], list[int]]:
    """
    Open the groups the ASSERTING nodes establish: steps 1 and 2 of the partition.

    master first, because it is a unique key: one group per distinct master, and
    a node naming one always lands on its master's group -- vc_identities_conflict
    answers on master alone when both sides carry one, so this loop cannot split
    two nodes that name the same master however much else they disagree about.
    Only when no node in the bucket names a master at all do the discriminators
    seed instead, one group per distinct asserted set: a domain cannot outrank a
    master, but with no master anywhere it is the strongest thing said.

    Returns the groups and the ids of the seeded ones. Every group here is a
    candidate for the nodes that assert nothing; groups opened later, for nodes
    nothing could place, deliberately are not.
    """
    groups: list[dict] = []
    seed_ids: list[int] = []

    def seed(index):
        identity = identities[index]
        group_id = next(
            (g for g in seed_ids if not vc_identities_conflict(groups[g], identity)),
            None,
        )
        if group_id is None:
            groups.append({})
            group_id = len(groups) - 1
            seed_ids.append(group_id)
        assigned[index] = group_id
        _absorb_vc_identity(groups[group_id], identity)

    for i, identity in enumerate(identities):
        if "master" in identity:
            seed(i)

    if not seed_ids:
        for i, identity in enumerate(identities):
            if identity:
                seed(i)

    return groups, seed_ids


def _place_unseeded_vc_nodes(
    identities: list[dict], assigned: list, groups: list[dict], seed_ids: list[int]
) -> None:
    """
    Place the nodes that seeded nothing: step 3 of the partition.

    Every one of them is resolved against the SEEDED groups before any of them
    moves, so no node's answer can depend on which node arrived first. Nodes
    asserting the same thing are bucketed and travel together; a bucket joins a
    group when that group is the only one that can take it AND no other bucket
    claiming that same group CONFLICTS WITH THIS ONE -- two buckets asserting
    different domains, both compatible with one group that says nothing about
    domains, are both refused rather than settled by arrival order.

    The test is against this bucket, not across the claimants collectively. A
    bucket that asserts NOTHING conflicts with nobody, so two OTHER buckets
    disagreeing with each other must not take it down with them: they are each
    refused and get their own group, so they never join, and the silent bucket's
    claim was unambiguous all along. Reading the claimants as a group refused it
    anyway -- usually a member device's name-only chassis reference, pushed into
    a chassis of its own that nothing identified, which is the outcome refusing
    exists to avoid.
    """
    buckets: dict[tuple, list[int]] = {}
    for i, identity in enumerate(identities):
        if assigned[i] is None:
            buckets.setdefault(_vc_identity_key(identity), []).append(i)

    takers = {
        key: [
            group_id for group_id in seed_ids
            if not vc_identities_conflict(groups[group_id], identities[members[0]])
        ]
        for key, members in buckets.items()
    }
    claimants: dict[int, list[tuple]] = {}
    for key, group_ids in takers.items():
        if len(group_ids) == 1:
            claimants.setdefault(group_ids[0], []).append(key)

    for key, members in buckets.items():
        group_ids = takers[key]
        mine = identities[members[0]]
        rivals = [
            identities[buckets[claim][0]]
            for claim in (claimants.get(group_ids[0], ()) if len(group_ids) == 1 else ())
            if claim != key
        ]
        if len(group_ids) == 1 and not any(
            vc_identities_conflict(mine, rival) for rival in rivals
        ):
            group_id = group_ids[0]
        else:
            groups.append({})
            group_id = len(groups) - 1
        for index in members:
            assigned[index] = group_id
            _absorb_vc_identity(groups[group_id], identities[index])


def vc_unique_master_fingerprint(data: dict):
    """
    The one VirtualChassis fingerprint an identity partition must NOT qualify.

    Qualifying a split node's fingerprints keeps the groups from meeting, and
    every key has to be qualified except this one -- the inverse of the first
    attempt, which qualified only the name key and let two groups meet on the
    whole-payload key instead. That key ignores private fields, so two nodes
    differing ONLY in ``_netbox_id`` hash identically and merged straight back
    together, silently dropping one addressed row.

    unique_master is the exception because it is a DB unique constraint:
    VirtualChassis.master is unique, so two nodes naming one master ARE one row
    whatever their names or groups say, and they must still meet. Qualifying it
    too was the other half of the same bug in the other direction -- two nodes
    naming one master under DIFFERENT names stopped merging, and the second
    create bound the first row while claiming a master the DB holds unique.

    Returns None when the payload asserts no master, in which case nothing is
    exempt and every fingerprint is qualified.

    The caller withholds the exemption from a node carrying an explicit
    ``_netbox_id``: "two nodes naming one master are one row" holds only while
    neither has said WHICH row, and two nodes addressing different rows have.
    """
    for matcher in get_model_matchers(get_object_type_model("dcim.virtualchassis")):
        if getattr(matcher, "name", None) == "unique_master":
            return matcher.fingerprint(data)
    return None


def partition_vc_identities(identities: list[dict]) -> list[int]:
    """
    Split same-named VC nodes into the chassis they describe. ONE group index per node.

    Nodes sharing an index describe one chassis and must dedupe-merge; nodes
    with different indices are different chassis and must not. Hash equality
    cannot express this -- the rule is "the same stack UNLESS conflicting
    identity is asserted", a compatibility relation -- so the discrimination
    happens INSIDE the name bucket, after grouping by name, and mirrors
    narrow_vc_candidates: group by what is ASSERTED, and place a node that
    asserts nothing only when exactly one group can take it.

    The steps, and each is a fact about the nodes rather than a tie-break:

    1. master, the unique key, seeds the groups: one group per distinct master.
       Nothing below re-splits such a group, because nothing outranks a unique
       key.
    2. with no master asserted anywhere, the discriminators seed instead: one
       group per distinct asserted set. Nodes asserting the same domain are one
       stack; ``building-a`` and ``building-b`` are two, for the same reason
       narrow_vc_candidates lets a domain exclude a row -- a discriminator that
       tells rows apart must tell nodes apart too.
    3. every node left says nothing the seeding step could use, so it is placed
       by compatibility: onto the one seeded group that can take it, and
       otherwise into a group of its own. Refusing to choose is the answer
       VirtualChassisNameMatcher.resolve rule 4 gives, for the same reason:
       these nodes are usually a member device's chassis reference, so guessing
       does not merely pick a row, it puts that device in a stack the payload
       never identified. Nodes asserting the SAME thing travel together even
       here, so a graph naming one unidentifiable chassis twice still plans it
       once.

    Placement is decided for all of them at once, against the seeded groups
    only, so the answer cannot depend on the order the nodes arrive in -- which
    stack a member lands in must not depend on which reference its producer
    emitted first. That costs one refusal: where two nodes assert DIFFERENT
    things and the only group that could take either says nothing about it,
    neither is placed, because taking the one that happened to come first is
    exactly the order-dependence being avoided.

    A group's own identity is what its seeded members agree on; a field they
    contradict each other about is CONTESTED, which CONFLICTS with every
    assertion about that field rather than merely failing to match one -- so a
    node asserting it cannot join that group at all, which is the point: nothing
    is attracted by whichever value happened to arrive first. Assertions from
    the nodes placed in step 3 are absorbed the same way, so a group can become
    contested after seeding too. That disagreement is still _merge_nodes' to report -- partitioning
    decides which nodes are one chassis, never whether their fields agree.
    """
    assigned: list[int | None] = [None] * len(identities)
    groups, seed_ids = _seed_vc_groups(identities, assigned)
    _place_unseeded_vc_nodes(identities, assigned, groups, seed_ids)
    return assigned


def contradicting_vc_discriminator(candidate, data) -> str | None:
    """
    A NON-EMPTY discriminator this payload asserts that this row does not carry.

    This is identity being read as identity: ``domain: "building-b"`` against a
    row carrying "building-a" says "not this row", and it has to say it whether
    the name matches twenty rows or exactly one. Without that, domain meant two
    different things depending on how many rows happened to exist -- a
    discriminator when it could narrow a set, a mutable field to be overwritten
    when it could not -- and a payload asserting a contradicting domain against a
    lone same-named row bound that row and then re-planned the domain onto it on
    every later pass, forever.

    An EMPTY assertion is deliberately excluded. ``domain: ""`` narrows but never
    identifies (see narrow_vc_candidates: every row that never set a domain
    carries ""), so it must not turn the ordinary single-row match into a
    duplicate insert -- which is the outcome this matcher exists to prevent.
    """
    for field, value in asserted_vc_discriminators(data).items():
        if not value:
            continue
        if _vc_row_value(candidate, field) != value:
            return field
    return None


def unasserted_vc_discriminators(candidate, data) -> str:
    """
    The values THIS ROW carries that the payload says nothing about.

    A row bearing ``domain: "dc-a"`` when the payload asserts no domain is a row
    the payload has not identified: somebody labelled that stack, and this
    producer never mentioned the label. Its only caller uses it to choose
    between ADOPTING that row and creating the payload's own
    (applier._choose_adoption_candidate rule 4) -- never to reject the payload.
    A labelled row is somebody's, so the payload gets a row of its own instead;
    that is a different write, not a failed one.
    """
    asserted = asserted_vc_discriminators(data)
    return ", ".join(
        f"{field} {_vc_row_value(candidate, field)!r}"
        for field in _VC_DISCRIMINATORS
        if _vc_row_value(candidate, field) and field not in asserted
    )


def annotate_vc_member_counts(queryset):
    """
    Annotate a VirtualChassis queryset with its REAL member count.

    Not VirtualChassis.member_count: that is a utilities.counters cached
    counter, and a stale one is exactly the state this branch has already had
    to repair once (see applier._try_adopt_masterless_virtualchassis). An
    identity decision must not be taken from a field that can be wrong -- an
    empty row whose counter drifted upward would read as populated and a
    populated one whose counter drifted down as empty, in both cases turning a
    refusal into a write or the other way round.
    """
    return queryset.annotate(_diode_member_count=models.Count("members"))


def _vc_ambiguity_remedy(data: dict) -> str:
    """
    What the operator can actually do about two rows one name matched.

    Split by what the payload asserts, because the halves have different ways
    out: narrowing reads the payload, not the rows, so labelling a row settles
    nothing for a payload that asserts no discriminator. Offering it there is
    advice that cannot be followed.
    """
    fields = " or ".join(_VC_DISCRIMINATORS)
    placement = (
        "put the referencing device into the chassis it belongs to in NetBox -- "
        "creating it there if it does not exist yet -- because a reference from a "
        "device that is already a member resolves to that member's own chassis"
    )
    asserted = describe_vc_assertions(data)
    if asserted:
        return (
            f"It asserts {asserted}, which every one of those rows carries too, so it "
            f"does not tell them apart. Settle it in NetBox, with no change to what the "
            f"producer sends: change the {fields} of the rows this payload does NOT mean, "
            f"so that only one still carries the asserted value; or merge the duplicates "
            f"into one row; or {placement}."
        )
    return (
        f"The payload asserts nothing that tells them apart, and labelling these rows "
        f"cannot settle it on its own -- a {fields} narrows only the values a payload "
        f"asserts, and this one asserts none. Settle it in NetBox, with no change to "
        f"what the producer sends: merge the duplicates into one row; or {placement}. "
        f"Alternatively the producer starts sending a {fields}, and the row this payload "
        f"means is given that value -- both halves are needed, one alone changes nothing."
    )


def describe_vc_candidates(candidates) -> str:
    """
    One-line, id-bearing description of the rows a name matched.

    Every caller today passes rows from annotate_vc_member_counts, so the
    per-row fallback below does not run. It stays because this string is only
    ever built on the way to raising, and a describe() that raised AttributeError
    on an unannotated queryset would replace a reportable refusal with a 500 --
    the one failure mode this whole path exists to avoid.
    """
    parts = []
    for candidate in candidates:
        count = getattr(candidate, "_diode_member_count", None)
        if count is None:
            count = candidate.members.count()
        parts.append(
            f"id {candidate.pk} (domain {candidate.domain!r}, {count} member(s), "
            f"{'mastered' if candidate.master_id else 'masterless'})"
        )
    return "; ".join(parts)


def vc_hint_pks(member_hint) -> list:
    """
    The usable device pks in a VC_MEMBER_HINT, and nothing else.

    ``member_hint`` is common.VC_MEMBER_HINT as the transformer left it: a list
    holding a pk for every member device that was already resolved to an
    existing row, and an UnresolvedReference for every one this batch is still
    creating. A device being created belongs to nothing yet, so it carries no
    evidence -- which is why the hint is filtered by type here rather than
    assumed to be pks. A direct apply-change-set can also put anything at all
    in a payload, and none of it may reach an ORM pk filter.

    bool is excluded explicitly: it is an int subclass, so True would otherwise
    be read as pk 1 (the same hazard applier._coerce_pk documents) and let an
    unrelated device decide which chassis a reference resolves to.

    Split out as its own function because it is the whole of that guard, and a
    guard that can only be observed through a database whose pk sequence the
    test cannot control is a guard nothing pins -- see
    VirtualChassisHintPkTests.
    """
    if member_hint is None:
        return []
    if not isinstance(member_hint, list | tuple):
        member_hint = [member_hint]
    return [v for v in member_hint if isinstance(v, int) and not isinstance(v, bool)]


def vc_candidates_owning(candidates, member_hint) -> list:
    """Candidates that one of the referencing member Devices ALREADY belongs to."""
    pks = vc_hint_pks(member_hint)
    if not pks:
        return []
    device_model = get_object_type_model("dcim.device")
    owned = set(
        device_model.objects.filter(pk__in=pks, virtual_chassis__isnull=False)
        .values_list("virtual_chassis_id", flat=True)
    )
    return [c for c in candidates if c.pk in owned]


_LOGICAL_MATCHERS = {
    "dcim.cable": lambda: [
        CableTerminationSetMatcher(
            model_class=get_object_type_model("dcim.cable"),
            name="logical_cable_termination_set",
        )
    ],
    "dcim.macaddress": lambda: [
        ObjectMatchCriteria(
            fields=("mac_address", "assigned_object_type", "assigned_object_id"),
            name="logical_mac_address_within_parent",
            model_class=get_object_type_model("dcim.macaddress"),
            condition=Q(assigned_object_id__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("mac_address", "assigned_object_type", "assigned_object_id"),
            name="logical_mac_address_within_parent",
            model_class=get_object_type_model("dcim.macaddress"),
            condition=Q(assigned_object_id__isnull=True),
        ),
    ],
    "ipam.aggregate": lambda: [
        ObjectMatchCriteria(
            fields=("prefix",),
            name="logical_aggregate_prefix_no_rir",
            model_class=get_object_type_model("ipam.aggregate"),
            condition=Q(rir__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("prefix", "rir"),
            name="logical_aggregate_prefix_within_rir",
            model_class=get_object_type_model("ipam.aggregate"),
            condition=Q(rir__isnull=False),
        ),
    ],
    "ipam.ipaddress": lambda: [
        GlobalIPNetworkIPMatcher(
            ip_fields=("address",),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.ipaddress"),
            name="logical_ip_address_global_no_vrf",
        ),
        VRFIPNetworkIPMatcher(
            ip_fields=("address",),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.ipaddress"),
            name="logical_ip_address_within_vrf",
        ),
    ],
    "ipam.iprange": lambda: [
        GlobalIPNetworkIPMatcher(
            ip_fields=("start_address", "end_address"),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.iprange"),
            name="logical_ip_range_start_end_global_no_vrf",
        ),
        VRFIPNetworkIPMatcher(
            ip_fields=("start_address", "end_address"),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.iprange"),
            name="logical_ip_range_start_end_within_vrf",
        ),
    ],
    "ipam.prefix": lambda: [
         ObjectMatchCriteria(
            fields=("prefix",),
            name="logical_prefix_global_no_vrf",
            model_class=get_object_type_model("ipam.prefix"),
            condition=Q(vrf__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("prefix", "vrf"),
            name="logical_prefix_within_vrf",
            model_class=get_object_type_model("ipam.prefix"),
            condition=Q(vrf__isnull=False),
        ),
    ],
    "virtualization.cluster": lambda: [
        ObjectMatchCriteria(
            fields=("name", "scope_type", "scope_id"),
            name="logical_cluster_within_scope",
            model_class=get_object_type_model("virtualization.cluster"),
            condition=Q(scope_type__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_cluster_with_no_scope_or_group",
            model_class=get_object_type_model("virtualization.cluster"),
            condition=Q(scope_type__isnull=True, group__isnull=True),
        ),
    ],
    "ipam.vlan": lambda: [
        ObjectMatchCriteria(
            fields=("vid",),
            name="logical_vlan_vid_no_group_or_svlan_or_site",
            model_class=get_object_type_model("ipam.vlan"),
            condition=Q(group__isnull=True, qinq_svlan__isnull=True, site__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("vid", "site"),
            name="logical_vlan_in_site",
            model_class=get_object_type_model("ipam.vlan"),
            condition=Q(group__isnull=True, qinq_svlan__isnull=True, site__isnull=False),
        ),
    ],
    "ipam.vlangroup": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_vlan_group_name_no_scope",
            model_class=get_object_type_model("ipam.vlangroup"),
            condition=Q(scope_type__isnull=True),
        ),
    ],
    "ipam.vrf": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_vrf_name_no_tenant",
            model_class=get_object_type_model("ipam.vrf"),
            condition=Q(rd__isnull=True, tenant__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("name", "tenant"),
            name="logical_vrf_name_within_tenant",
            model_class=get_object_type_model("ipam.vrf"),
            condition=Q(rd__isnull=True, tenant__isnull=False),
        ),
    ],
    "wireless.wirelesslan": lambda: [
        ObjectMatchCriteria(
            fields=("ssid",),
            name="logical_wireless_lan_ssid_no_group_or_vlan",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(group__isnull=True, vlan__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("ssid", "group"),
            name="logical_wireless_lan_ssid_in_group",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(group__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("ssid", "vlan"),
            name="logical_wireless_lan_ssid_in_vlan",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(vlan__isnull=False),
        ),
    ],
    "virtualization.virtualmachine": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_virtual_machine_name_no_cluster",
            model_class=get_object_type_model("virtualization.virtualmachine"),
            condition=Q(cluster__isnull=True),
        ),
    ],
    "dcim.virtualchassis": lambda: [
        VirtualChassisNameMatcher(
            model_class=get_object_type_model("dcim.virtualchassis"),
            name="logical_vc_name_no_master",
        )
    ],
    "ipam.service": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_service_name_no_device_or_vm",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(device__isnull=True, virtual_machine__isnull=True),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_service_name_on_device",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(device__isnull=False),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "virtual_machine"),
            name="logical_service_name_on_vm",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(virtual_machine__isnull=False),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "parent_object_type", "parent_object_id"),
            name="logical_service_name_on_parent",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(parent_object_type__isnull=False),
            min_version="4.3.0"
        ),
    ],
    "dcim.modulebay": lambda: [
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_module_bay_name_on_device",
            model_class=get_object_type_model("dcim.modulebay"),
        )
    ],
    "dcim.inventoryitem": lambda: [
        # TODO: this may be handleable by the existing constraints.
        # we ignore it due to null values for parent but could have
        # better coverage of this case perhaps.
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_inventory_item_name_on_device_no_parent",
            model_class=get_object_type_model("dcim.inventoryitem"),
            condition=Q(parent__isnull=True),
        )
    ],
    "ipam.fhrpgroup": lambda: [
        ObjectMatchCriteria(
            fields=("group_id",),
            name="logical_fhrp_group_id",
            model_class=get_object_type_model("ipam.fhrpgroup"),
        )
    ],
    "tenancy.contact": lambda: [
        ObjectMatchCriteria(
            # contacts are unconstrained in 4.3.0
            # in 4.2 they are constrained by unique name per group
            fields=("name", ),
            name="logical_contact_name",
            model_class=get_object_type_model("tenancy.contact"),
            min_version="4.3.0",
        )
    ],
    "dcim.devicerole": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_device_role_name_no_parent",
            model_class=get_object_type_model("dcim.devicerole"),
            condition=Q(parent__isnull=True),
            min_version="4.3.0",
        ),
        ObjectMatchCriteria(
            fields=("slug",),
            name="logical_device_role_slug_no_parent",
            model_class=get_object_type_model("dcim.devicerole"),
            condition=Q(parent__isnull=True),
            min_version="4.3.0",
        )
    ],
    "extras.journalentry": lambda: [
        ObjectMatchCriteria(
            fields=("assigned_object_id", "assigned_object_type", "comments"),
            name="logical_journal_entry_assigned_object_comments",
            model_class=get_object_type_model("extras.journalentry"),
        )
    ],
}

@dataclass
class ObjectMatchCriteria:
    """
    Defines criteria for identifying a specific object.

    This matcher expects a fully 'transformed' and resolved
    set of fields. ie field names are snake case and match
    the model fields and any references to another object
    specify a specific id in the appropriate field name.
    eg device_id=123 etc and for any generic references,
    both the type and id should be specified, eg:
    scope_type="dcim.site" and scope_id=123
    """

    fields: tuple[str] | None = None
    expressions: tuple | None = None
    condition: Q | None = None
    model_class: type[models.Model] | None = None
    name: str | None = None

    min_version: str | None = None
    max_version: str | None = None

    def __hash__(self):
        """Hash the object match criteria."""
        return hash((self.fields, self.expressions, self.condition, self.model_class.__name__, self.name))

    def has_required_fields(self, data) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self._get_refs())

    @cache
    def _get_refs(self) -> set[str]:
        """Returns a set of all field names referenced by the constraint."""
        refs = set()
        if self.fields:
            refs.update(self.fields)
        elif self.expressions:
            for expr in self.expressions:
                refs |= _get_refs(expr)
        return frozenset(refs)

    @cache
    def _get_insensitive_refs(self) -> set[str]:
        """
        Returns a set of all field names that should be compared in a case insensitive manner.

        best effort, doesn't handle things being nested in a complex way.
        """
        refs = set()
        if self.expressions:
            for expr in self.expressions:
                # TODO be more careful here
                if expr.__class__.__name__ == "Lower":
                    for source_expr in getattr(expr, "source_expressions", []):
                        if hasattr(source_expr, "name"):
                            refs.add(source_expr.name)
        return refs

    def fingerprint(self, data: dict) -> str|None:
        """
        Returns a fingerprint of the data based on these criteria.

        These criteria that can be used to determine if two
        data structs roughly match.

        This is a best effort based on the referenced fields
        and some interrogation of case sensitivity. The
        real criteria are potentially complex...
        """
        if not self.has_required_fields(data):
            return None

        if self.condition:
            if not self._check_condition(data):
                return None

        # sort the fields by name
        sorted_fields = sorted(self._get_refs())
        insensitive = self._get_insensitive_refs()
        values = []
        for field in sorted_fields:
            value = data[field]
            if isinstance(value, dict):
                logger.warning(f"unexpected value type for fingerprinting: {value}")
                return None
            if field in insensitive and value is not None:
                value = value.lower()
            values.append(value)

        if values and all(v is None for v in values):
            return None

        return hash((self.model_class.__name__, self.name, tuple(values)))

    def _check_condition(self, data) -> bool:
        return self._check_condition_1(data, self.condition)

    def _check_condition_1(self, data, condition) -> bool:
        if condition is None:
            return True
        if isinstance(condition, tuple):
            return self._check_simple_condition(data, condition)

        if hasattr(condition, "connector") and condition.connector == Q.AND:
            result = True
            for child in condition.children:
                if not self._check_condition_1(data, child):
                    result = False
                    break
            if condition.negated:
                return not result
            return result
        # TODO handle OR ?
        logger.warning(f"Unhandled condition {condition}")
        return False

    def _check_simple_condition(self, data, condition) -> bool:
        if condition is None:
            return True

        k, v = condition
        result = False
        if k.endswith("__isnull"):
            k = k[:-8]
            is_null = k not in data or data[k] is None
            result = is_null == v
        else:
            result = k in data and data[k] == v

        return result

    def build_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        if self.fields and len(self.fields) > 0:
            return self._build_fields_queryset(data)
        if self.expressions and len(self.expressions) > 0:
            return self._build_expressions_queryset(data)
        raise ValueError("No fields or expressions to build queryset from")

    def _build_fields_queryset(self, data) -> models.QuerySet: # noqa: C901
        """Builds a queryset for a simple set-of-fields constraint."""
        if not self._check_condition(data):
            return None

        # A lookup whose referenced values are ALL null can only "match" via
        # IS NULL on every field, binding an arbitrary row (e.g. a null
        # asset_tag adopting an unrelated module). Partial nulls keep the
        # long-standing clear-FK dedupe: Django renders =None as IS NULL.
        if all(data.get(field_name) is None for field_name in self.fields):
            return None

        data = self._prepare_data(data)
        lookup_kwargs = {}
        for field_name in self.fields:
            field = self.model_class._meta.get_field(field_name)
            if field_name not in data:
                return None  # cannot match, missing field data
            lookup_value = data.get(field_name)
            if isinstance(lookup_value, UnresolvedReference):
                return None  # cannot match, missing field data
            if isinstance(lookup_value, dict):
                return None  # cannot match, missing field data
            lookup_kwargs[field.name] = lookup_value

        qs = self.model_class.objects.filter(**lookup_kwargs)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

    def _build_expressions_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        # Same all-None skip as the fields path, using the cached ref set;
        # raw data is checked so both guards read identical inputs.
        refs = self._get_refs()
        if refs and all(data.get(r) is None for r in refs):
            return None

        data = self._prepare_data(data)

        replacements = {
            F(field): Value(value) if isinstance(value, str | int | float | bool) else value
            for field, value in data.items()
        }

        filters = []
        for expr in self.expressions:
            if hasattr(expr, "get_expression_for_validation"):
                expr = expr.get_expression_for_validation()

            refs = [F(ref) for ref in _get_refs(expr)]
            for ref in refs:
                if ref not in replacements:
                    return None  # cannot match, missing field data
                if isinstance(replacements[ref], UnresolvedReference):
                    return None  # cannot match, missing field data

            rhs = expr.replace_expressions(replacements)
            condition = Exact(expr, rhs)
            filters.append(condition)

        qs = self.model_class.objects.filter(*filters)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

    def _prepare_data(self, data: dict) -> dict:
        prepared = {}
        for field_name, value in data.items():
            try:
                field = self.model_class._meta.get_field(field_name)
                # special handling for object type -> content type id
                if field.is_relation and hasattr(field, "related_model") and field.related_model == ContentType:
                    # Handle ManyToMany fields (list of object types) and ForeignKey fields (single object type)
                    if isinstance(value, list):
                        prepared[field_name] = [
                            content_type_id(v) if v is not None else None for v in value
                        ]
                    else:
                        prepared[field_name] = content_type_id(value) if value is not None else None
                else:
                    prepared[field_name] = value

            except FieldDoesNotExist:
                continue
        return prepared



@dataclass
class CustomFieldMatcher:
    """A matcher for a unique custom field."""

    name: str
    custom_field: str
    model_class: type[models.Model]

    min_version: str | None = None
    max_version: str | None = None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        value = data.get("custom_fields", {}).get(self.custom_field)
        if value is None:
            return None

        return hash((self.model_class.__name__, self.name, value))

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        value = data.get("custom_fields", {}).get(self.custom_field)
        if value is None:
            return None

        return self.model_class.objects.filter(**{f'custom_field_data__{self.custom_field}': value})

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return self.custom_field in data.get("custom_fields", {})


@dataclass
class GlobalIPNetworkIPMatcher:
    """A matcher that ignores the mask."""

    ip_fields: tuple[str]
    vrf_field: str
    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def _check_condition(self, data: dict) -> bool:
        """Check the condition for the custom field."""
        return data.get(self.vrf_field, None) is None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        values = []
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            values.append(value)

        return hash((self.model_class.__name__, self.name, tuple(values)))

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self.ip_fields)

    def ip_value(self, data: dict, field: str) -> str|None:
        """Get the IP value from the data."""
        value = data.get(field)
        if value is None:
            return None
        return _ip_only(value)

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        filter = {
            f'{self.vrf_field}__isnull': True,
        }
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            filter[f'{field}__net_host'] = value

        return self.model_class.objects.filter(**filter)


@dataclass
class VirtualChassisNameMatcher:
    """
    Best-effort VirtualChassis matcher: by name, only when the payload has no master.

    VirtualChassis has no unique constraint besides master (names may
    legitimately duplicate), so this is a fallback: a payload that carries a
    master keeps resolving through the auto-derived unique_master matcher.
    The DB row's own master is deliberately NOT filtered on — a masterless
    payload must bind a mastered row.

    A name that matches SEVERAL rows is not resolved by creation order; see
    ``resolve``. That is the whole of this matcher's identity policy and it is
    why this class carries a resolve() hook at all -- the framework's default,
    order_by('pk').first(), is deterministic but determinism is not identity.
    """

    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def has_required_fields(self, data: dict) -> bool:
        """True when the payload carries a usable name and no master."""
        name = data.get("name")
        return isinstance(name, str) and bool(name) and data.get("master") is None

    def fingerprint(self, data: dict) -> int | None:
        """
        Name-keyed fingerprint, deliberately NOT gated on master.

        Within one transform batch the members' name-only VC node and the
        master-bearing VC node must dedupe-merge into a single create; gating
        this on master absence would leave two nodes and a split chassis.

        Adding master (or domain) to this KEY is not the way to keep two
        genuinely different same-named stacks apart either, for the same reason:
        it separates the name-only node from the master-bearing one and
        reintroduces the split. Hash equality cannot express "the same stack
        unless conflicting identity is asserted", so that discrimination happens
        after grouping by name, in partition_vc_identities -- which is where the
        cost of this name-only key is paid, not here.
        """
        name = data.get("name")
        if not isinstance(name, str) or not name:
            return None
        return hash((self.model_class.__name__, self.name, name))

    def build_queryset(self, data: dict) -> models.QuerySet | None:
        """Queryset over all VCs with this name, mastered or not."""
        if not self.has_required_fields(data):
            return None
        return self.model_class.objects.filter(name=data["name"])

    def resolve(self, queryset: models.QuerySet, data: dict):
        """
        Choose among same-named candidates, or refuse to choose.

        find_existing_object's default is order_by('pk').first(): the oldest
        row wins. For a name-keyed match on a model with no uniqueness on name
        that is a policy, not a lookup -- and the policy is unsafe. Two
        legitimately distinct stacks may both be called "access-stack", and the
        reference being resolved is usually a MEMBER DEVICE's
        virtual_chassis, so picking the older row does not merely "match
        something": it plans that device into the other stack. A non-unique
        name is evidence, not permission.

        The rules, in order, and each is a fact about the rows rather than a
        tie-break over them:

        0. exactly one candidate -> resolve it. This is the ordinary case and
           the feature this matcher exists for; nothing below may weaken it.
        1. a member device that referenced this node ALREADY belongs to one of
           the candidates -> that one. The database already answered the
           question, and honouring it is the one rule that guarantees no
           device is relocated. When two referencing members disagree (each
           already in a DIFFERENT candidate) there is no single answer and the
           payload is describing a merge of two stacks -- refuse.
        2. the payload ASSERTS a discriminator (_VC_DISCRIMINATORS) -> keep
           only candidates carrying that value (narrow_vc_candidates, shared
           with the applier's adoption). Exactly one left resolves; NONE left
           means the payload names a chassis that does not exist yet, so return
           None and let it be created rather than binding a row whose
           discriminator contradicts the payload. An explicitly empty
           ``domain: ""`` is an assertion like any other and excludes the rows
           that DO carry a domain -- it used to be dropped, which let a payload
           declaring no domain bind a domain-bearing row and then write "" over
           the very field the refusal below points at.
        3. exactly one candidate has a master or members and every other is
           empty -> that one. This is the recovery path for duplicates an
           earlier bug created: an empty row is not a stack anyone owns, so
           preferring the real one is a repair, and it cannot relocate a
           device that is already placed (rule 1 outranks it).
        4. otherwise -> AmbiguousObjectMatch, naming the rows and the remedies
           that would actually resolve them (_vc_ambiguity_remedy: what those
           are depends on whether the payload asserts a discriminator at all).

        Rule 3 is the one that could still place a device into a stack it was
        not in (a device in no chassis, or in a chassis with another name,
        joining the sole populated candidate). It is bounded on purpose: it
        needs every OTHER same-named row to be empty AND masterless, which is
        the signature of bug-created duplicates and not of two real stacks --
        two real stacks both have members, which lands on rule 4.

        Rule 0 yields to ONE thing: a non-empty discriminator the row does not
        carry (contradicting_vc_discriminator). A payload asserting
        ``domain: "building-b"`` against a lone row carrying "building-a" is
        saying "not this row", and it has to mean that whether the name matched
        one row or twenty -- otherwise domain is a discriminator when several
        rows exist and a mutable field to be overwritten when one does, and
        which one it is depends on the database's history rather than on the
        payload. Measured with the old exemption: that payload bound the row,
        applied, and then re-planned ``domain: "building-b"`` onto it on every
        subsequent pass, forever. A successful apply that never converges is
        not a contract worth keeping.

        An EMPTY assertion still does not overrule rule 0: a name matching
        exactly one row resolves to it even when the payload asserts
        ``domain: ""`` and the row carries a domain. Excluding there would
        answer "no match" for the ordinary case and insert a duplicate chassis,
        which is the outcome this matcher exists to prevent; the "" then reaches
        that row as a field write like any other field the payload carries. That
        asymmetry is the same one narrow_vc_candidates draws -- "" narrows, but
        it never identifies, because every row that never set a domain has it.

        Adoption in the applier decides the opposite way for the same input
        because it has a lossless alternative -- the CREATE its plan already
        asked for (see _choose_adoption_candidate).
        """
        candidates = list(annotate_vc_member_counts(queryset).order_by("pk"))
        if not candidates:
            return None

        # A NON-EMPTY asserted discriminator speaks FIRST, before the rules
        # below -- including before existing membership. It is the payload
        # saying "not that row", and it has to mean that whether the name
        # matched one row or twenty; an earlier revision applied it only to the
        # single-candidate case, and the member hint then short-circuited it.
        # Measured with two rows "a" and "b" and a device already in "a" whose
        # payload asserts domain "b": membership answered "a", so the plan
        # UPDATED a's domain to "b" and left the device where it was --
        # overwriting the discriminator that identifies that row instead of
        # performing the move the payload asked for. Same when the asserted
        # domain matches no row at all.
        #
        # Excluding here leaves membership to choose among the rows that are
        # still COMPATIBLE, which is what it is good for. An EMPTY assertion is
        # not applied here: `domain: ""` narrows but never identifies, so it
        # stays with the ordinary narrowing below, where it cannot turn the
        # single-row case into a duplicate insert.
        compatible = [c for c in candidates if not contradicting_vc_discriminator(c, data)]
        if not compatible:
            # The payload describes a chassis none of these rows is. Create it
            # rather than bind a row it has just contradicted and re-plan the
            # difference forever.
            return None
        candidates = compatible

        if len(candidates) == 1:
            return candidates[0]

        name = data.get("name")
        owned = vc_candidates_owning(candidates, data.get(VC_MEMBER_HINT))
        if len(owned) == 1:
            return owned[0]
        if len(owned) > 1:
            raise AmbiguousObjectMatch(
                f"Ambiguous dcim.virtualchassis reference {name!r}: the member devices "
                f"named in this payload already belong to DIFFERENT virtual chassis with "
                f"that name -- {describe_vc_candidates(owned)}. Refusing to choose one, "
                f"because binding either would move devices out of the other. Ingest "
                f"those devices in separate requests, so each keeps the chassis it is "
                f"already in; or merge the two chassis into one in NetBox first.",
                "dcim.virtualchassis", "name",
            )

        candidates, contradicted, _identified = narrow_vc_candidates(candidates, data)
        if contradicted:
            return None
        if len(candidates) == 1:
            return candidates[0]

        populated = [
            c for c in candidates
            if c.master_id is not None or c._diode_member_count
        ]
        if len(populated) == 1:
            return populated[0]

        raise AmbiguousObjectMatch(
            f"Ambiguous dcim.virtualchassis reference {name!r}: "
            f"{len(candidates)} existing virtual chassis named {name!r} are equally "
            f"consistent with this payload -- {describe_vc_candidates(candidates)}. "
            f"VirtualChassis.name is not unique in NetBox, so choosing would move devices "
            f"into a chassis this payload never identified. "
            + _vc_ambiguity_remedy(data),
            "dcim.virtualchassis", "name",
        )


@dataclass
class CableTerminationSetMatcher:
    """
    Match a Cable by its canonical set of terminations.

    Cable has no DB unique constraint; identity is the sorted set of
    (object_type, logical-id) termination tuples across BOTH ends, with
    A/B and within-end order insignificant. ObjectMatchCriteria is
    scalar-field-only and cannot express a related-row set match.
    """

    model_class: type[models.Model]
    name: str
    a_field: str = "a_terminations"
    b_field: str = "b_terminations"

    min_version: str | None = None
    max_version: str | None = None

    def has_required_fields(self, data: dict) -> bool:
        """Both termination ends present and non-empty."""
        a = data.get(self.a_field)
        b = data.get(self.b_field)
        return bool(a) and bool(b) and isinstance(a, list) and isinstance(b, list)

    def _reduce(self, term: dict):
        """
        Reduce one termination dict to a hashable (object_type, logical_id) tuple.

        logical_id is the resolved pk (int) when available; at transform time
        object_id is an UnresolvedReference -> reduce to ("__uuid__", uuid)
        (best-effort in-batch dedup only). Returns None if the item is not a
        dict / lacks the expected keys.
        """
        if not isinstance(term, dict):
            return None
        object_type = term.get("object_type")
        object_id = term.get("object_id")
        if object_type is None or object_id is None:
            return None
        if isinstance(object_id, UnresolvedReference):
            logical_id = ("__uuid__", object_id.uuid)
        elif isinstance(object_id, int):
            logical_id = object_id
        else:
            # stringified ref or unexpected type -- fold to a stable string
            logical_id = ("__str__", str(object_id))
        return (object_type, logical_id)

    def _reduced_set(self, data: dict):
        """Union of A+B reduced tuples, or None if any item is unreducible."""
        reduced = []
        for field in (self.a_field, self.b_field):
            for term in data.get(field, []):
                r = self._reduce(term)
                if r is None:
                    return None
                reduced.append(r)
        return reduced

    def fingerprint(self, data: dict) -> int | None:
        """
        Order-insensitive hash over the union of A+B reduced tuples.

        Best-effort in-batch dedup: may be computed over uuids when terminations
        are unresolved. Authoritative matching is build_queryset.
        """
        if not self.has_required_fields(data):
            return None
        reduced = self._reduced_set(data)
        if reduced is None:
            return None
        return hash(
            (self.model_class.__name__, self.name, tuple(sorted(reduced)))
        )

    def build_queryset(self, data: dict) -> models.QuerySet | None:
        """
        Authoritative exact-set match over real CableTermination rows.

        Annotate the termination count per Cable, require count == requested
        count, then require every requested (termination_type ct_id,
        termination_id pk) is present. Rejects subset/superset. Returns None if
        any object_id is still unresolved (cannot authoritatively match).
        """
        if not self.has_required_fields(data):
            return None

        pairs = []  # (content_type_id, pk)
        for field in (self.a_field, self.b_field):
            for term in data.get(field, []):
                if not isinstance(term, dict):
                    return None
                object_id = term.get("object_id")
                object_type = term.get("object_type")
                if not isinstance(object_id, int) or object_type is None:
                    return None  # unresolved -> cannot authoritatively match
                pairs.append((content_type_id(object_type), object_id))

        if not pairs:
            return None

        # A cable cannot terminate the same object twice (CableTermination has
        # a unique (termination_type, termination_id) constraint). A duplicate
        # pair would both inflate len(pairs) and be satisfiable by one shared
        # termination row (each filter is a separate join), letting an invalid
        # payload like A:[if1] B:[if1] false-match a larger cable containing
        # if1. Not authoritatively matchable -> let the serializer reject it.
        if len(set(pairs)) != len(pairs):
            return None

        qs = self.model_class.objects.annotate(
            _term_count=models.Count("terminations")
        ).filter(_term_count=len(pairs))
        for ct_id, pk in pairs:
            qs = qs.filter(
                terminations__termination_type_id=ct_id,
                terminations__termination_id=pk,
            )
        return qs.distinct()


@dataclass
class VRFIPNetworkIPMatcher:
    """Matches ip in a vrf, ignores mask."""

    ip_fields: tuple[str]
    vrf_field: str
    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def _check_condition(self, data: dict) -> bool:
        """Check the condition for the custom field."""
        return data.get(self.vrf_field, None) is not None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        values = []
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            values.append(value)

        vrf_id = data[self.vrf_field]

        return hash((self.model_class.__name__, self.name, tuple(values), vrf_id))

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self.ip_fields) and self.vrf_field in data

    def ip_value(self, data: dict, field: str) -> str|None:
        """Get the IP value from the data."""
        value = data.get(field)
        if value is None:
            return None
        return _ip_only(value)

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        filter = {}
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            filter[f'{field}__net_host'] = value

        vrf_id = data[self.vrf_field]
        if isinstance(vrf_id, UnresolvedReference):
            return None
        filter[f'{self.vrf_field}'] = vrf_id

        return self.model_class.objects.filter(**filter)


def _ip_only(value: str) -> str|None:
    try:
        ip = netaddr.IPNetwork(value)
        value = ip.ip
    except netaddr.core.AddrFormatError:
        return None

    return value

@dataclass
class AutoSlugMatcher:
    """A special matcher that tries to match on auto generated slugs."""

    name: str
    slug_field: str
    model_class: type[models.Model]

    min_version: str | None = None
    max_version: str | None = None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        slug = data.get('_auto_slug', None)
        if slug is None:
            return None

        return hash((self.model_class.__name__, self.name, slug.value))

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        slug = data.get('_auto_slug', None)
        if slug is None:
            return None

        return self.model_class.objects.filter(**{f'{self.slug_field}': str(slug.value)})

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return '_auto_slug' in data


@lru_cache(maxsize=256)
def _get_custom_field_matchers(model_class) -> tuple:
    """Get matchers for unique custom fields (cached)."""
    if not hasattr(model_class, "get_custom_fields"):
        return ()
    unique_custom_fields = CustomField.objects.get_for_model(model_class).filter(unique=True)
    if not unique_custom_fields:
        return ()
    return tuple(
        CustomFieldMatcher(
            model_class=model_class,
            custom_field=cf.name,
            name=f"unique_custom_field_{cf.name}",
        )
        for cf in unique_custom_fields
    )


def _on_custom_field_change(**kwargs):
    _get_custom_field_matchers.cache_clear()


post_save.connect(_on_custom_field_change, sender=CustomField)
post_delete.connect(_on_custom_field_change, sender=CustomField)


def get_model_matchers(model_class) -> list:
    """Extract unique constraints from a Django model."""
    matchers = []
    matchers += _get_model_matchers(model_class)
    matchers += _get_custom_field_matchers(model_class)
    matchers += _get_autoslug_matchers(model_class)
    return matchers

@lru_cache(maxsize=256)
def _get_autoslug_matchers(model_class) -> list:
    matchers = []
    for field in model_class._meta.fields:
        if isinstance(field, SlugField):
            matchers.append(
                AutoSlugMatcher(
                    model_class=model_class,
                    slug_field=field.name,
                    name=f"unique_autoslug_{field.name}",
                )
            )
            break
    return matchers

@lru_cache(maxsize=256)
def _get_model_matchers(model_class) -> list[ObjectMatchCriteria]:
    object_type = get_object_type(model_class)
    matchers = [
        x for x in _LOGICAL_MATCHERS.get(object_type, lambda: [])()
        if in_version_range(x.min_version, x.max_version)
    ]

    # collect single fields that are unique
    for field in model_class._meta.fields:
        if field.name == "id":
            # TODO(ltucker): more django-general detection of pk field?
            continue

        if field.unique:
            matchers.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    fields=(field.name,),
                    name=f"unique_{field.name}",
                )
            )

    # collect UniqueConstraint constraints
    for constraint in model_class._meta.constraints:
        if not _is_supported_constraint(constraint, model_class):
            continue
        if len(constraint.fields) > 0:
            matchers.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    fields=tuple(constraint.fields),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        elif len(constraint.expressions) > 0:
            matchers.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    expressions=tuple(constraint.expressions),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        else:
            logger.debug(
                f"Constraint {constraint.name} on {model_class.__name__} had no fields or expressions (skipped)"
            )
            # (this shouldn't happen / enforced by django)
            continue

    return matchers


def _is_supported_constraint(constraint, model_class) -> bool:
    if not isinstance(constraint, models.UniqueConstraint):
        return False

    if len(constraint.opclasses) > 0:
        logger.warning(f"Constraint {constraint.name} on {model_class.__name__} had opclasses (skipped)")
        return False

    if constraint.nulls_distinct is not None and constraint.nulls_distinct is True:
        logger.warning(f"Constraint {constraint.name} on {model_class.__name__} had nulls_distinct (skipped)")
        return False

    for field_name in constraint.fields:
        field = model_class._meta.get_field(field_name)
        if field.generated:
            logger.warning(
                f"Constraint {constraint.name} on {model_class.__name__} had"
                f" generated field {field_name} (skipped)"
            )
            return False

    return True

def _get_refs(expr) -> set[str]:
    refs = set()
    if isinstance(expr, str):
        refs.add(expr)
    elif isinstance(expr, F):
        refs.add(expr.name)
    elif hasattr(expr, "get_source_expressions"):
        for subexpr in expr.get_source_expressions():
            refs |= _get_refs(subexpr)
    else:
        logger.warning(f"Unhandled expression type for _get_refs: {type(expr)}")
    return refs

def _fingerprint_all(data: dict, object_type: str|None = None) -> str:
    """
    Returns a fingerprint of the data based on all fields.

    Data should be a (flattened) dictionary of field values.
    This ignores any fields that start with an underscore.
    """
    if data is None:
        return None

    try:
        values = ["object_type", object_type]
        for k, v in sorted(data.items()):
            if k.startswith("_"):
                continue
            values.append(k)
            if isinstance(v, list | tuple):
                values.extend(sorted(_as_tuples(v)))
            elif isinstance(v, dict):
                values.append(_fingerprint_all(v))
            else:
                values.append(v)

        return hash(tuple(values))
    except Exception as e:
        logger.error(f"Error fingerprinting data: {e}")
        raise

def _as_tuples(vs):
    if isinstance(vs, list):
        return tuple(_as_tuples(v) for v in vs)
    if isinstance(vs, dict):
        return tuple((k, _as_tuples(v)) for k, v in vs.items())
    return vs

def fingerprints(data: dict, object_type: str) -> list[str]:
    """
    Get fingerprints for a data structure.

    This returns all fingerprints for the given data that
    have required fields.
    """
    if data is None:
        return None

    model_class = get_object_type_model(object_type)
    # check any known match criteria
    fps = []
    for matcher in get_model_matchers(model_class):
        fp = matcher.fingerprint(data)
        if fp is not None:
            fps.append(fp)
    fp = _fingerprint_all(data, object_type)
    fps.append(fp)
    return fps

def _find_obj_cache_key(data: dict, object_type: str) -> str | None:
    """
    Build a deterministic cache key from entity lookup data.

    Includes only simple scalar fields. Entities whose identity depends
    solely on unresolved references (no scalar fields at all) are not
    cacheable.
    """
    if object_type == "dcim.cable":
        # Cable identity lives entirely in list fields skipped by the scalar-only
        # cache key; always run the authoritative build_queryset matcher loop.
        return None

    if object_type == "dcim.virtualchassis":
        # Not cacheable, and the reason is identity rather than cost. The key
        # below is built from SCALAR fields only, so it cannot see
        # common.VC_MEMBER_HINT (a list, and private) -- two payloads naming the
        # same chassis on behalf of DIFFERENT member devices would share one
        # key, and the first one's answer would be served to the second, which
        # is precisely the "pick a row the payload never identified" the
        # VirtualChassisNameMatcher.resolve rules exist to prevent. A cached hit
        # would also skip resolve() entirely, so an ambiguity that must be
        # reported would instead answer with whatever row was cached before the
        # duplicate appeared. Both caches (django and request-scoped) key off
        # this function, so returning None disables both.
        return None

    items = []
    for k, v in sorted(data.items()):
        if k.startswith("_"):
            continue
        if isinstance(v, UnresolvedReference):
            items.append((k, f"__unresolved__:{v.object_type}"))
        elif isinstance(v, (dict, list)):
            continue  # skip complex nested data, not used by matchers
        else:
            items.append((k, str(v)))

    if not items:
        return None

    branch_schema = _get_active_branch_schema()
    if branch_schema:
        raw = f"{branch_schema}:{object_type}:{items}"
    else:
        raw = f"{object_type}:{items}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return f"diode:fobj:{key_hash}"


def find_existing_object(data: dict, object_type: str): # noqa: C901
    """
    Find an existing object that matches the given data.

    Uses all object match criteria to look for an existing
    object. Returns the first match found.

    Returns the object if found, otherwise None.
    """
    ctx = get_profile_ctx()
    start = time.monotonic() if ctx else None
    queries_before = ctx.db_query_snapshot() if ctx else 0
    matchers_checked = 0
    result = None
    cache_hit = False

    model_class = get_object_type_model(object_type)
    cache_ttl = _get_find_obj_cache_ttl()
    cache_key = _find_obj_cache_key(data, object_type) if cache_ttl > 0 else None

    req_cache = _request_obj_cache.get(None)
    if req_cache is not None and cache_key is not None and cache_key in req_cache:
        cached = req_cache[cache_key]
        if ctx:
            ctx.record_timing("find_obj", (time.monotonic() - start) * 1000)
            ctx.increment("find_obj_found")
            ctx.increment("find_obj_req_cache_hit")
        return cached

    if cache_key:
        cached_id = django_cache.get(cache_key)
        if cached_id is not None:
            cache_hit = True
            result = model_class.objects.filter(pk=cached_id).first()
            if result is None:
                # Object deleted since cached — clean up and fall through
                cache_hit = False
                django_cache.delete(cache_key)
                django_cache.delete(_find_obj_rev_key(object_type, cached_id))

    if not cache_hit:
        for matcher in get_model_matchers(model_class):
            if not matcher.has_required_fields(data):
                continue
            q = matcher.build_queryset(data)
            if q is None:
                continue
            matchers_checked += 1
            # A matcher may own the choice among the rows its queryset returned
            # (VirtualChassisNameMatcher.resolve). The default remains the
            # oldest row, which is right wherever the criteria carry real
            # uniqueness -- there is only ever one row to pick.
            resolve = getattr(matcher, "resolve", None)
            if resolve is not None:
                existing = resolve(q, data)
            else:
                existing = q.order_by('pk').first()
            if existing is not None:
                result = existing
                break

        if cache_key and result is not None:
            django_cache.set(cache_key, result.id, cache_ttl)
            django_cache.set(_find_obj_rev_key(object_type, result.id), cache_key, cache_ttl)

    if req_cache is not None and cache_key is not None and result is not None:
        req_cache[cache_key] = result

    if ctx:
        ctx.record_timing("find_obj", (time.monotonic() - start) * 1000)
        ctx.increment("find_obj_matchers_checked", matchers_checked)
        ctx.increment("find_obj_queries", ctx.db_query_snapshot() - queries_before)
        ctx.increment("find_obj_found" if result else "find_obj_not_found")
        if cache_key is not None:
            ctx.increment("find_obj_cache_hit" if cache_hit else "find_obj_cache_miss")
    return result
