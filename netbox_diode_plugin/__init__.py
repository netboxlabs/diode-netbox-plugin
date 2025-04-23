#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin."""

from netbox.plugins import PluginConfig

from .version import version_semver


class NetBoxDiodePluginConfig(PluginConfig):
    """NetBox Diode plugin configuration."""

    name = "netbox_diode_plugin"
    verbose_name = "NetBox Labs, Diode Plugin"
    description = "Diode plugin for NetBox."
    version = version_semver()
    base_url = "diode"
    min_version = "4.2.3"
    default_settings = {
        # Default Diode gRPC target for communication with Diode server
        "diode_target": "grpc://localhost:8080/diode",

        # Default username associated with changes applied via plugin
        "diode_username": "diode",
    }


config = NetBoxDiodePluginConfig
