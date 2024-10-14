# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Plugin Settings."""

from netbox.plugins import get_plugin_config

__all__ = (
    "get_diode_user_types",
    "get_diode_usernames",
    "get_diode_username_for_user_type",
)


def get_diode_user_types():
    """Returns a list of diode user types."""
    return "diode_to_netbox", "netbox_to_diode", "diode"


def get_diode_user_types_with_labels():
    """Returns a list of diode user types with labels."""
    return (
        ("diode_to_netbox", "Diode to NetBox"),
        ("netbox_to_diode", "NetBox to Diode"),
        ("diode", "Diode"),
    )


def get_diode_usernames():
    """Returns a dictionary of diode user types and their configured usernames."""
    return {
        user_type: get_plugin_config("netbox_diode_plugin", f"{user_type}_username")
        for user_type in get_diode_user_types()
    }


def get_diode_username_for_user_type(user_type):
    """Returns a diode username for a given user type."""
    return get_plugin_config("netbox_diode_plugin", f"{user_type}_username")
