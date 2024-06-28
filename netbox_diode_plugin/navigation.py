# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Navigation."""

from extras.plugins import PluginMenu, PluginMenuItem

audit_log = {
    "link": "plugins:netbox_diode_plugin:audit_log",
    "link_text": "Audit Log",
    "staff_only": True,
}


menu = PluginMenu(
    label="NetBox Labs",
    groups=(
        (
            "Diode",
            (PluginMenuItem(**audit_log),),
        ),
    ),
    icon_class="mdi mdi-arrow-collapse-right",
)
