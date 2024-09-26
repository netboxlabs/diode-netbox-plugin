#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
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
    min_version = "3.7.2"
    default_settings = {
        # Default Diode gRPC target for communication with Diode server
        "diode_target": "grpc://localhost:8080/diode",

        # User allowed for Diode to NetBox communication
        "diode_to_netbox_username": "diode-to-netbox",

        # User allowed for NetBox to Diode communication
        "netbox_to_diode_username": "netbox-to-diode",

        # User allowed for data ingestion
        "diode_username": "diode-ingestion",
    }


config = NetBoxDiodePluginConfig
