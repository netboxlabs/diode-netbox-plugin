#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Navigation."""

from netbox.plugins import PluginMenu, PluginMenuItem

settings = {
    "link": "plugins:netbox_diode_plugin:settings",
    "link_text": "Settings",
    "staff_only": True,
}


menu = PluginMenu(
    label="Diode",
    groups=(
        (
            "Diode",
            (
                PluginMenuItem(**settings),
            ),
        ),
    ),
    icon_class="mdi mdi-upload",
)
