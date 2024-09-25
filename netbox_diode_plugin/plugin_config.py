# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Plugin Settings."""

from netbox.plugins import get_plugin_config

__all__ = ("get_diode_usernames", "get_diode_username_for_user_category")


def get_diode_usernames():
    """Returns a dictionary of diode user categories and their configured usernames."""
    diode_user_categories = ("diode_to_netbox", "netbox_to_diode", "diode")
    return {
        user_category: get_plugin_config(
            "netbox_diode_plugin", f"{user_category}_username"
        )
        for user_category in diode_user_categories
    }


def get_diode_username_for_user_category(user_category):
    """Returns a diode username for a given user category."""
    return get_plugin_config("netbox_diode_plugin", f"{user_category}_username")
