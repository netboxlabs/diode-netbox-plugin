#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Driver field policy."""

import logging
from functools import lru_cache

from dcim.choices import InterfaceTypeChoices
from dcim.constants import WIRELESS_IFACE_TYPES
from django.db.models import Q

from .common import UnresolvedReference
from .matcher import get_model_matchers
from .plugin_utils import get_json_ref_info, get_object_type_model

logger = logging.getLogger(__name__)

# --- Driver field policy -----------------------------------------------------
# A few NetBox fields are legal only for particular values of another field on the
# same object: a "driver" field whose value forbids a set of "dependent" fields.
# NetBox enforces that at the model (Interface.clean() / VLAN.clean()) and, for
# dcim.interface, in the serializer's validate() — and where it enforces it, it
# rejects the WHOLE entity rather than ignoring the surplus field.
#
# object_type -> driver_field -> { driver_value : [dependent fields forbidden] }
# Values verified against NetBox model clean() / serializer validate()
# (v4.4.10 / v4.5.5 / v4.6.0).
_INTERFACE_MODE_VLAN_RULES = {
    "": ["untagged_vlan", "tagged_vlans", "qinq_svlan"],  # routed / no 802.1Q
    "access": ["tagged_vlans", "qinq_svlan"],
    "tagged": ["qinq_svlan"],
    "tagged-all": ["tagged_vlans", "qinq_svlan"],
    "q-in-q": [],
}

# rf_channel / rf_channel_frequency / rf_channel_width / rf_role may be set only on
# wireless interface types (Interface.clean(); is_wireless == type in WIRELESS_IFACE_TYPES).
# Keyed on every non-wireless type (complement of the wireless allow-set) so a type
# change away from wireless clears the stale rf fields.
_RF_FIELDS = ["rf_channel", "rf_channel_frequency", "rf_channel_width", "rf_role"]
_INTERFACE_TYPE_RF_RULES = {
    t: _RF_FIELDS for t in InterfaceTypeChoices.values() if t not in WIRELESS_IFACE_TYPES
}

# ipam.vlan: qinq_svlan may be set only on a Q-in-Q customer VLAN (qinq_role == 'cvlan');
# VLAN.clean() rejects a stale qinq_svlan for any other role. (The reciprocal
# "customer VLAN requires an svlan" is a presence rule, not ours -> 'cvlan' maps to [].)
_VLAN_QINQ_RULES = {
    "": ["qinq_svlan"],
    "svlan": ["qinq_svlan"],
    "cvlan": [],
}

# One registry, applied uniformly: the rules encode NetBox's data model, not which
# serializer happens to police it, and NetBox rejects the WHOLE entity wherever it
# does police it. See the two phases below for what "applied" means on each side.
#
# Measured cost of that uniformity, on v4.5.5: virtualization.vminterface is the one
# entry whose serializer does NO mode/VLAN validation, so a CREATE naming mode
# "access" with tagged VLANs was stored as submitted (develop keeps tagged=[2701]).
# It now loses those VLANs, with a warning. That is the only payload measured where
# NetBox would have kept the data: dcim.interface answers the same contradiction with
# a 400 that costs the whole interface, VLAN.clean() rejects a stale qinq_svlan, and
# BaseInterface.save() drops a non-tagged interface's tagged VLANs on every later save
# anyway (`not self._state.adding`), so the stored state was one save from vanishing.
_DRIVER_FIELD_RULES = {
    "dcim.interface": {"mode": _INTERFACE_MODE_VLAN_RULES, "type": _INTERFACE_TYPE_RF_RULES},
    "virtualization.vminterface": {"mode": _INTERFACE_MODE_VLAN_RULES},
    "ipam.vlan": {"qinq_role": _VLAN_QINQ_RULES},
}

# The policy runs in two phases, on purpose:
#
# Phase 1 — apply_submitted_driver_field_policy(), run by the transformer over the
# whole entity graph before fingerprint dedupe, and again on each node dedupe merges
# (see transform_proto_json). A driver value the producer SUBMITTED wins over a field
# it forbids that the producer also submitted: the dependent field is removed from the
# payload, the reference edges it created are released, any child node left unreachable
# is pruned, and the removal is reported as a warning. This is what rescues a
# self-contradictory payload from a 400 that costs the whole entity. The pass is
# idempotent — once a field is gone there is nothing left to drop and nothing new to
# warn about — so however often it runs, each field is reported once.
#
# Phase 2 — normalize_changeset(), run by the differ once an existing row has been
# matched. It clears a STORED dependent value the effective driver value forbids, so
# the merged partial-update state stays legal. It never touches a field the payload
# still carries, which after phase 1 means either the driver value permits it or the
# producer submitted no driver value at all.
#
# Neither phase writes a value into a payload that did not carry the field unless
# there is a non-empty STORED value to clear; that is what makes re-ingest converge.
# Phase 1 removes rather than blanks precisely so it cannot guess wrong about the
# empty representation a field ends up storing: measured on v4.5.5, dcim.interface
# rf_role stores '' when it is submitted as null but NULL when it is not submitted at
# all — the same column, two empty values, so no single blank matches both, and a
# blank that does not match diffs against the stored one on every later ingest.


def _q_field_refs(condition) -> set[str]:
    """Field names a matcher's ``condition`` Q object reads, lookups stripped."""
    refs = set()
    if condition is None:
        return refs
    children = getattr(condition, "children", None)
    if children is None:
        return refs
    for child in children:
        if isinstance(child, Q):
            refs |= _q_field_refs(child)
        elif isinstance(child, (tuple, list)) and child:
            refs.add(str(child[0]).split("__", 1)[0])
    return refs


@lru_cache(maxsize=256)
def match_participating_fields(object_type: str) -> frozenset:
    """
    Every field name any matcher for ``object_type`` reads, conditions included.

    Phase 1 must never drop one of these. A matcher is how the plugin decides WHICH
    row a payload is talking about, so removing a field it reads does not just change
    what gets written -- it changes what gets matched, and the entity silently lands
    on a different object.

    Measured, and the reason this gate exists: ``ipam.vlan.qinq_svlan`` is read by
    four matchers (``ipam_vlan_unique_qinq_svlan_vid`` and ``..._name`` through their
    fields; ``logical_vlan_vid_no_group_or_svlan_or_site`` and ``logical_vlan_in_site``
    through a ``qinq_svlan IS NULL`` condition). Dropping it pre-match made a
    contradictory VLAN payload satisfy the vid-only criterion, so it adopted and
    renamed an unrelated VLAN that merely shared the vid -- or inserted a duplicate
    row and then converged onto it, which no re-ingest check can see. Dropping it
    post-match instead re-planned a CREATE forever. Neither placement is safe, so a
    match-participating field is simply never dropped and NetBox's own error stands.

    No ``dcim.interface`` or ``virtualization.vminterface`` field in the registry is
    read by any matcher (those match on device/VM plus name), so this costs the
    motivating cases nothing.

    A failed model lookup PROPAGATES. Answering "no field participates" would license
    every drop this gate exists to refuse, and ``lru_cache`` would keep serving that
    answer long after the database recovered; ``lru_cache`` does not cache exceptions,
    so the next call retries a lookup that failed transiently.
    """
    model_class = get_object_type_model(object_type)
    names = set()
    for matcher in get_model_matchers(model_class):
        # ObjectMatchCriteria._get_refs() already unions fields with the names its
        # expressions reference; other matcher classes only carry plain fields.
        get_refs = getattr(matcher, "_get_refs", None)
        if callable(get_refs):
            names |= set(get_refs())
        else:
            names.update(getattr(matcher, "fields", None) or ())
        names |= _q_field_refs(getattr(matcher, "condition", None))
    return frozenset(names)


def _drop_reason(driver_field: str, driver_value: str) -> str:
    """Explain a phase 1 drop, including when the submitted driver value is empty."""
    if driver_value:
        return (
            f"Dropped: submitted {driver_field} '{driver_value}' does not support this field."
        )
    return (
        f"Dropped: the submitted {driver_field} is empty, which does not support this field."
    )


def _ref_uuids(value) -> set[str]:
    """Every node uuid an (arbitrarily nested) field value points at."""
    if isinstance(value, UnresolvedReference):
        return {value.uuid}
    if isinstance(value, dict):
        found = set()
        for item in value.values():
            found |= _ref_uuids(item)
        return found
    if isinstance(value, list | tuple | set | frozenset):
        found = set()
        for item in value:
            found |= _ref_uuids(item)
        return found
    return set()


def _node_ref_uuids(node: dict) -> set[str]:
    """Every uuid the node's payload fields still point at (underscore keys excluded)."""
    found = set()
    for key, value in node.items():
        if key.startswith("_"):
            continue
        found |= _ref_uuids(value)
    return found


def _outgoing_edges(node: dict) -> set[str]:
    """
    The uuids this node keeps alive.

    A node's ``_refs`` is the set of nodes it depends on, and ``_topo_sort`` orders
    the graph by it. For a post-create step that set also holds the uuid of the node
    it completes (its ``_instance``); that edge is excluded here, because a post-create
    step hangs OFF its object rather than being a reason for the object to exist.
    """
    refs = set(node.get("_refs") or ())
    if node.get("_is_post_create"):
        refs.discard(node.get("_instance"))
    return refs


def referenced_uuids(entities) -> set[str]:
    """Public alias: the reference graph the transformer snapshots before any drop."""
    return _referenced_uuids(entities)


@lru_cache(maxsize=16)
def droppable_dependent_fields(object_type: str) -> frozenset:
    """
    Fields of ``object_type`` that SOME driver value could remove.

    A disagreement between two duplicate nodes over one of these is not necessarily a
    disagreement about real data: a driver value the merge has not reached yet may
    delete the field outright and settle it. Fields the gate protects are excluded —
    they can never be dropped, so a conflict on one is final.
    """
    rules_by_driver = _DRIVER_FIELD_RULES.get(object_type)
    if not rules_by_driver:
        return frozenset()
    names = set()
    for value_map in rules_by_driver.values():
        for dependents in value_map.values():
            names.update(dependents)
    return frozenset(names - match_participating_fields(object_type))


def release_rejected_edges(node: dict, rejected_values: list) -> None:
    """
    Release the edges only a rejected duplicate value contributed.

    ``_merge_nodes`` unions ``_refs``, so a value it declines to merge would otherwise
    keep its child nodes reachable and have them created anyway — the same manufactured
    VLAN ``_prune_orphaned_nodes`` exists to prevent. Only edges nothing else on the
    node still needs are released.
    """
    released = set()
    for value in rejected_values:
        released |= _ref_uuids(value)
    released -= _node_ref_uuids(node)
    if released and "_refs" in node:
        node["_refs"] = set(node["_refs"]) - released


def _referenced_uuids(entities) -> set[str]:
    """Every uuid some node in ``entities`` references."""
    referenced = set()
    for entity in entities:
        referenced |= _outgoing_edges(entity)
    return referenced


def _prune_orphaned_nodes(entities: list[dict], referenced_before: set[str]) -> list[dict]:
    """
    Drop child nodes that nothing surviving references any more.

    A dropped dependent field can be a NESTED reference — ``tagged_vlans`` is the
    motivating one — and by the time the policy runs, ``_transform_proto_json_1`` has
    already turned each item into its own node and recorded the edge in the parent's
    ``_refs``. Deleting only the field leaves those nodes and edges in the graph, so
    resolution still plans a CREATE for them: measured on v4.5.5, an access-mode
    interface submitting ``tagged_vlans`` [vid 621] applied 200, ended with no tagged
    VLANs, and left VLAN 621 in NetBox — a VLAN manufactured out of a field the change
    set said it had dropped.

    Pruning is reference-counted, because over-pruning is worse than the orphan. The
    same VLAN can be the interface's ``untagged_vlan`` as well as one of its tagged
    ones, or be tagged by a second interface in the same graph, or belong to a group,
    and after fingerprint dedupe all of those are edges into ONE node. So a node goes
    only when:

      * something referenced it BEFORE the drops (``referenced_before``) — that is what
        makes it a nested child rather than a root. A root is the entity's own primary
        object or a post-create step, and is never pruned; and
      * nothing that survives references it now.

    Removal is transitive (a pruned VLAN's group may itself become unreachable) and a
    post-create step follows the node it completes. Anything left referencing a pruned
    uuid would surface as an unresolved reference from ``_check_unresolved_refs``, so
    the two conditions are deliberately conservative: unsure means keep.
    """
    by_uuid = {entity["_uuid"]: entity for entity in entities}
    alive = set(by_uuid)
    while True:
        referenced = _referenced_uuids(by_uuid[uuid] for uuid in alive)
        doomed = set()
        for uuid in alive:
            entity = by_uuid[uuid]
            if entity.get("_is_post_create"):
                if entity.get("_instance") not in alive:
                    doomed.add(uuid)
            elif uuid in referenced_before and uuid not in referenced:
                doomed.add(uuid)
        if not doomed:
            break
        alive -= doomed
        logger.debug(f"Pruned {len(doomed)} node(s) orphaned by a driver-field drop")
    return [entity for entity in entities if entity["_uuid"] in alive]


def submitted_driver_field_drops(object_type: str, entity: dict) -> dict[str, str]:
    """
    Remove the submitted fields the SUBMITTED driver value forbids.

    Mutates ``entity`` in place, deleting each forbidden field and releasing the
    reference edges that field contributed to the node's ``_refs``, and returns
    ``{field: reason}`` for what it removed. No-op unless ``object_type`` is
    registered.

    The driver field must be explicitly present in ``entity``: the policy is that
    a submitted driver value wins over the fields it forbids, so with no submitted
    driver value there is nothing to win and producer data is left alone (a create
    that simply omits ``mode`` keeps the VLANs NetBox would have stored). A value
    that is absent or already empty is not touched either — there is nothing to
    drop, and rewriting it could only manufacture a spurious change.

    Forbidden fields are DELETED rather than blanked. The payload then reads as if
    the producer had never sent the field: nothing is submitted to NetBox for a
    create, and phase 2 remains free to clear a non-empty stored value with the
    empty representation that field actually accepts.
    """
    rules_by_driver = _DRIVER_FIELD_RULES.get(object_type)
    if not rules_by_driver:
        return {}
    dropped = {}
    released = set()
    for driver_field, value_map in rules_by_driver.items():
        if driver_field not in entity:
            continue  # nothing submitted -> nothing wins
        submitted = entity[driver_field]
        if submitted is not None and not isinstance(submitted, str):
            # Every driver field is a NetBox CharField; anything else is not a
            # value we can reason about, so fail open and let NetBox judge it.
            continue
        driver_value = submitted or ""
        for dependent in value_map.get(driver_value, ()):
            if not entity.get(dependent):
                continue  # not submitted, or submitted empty -> nothing to drop
            if dependent in match_participating_fields(object_type):
                # Identity field: dropping it would change which row we mean.
                # See match_participating_fields.
                logger.debug(
                    f"{object_type}.{dependent} is forbidden by {driver_field} "
                    f"'{driver_value}' but participates in matching; left for NetBox to reject"
                )
                continue
            released |= _ref_uuids(entity[dependent])
            del entity[dependent]
            dropped[dependent] = _drop_reason(driver_field, driver_value)
    if released and "_refs" in entity:
        # Release only the edges NOTHING else on this node needs. The same VLAN node
        # can be reached from untagged_vlan and from tagged_vlans at once (after
        # fingerprint dedupe, routinely), and dropping the tagged_vlans edge must not
        # take the untagged_vlan edge with it.
        entity["_refs"] = set(entity["_refs"]) - (released - _node_ref_uuids(entity))
    return dropped


def apply_submitted_driver_field_policy(entities: list[dict]) -> bool:
    """
    Phase 1: let each submitted driver value win over the fields it forbids.

    Mutates the nodes in place and returns whether any reference edge was released,
    i.e. whether a later prune has anything to do. It does NOT prune: see
    ``prune_orphaned_nodes`` and the ordering note there for why that has to wait.

    Every dropped value is recorded in the node's ``_warnings``, which the differ
    surfaces on the change set, so producer data is never discarded silently.

    Safe to run any number of times over the same graph, which the transformer does
    (once up front, then on every node fingerprint dedupe merges): a field that is
    already gone drops nothing, warns nothing and releases nothing.
    """
    refs_released = False
    for entity in entities:
        object_type = entity.get("_object_type")
        if object_type not in _DRIVER_FIELD_RULES:
            continue
        refs_before = set(entity.get("_refs") or ())
        dropped = submitted_driver_field_drops(object_type, entity)
        if not dropped:
            continue
        if set(entity.get("_refs") or ()) != refs_before:
            refs_released = True
        entity_warnings = entity.setdefault("_warnings", {})
        for field, reason in dropped.items():
            messages = entity_warnings.setdefault(field, [])
            # One drop of one field is one message. The pass runs repeatedly and
            # _merge_nodes unions the warnings of merged duplicates, so the same
            # sentence can arrive from more than one of those steps.
            if reason not in messages:
                messages.append(reason)
        logger.debug(f"Dropped {sorted(dropped)} from {object_type}: forbidden by the submitted driver value")
    return refs_released


def prune_orphaned_nodes(entities: list[dict], referenced_before: set) -> list[dict]:
    """
    Drop the child nodes the policy's releases left unreachable.

    Separate from the drop, and run ONCE after deduplication, because pruning inside
    the pre-dedupe pass destroys a duplicate representation before
    ``_fingerprint_dedupe`` can merge it. Measured: an interface with mode "access",
    ``untagged_vlan {vid 3992}`` and ``tagged_vlans [{vid 3992, name "v3992"}]`` --
    two child nodes for one VLAN, the richer one inside the field the mode forbids.
    Pruning per pass removed the named copy before the merge, leaving a survivor with
    a vid and no name, and the whole entity became
    ``400 {"ipam.vlan": {"name": ["This field cannot be blank."]}}`` forever -- an error
    that also misdirects, blaming a blank VLAN name for an interface mode contradiction.
    The quieter form of the same mechanism silently loses any field stated only on the
    dropped occurrence (a ``description`` carried only in ``tagged_vlans``).
    Deferring the sweep lets dedupe merge first, so the survivor keeps what the pruned
    copy contributed and only genuinely unreachable nodes go.

    ``referenced_before`` is the reference graph snapshotted BEFORE any drop: a node
    something referenced then was created as a nested child of that reference, while a
    node nothing referenced is a root (the entity's own primary object, or a post-create
    step) and is never pruned.
    """
    return _prune_orphaned_nodes(entities, referenced_before)


def normalize_changeset(object_type: str, prechange: dict, entity: dict) -> None:
    """
    Phase 2: clear stale STORED dependent fields the effective driver value forbids.

    Mutates ``entity`` in place. No-op unless ``object_type`` is registered and an
    existing row (non-empty ``prechange``) is present. A dependent field is cleared
    only when it is NOT explicitly present in ``entity`` (respect producer intent;
    phase 1 has already removed whatever a submitted driver value forbids) and its
    existing value is non-empty (idempotency: once NetBox stores the empty value,
    nothing is injected again, so re-ingest converges). Clearing uses ``[]`` for
    many-valued refs and ``None`` for single refs.
    """
    rules_by_driver = _DRIVER_FIELD_RULES.get(object_type)
    if not rules_by_driver or not prechange:
        return
    for driver_field, value_map in rules_by_driver.items():
        effective = entity.get(driver_field, prechange.get(driver_field))
        if effective is not None and not isinstance(effective, str):
            continue  # not a driver value we can reason about; fail open
        for dependent in value_map.get(effective or "", ()):
            if dependent in entity:
                continue
            if prechange.get(dependent):
                ref = get_json_ref_info(object_type, dependent)
                entity[dependent] = [] if (ref and ref.is_many) else None
