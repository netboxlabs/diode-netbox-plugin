#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin."""

from extras.plugins import PluginConfig

from .version import version_semver


class NetBoxDiodePluginConfig(PluginConfig):
    """NetBox Diode plugin configuration."""

    name = "netbox_diode_plugin"
    verbose_name = "NetBox Labs, Diode Plugin"
    description = "Diode plugin for NetBox."
    version = version_semver()
    base_url = "diode"
    min_version = "3.7.2"

    def ready(self):
        """Registers plugin signals."""
        super().ready()


config = NetBoxDiodePluginConfig