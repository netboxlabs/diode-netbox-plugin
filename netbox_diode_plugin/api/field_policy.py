#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Driver field policy."""

import logging

from dcim.choices import InterfaceTypeChoices
from dcim.constants import WIRELESS_IFACE_TYPES

from .plugin_utils import get_json_ref_info

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
# Phase 1 — apply_submitted_driver_field_policy(), run by the transformer for every
# entity BEFORE any fingerprinting. A driver value the producer SUBMITTED wins over
# a field it forbids that the producer also submitted: the dependent field is
# removed from the payload and the removal is reported as a warning. This is what
# rescues a self-contradictory payload from a 400 that costs the whole entity.
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


def _drop_reason(driver_field: str, driver_value: str) -> str:
    """Explain a phase 1 drop, including when the submitted driver value is empty."""
    if driver_value:
        return (
            f"Dropped: submitted {driver_field} '{driver_value}' does not support this field."
        )
    return (
        f"Dropped: the submitted {driver_field} is empty, which does not support this field."
    )


def submitted_driver_field_drops(object_type: str, entity: dict) -> dict[str, str]:
    """
    Remove the submitted fields the SUBMITTED driver value forbids.

    Mutates ``entity`` in place, deleting each forbidden field, and returns
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
            del entity[dependent]
            dropped[dependent] = _drop_reason(driver_field, driver_value)
    return dropped


def apply_submitted_driver_field_policy(entities: list[dict]) -> None:
    """
    Phase 1: let each submitted driver value win over the fields it forbids.

    Runs before fingerprinting, so it must be given transformed entity nodes.
    Every dropped value is recorded in the node's ``_warnings``, which the differ
    surfaces on the change set, so producer data is never discarded silently.
    """
    for entity in entities:
        object_type = entity.get("_object_type")
        if object_type not in _DRIVER_FIELD_RULES:
            continue
        dropped = submitted_driver_field_drops(object_type, entity)
        if not dropped:
            continue
        entity_warnings = entity.setdefault("_warnings", {})
        for field, reason in dropped.items():
            entity_warnings.setdefault(field, []).append(reason)
        logger.debug(f"Dropped {sorted(dropped)} from {object_type}: forbidden by the submitted driver value")


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
