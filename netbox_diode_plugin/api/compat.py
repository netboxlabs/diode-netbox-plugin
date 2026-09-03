#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Compatibility Transformations."""

import logging
import re
from collections import defaultdict
from functools import cache

from django.conf import settings
from packaging import version
from utilities.release import load_release_data

logger = logging.getLogger(__name__)

_MIGRATIONS_BY_OBJECT_TYPE = defaultdict(list)

def apply_entity_migrations(data: dict, object_type: str):
    """
    Applies migrations to diode entity data prior to diffing to improve compatibility with current NetBox version.

    These represent cases like deprecated fields that have been replaced with new fields, but
    are supported for backwards compatibility.
    """
    for migration in _MIGRATIONS_BY_OBJECT_TYPE.get(object_type, []):
        logger.debug(f"Applying migration {migration.__name__} for {object_type}")
        migration(data)

def _register_migration(func, min_version, max_version, object_type):
    """Registers a migration function."""
    if in_version_range(min_version, max_version):
        logger.debug(f"Registering migration {func.__name__} for {object_type}.")
        _MIGRATIONS_BY_OBJECT_TYPE[object_type].append(func)
    else:
        logger.debug(f"Skipping migration {func.__name__} for {object_type}: {min_version} to {max_version}.")

@cache
def _current_netbox_version():
    """Returns the current version of NetBox."""
    try:
        return version.parse(settings.RELEASE.version)
    except Exception:
        logger.exception("Failed to determine current version of NetBox.")
        return (0, 0, 0)

def in_version_range(min_version: str | None, max_version: str | None):
    """Returns True if the current version of NetBox is within the given version range."""
    min_version = version.parse(min_version) if min_version else None
    max_version = version.parse(max_version) if max_version else None
    current_version = _current_netbox_version()
    if min_version and current_version < min_version:
        return False
    if max_version and current_version > max_version:
        return False
    return True

def diode_migration(min_version: str, max_version: str | None, object_type: str):
    """Decorator to mark a function as a diode migration."""
    def decorator(func):
        _register_migration(func, min_version, max_version, object_type)
        return func
    return decorator

@diode_migration(min_version="4.3.0", max_version=None, object_type="ipam.service")
def _migrate_service_parent_object(data: dict):
    """Transforms ipam.service device and virtual_machine references to parent_object."""
    device = data.pop("device", None)
    if device:
        if data.get("parent_object_device") is None:
            data["parent_object_device"] = device
        # else ignored.

    virtual_machine = data.pop("virtual_machine", None)
    if virtual_machine:
        if data.get("parent_object_virtual_machine") is None:
            data["parent_object_virtual_machine"] = virtual_machine
        # else ignored.

@diode_migration(min_version="4.3.0", max_version=None, object_type="tenancy.contact")
def _migrate_contact_group(data: dict):
    """Transforms tenancy.contact group references to groups."""
    group = data.pop("group", None)
    if group:
        if data.get("groups") is None:
            data["groups"] = [group]
        # else ignored.

@diode_migration(min_version="4.2.0", max_version="4.2.99", object_type="ipam.service")
def _migrate_service_parent_object_down(data: dict):
    """Transforms ipam.service parent_object to device and virtual_machine."""
    parent_object_vm = data.pop("parent_object_virtual_machine", None)
    if parent_object_vm and data.get("virtual_machine") is None:
        data["virtual_machine"] = parent_object_vm
    parent_object_device = data.pop("parent_object_device", None)
    if parent_object_device and data.get("device") is None:
        data["device"] = parent_object_device

@diode_migration(min_version="4.2.0", max_version="4.2.99", object_type="tenancy.contact")
def _migrate_contact_group_down(data: dict):
    """Transforms tenancy.contact groups to group."""
    groups = data.pop("groups", None)
    if groups and len(groups) == 1:
        data["group"] = groups[0]

@diode_migration(min_version="4.7.0-beta1", max_version=None, object_type="ipam.service")
def _migrate_service_port_mappings(data: dict):
    """
    Synthesizes port_mappings from legacy protocol/ports.

    NetBox 4.7 replaced Service.protocol/ports with the unified
    port_mappings field ("tcp/80"-style strings); the legacy fields are
    no longer model-backed, so a payload carrying only them would fail
    with "At least one port mapping is required".
    """
    protocol = data.pop("protocol", None)
    ports = data.pop("ports", None)
    if data.get("port_mappings"):
        return  # producer already speaks port_mappings
    if protocol and ports:
        data["port_mappings"] = [f"{protocol}/{port}" for port in ports]

def _flat_mac_to_primary_mac_address(data: dict):
    """
    Rewrites the flat mac_address value into the modeled primary MAC form.

    NetBox 4.7 added mac_address as a writable serializer shortcut with no
    backing model column, so it cannot pass the plugin's diffable-field
    gate. The plugin has always modeled this as a nested MACAddress via
    primary_mac_address, which diffs and applies cleanly on every
    supported NetBox version - so translate rather than reject.
    """
    mac = data.pop("mac_address", None)
    if mac and data.get("primary_mac_address") is None:
        data["primary_mac_address"] = {"mac_address": mac}

@diode_migration(min_version="4.2.0", max_version=None, object_type="dcim.interface")
def _migrate_interface_mac_address(data: dict):
    """Translates dcim.interface's flat mac_address shortcut."""
    _flat_mac_to_primary_mac_address(data)

@diode_migration(min_version="4.2.0", max_version=None, object_type="virtualization.vminterface")
def _migrate_vminterface_mac_address(data: dict):
    """Translates virtualization.vminterface's flat mac_address shortcut."""
    _flat_mac_to_primary_mac_address(data)
