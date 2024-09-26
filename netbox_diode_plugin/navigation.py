#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Navigation."""

from netbox.plugins import PluginMenu, PluginMenuItem

ingestion_logs = {
    "link": "plugins:netbox_diode_plugin:ingestion_logs",
    "link_text": "Ingestion Logs",
    "staff_only": True,
}

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
                PluginMenuItem(**ingestion_logs),
                PluginMenuItem(**settings),
            ),
        ),
    ),
    icon_class="mdi mdi-upload",
)
