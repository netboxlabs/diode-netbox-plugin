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

# A few NetBox fields are legal only for particular values of another field on the same
# object: a "driver" field whose value forbids a set of "dependent" fields. Wherever
# NetBox polices this it rejects the WHOLE entity rather than ignoring the surplus field.
#
# object_type -> driver_field -> { driver_value: [forbidden] }, per NetBox's clean().
_INTERFACE_MODE_VLAN_RULES = {
    "": ["untagged_vlan", "tagged_vlans", "qinq_svlan"],  # routed / no 802.1Q
    "access": ["tagged_vlans", "qinq_svlan"],
    "tagged": ["qinq_svlan"],
    "tagged-all": ["tagged_vlans", "qinq_svlan"],
    "q-in-q": [],
}

# rf_* is wireless-only. Keyed on the complement of the wireless allow-set, so a type
# change away from wireless clears the stale rf fields.
_RF_FIELDS = ["rf_channel", "rf_channel_frequency", "rf_channel_width", "rf_role"]
_INTERFACE_TYPE_RF_RULES = {
    t: _RF_FIELDS for t in InterfaceTypeChoices.values() if t not in WIRELESS_IFACE_TYPES
}

# qinq_svlan is customer-VLAN-only. The reciprocal "customer VLAN requires an svlan" is
# a presence rule, not ours, so 'cvlan' maps to [].
_VLAN_QINQ_RULES = {
    "": ["qinq_svlan"],
    "svlan": ["qinq_svlan"],
    "cvlan": [],
}

# Applied uniformly: the rules encode NetBox's data model, not which serializer happens
# to police it. virtualization.vminterface validates nothing, so it is the only place a
# create loses VLANs NetBox would have stored -- accepted, because BaseInterface.save()
# drops a non-tagged interface's tagged VLANs on every later save anyway.
_DRIVER_FIELD_RULES = {
    "dcim.interface": {"mode": _INTERFACE_MODE_VLAN_RULES, "type": _INTERFACE_TYPE_RF_RULES},
    "virtualization.vminterface": {"mode": _INTERFACE_MODE_VLAN_RULES},
    "ipam.vlan": {"qinq_role": _VLAN_QINQ_RULES},
}

# Phase 1 (apply_submitted_driver_field_policy, in the transformer) lets a SUBMITTED
# driver value delete the fields it forbids, rescuing a self-contradictory payload from
# a 400 that costs the whole entity. Phase 2 (normalize_changeset) clears a STORED value
# the effective driver value forbids, and stays in the differ because it needs the
# matched row. Neither invents a field the payload did not carry unless there is a
# non-empty stored value to clear, which is what makes re-ingest converge.


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

    Phase 1 must never drop one of these: a matcher decides WHICH row a payload means,
    so removing a field it reads lands the entity on a different object. Four VLAN
    matchers read ``ipam.vlan.qinq_svlan``; dropping it let a contradictory payload
    satisfy the vid-only criterion and rename an unrelated same-vid row.

    A failed lookup PROPAGATES: "no field participates" would license every drop the
    gate refuses, and ``lru_cache`` would serve it after the database recovered.
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

    Its ``_refs``, minus (for a post-create step) the node it completes, which it hangs
    off rather than keeps alive.
    """
    refs = set(node.get("_refs") or ())
    if node.get("_is_post_create"):
        refs.discard(node.get("_instance"))
    return refs


@lru_cache(maxsize=16)
def droppable_dependent_fields(object_type: str) -> frozenset:
    """
    Fields of ``object_type`` that SOME driver value could remove.

    Two duplicates disagreeing over one of these may not be disagreeing about real data:
    a driver value the merge has not reached yet may delete the field and settle it.
    Gate-protected fields are excluded, so a conflict on one is final.
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

    ``_merge_nodes`` unions ``_refs``, so a declined value would otherwise keep its
    children reachable and have them created anyway. Only edges nothing else needs go.
    """
    released = set()
    for value in rejected_values:
        released |= _ref_uuids(value)
    released -= _node_ref_uuids(node)
    if released and "_refs" in node:
        node["_refs"] = set(node["_refs"]) - released


def referenced_uuids(entities) -> set[str]:
    """Every uuid some node in ``entities`` references."""
    referenced = set()
    for entity in entities:
        referenced |= _outgoing_edges(entity)
    return referenced


def prune_orphaned_nodes(entities: list[dict], referenced_before: set) -> list[dict]:
    """
    Drop the child nodes a drop left unreachable.

    A dropped dependent field is often a NESTED reference whose items are already nodes
    with edges in ``_refs``; deleting only the field leaves those behind and resolution
    still creates them -- a VLAN manufactured out of a field the change set dropped.

    Reference-counted and transitive, because over-pruning is worse than the orphan: one
    VLAN node is routinely reached from ``untagged_vlan``, ``tagged_vlans``, a second
    interface and a group at once. A node goes only if something referenced it BEFORE
    the drops (``referenced_before`` -- what makes it a child and not a root) and nothing
    surviving references it now. Unsure means keep. Run ONCE, after dedupe, never inside
    a pass: pruning the copy inside the forbidden field before the merge leaves a
    survivor missing a required ``name`` (a permanent 400 blaming a blank VLAN name for
    a mode contradiction) or a ``description``.
    """
    by_uuid = {entity["_uuid"]: entity for entity in entities}
    alive = set(by_uuid)
    while True:
        referenced = referenced_uuids(by_uuid[uuid] for uuid in alive)
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

    Mutates ``entity`` in place, deleting each forbidden field and releasing the edges it
    contributed to ``_refs``; returns ``{field: reason}``. The driver field must be
    PRESENT: with no submitted driver value nothing wins, so producer data is left alone
    (a create omitting ``mode`` keeps the VLANs NetBox would have stored). Forbidden
    fields are DELETED, not blanked -- the same column stores different empty values
    depending on whether the field was submitted at all, so a guessed blank diffs against
    the stored one forever, and deletion leaves phase 2 free to clear it properly.
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
            continue  # not a CharField value we can reason about; let NetBox judge it
        driver_value = submitted or ""
        for dependent in value_map.get(driver_value, ()):
            if not entity.get(dependent):
                continue  # not submitted, or submitted empty -> nothing to drop
            if dependent in match_participating_fields(object_type):
                # Identity field: dropping it would change which row we mean.
                logger.debug(
                    f"{object_type}.{dependent} is forbidden by {driver_field} "
                    f"'{driver_value}' but participates in matching; left for NetBox to reject"
                )
                continue
            released |= _ref_uuids(entity[dependent])
            del entity[dependent]
            dropped[dependent] = _drop_reason(driver_field, driver_value)
    if released and "_refs" in entity:
        # Only edges NOTHING else on this node needs: one VLAN node is routinely both
        # untagged_vlan and a tagged one.
        entity["_refs"] = set(entity["_refs"]) - (released - _node_ref_uuids(entity))
    return dropped


def apply_submitted_driver_field_policy(entities: list[dict]) -> bool:
    """
    Phase 1: let each submitted driver value win over the fields it forbids.

    Mutates the nodes in place; returns whether any edge was released, i.e. whether a
    later prune has anything to do. It does NOT prune. Every drop is recorded in
    ``_warnings`` so nothing is discarded silently, and the pass is idempotent -- the
    transformer runs it once up front and again on every node dedupe merges.
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
            # One drop of one field is one message; the pass runs repeatedly and
            # _merge_nodes unions merged duplicates' warnings.
            if reason not in messages:
                messages.append(reason)
        logger.debug(f"Dropped {sorted(dropped)} from {object_type}: forbidden by the submitted driver value")
    return refs_released


def normalize_changeset(object_type: str, prechange: dict, entity: dict) -> None:
    """
    Phase 2: clear stale STORED dependent fields the effective driver value forbids.

    Mutates ``entity`` in place; no-op without a registered type and a non-empty
    ``prechange``. A field is cleared only when it is NOT present in ``entity`` (producer
    intent; phase 1 removed what a submitted driver value forbids) and its stored value
    is non-empty, so re-ingest converges. ``[]`` for many-valued refs, ``None`` for one.
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
