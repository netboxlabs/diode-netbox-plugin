#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Navigation."""

from django.utils.translation import gettext as _
from netbox.plugins import PluginMenu, PluginMenuItem

_diode_menu_items = (
    PluginMenuItem(
        link="plugins:netbox_diode_plugin:settings",
        link_text=_("Settings"),
        permissions=("netbox_diode_plugin.view_setting",),
    ),
    PluginMenuItem(
        link="plugins:netbox_diode_plugin:client_credential_list",
        link_text=_("Client Credentials"),
        permissions=("netbox_diode_plugin.view_clientcredentials",),
    ),
)

menu = PluginMenu(
    label="Diode",
    groups=(
        (_("Diode"), _diode_menu_items),
    ),
    icon_class="mdi mdi-upload",
)
