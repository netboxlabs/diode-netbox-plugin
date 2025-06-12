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

        # client_id and client_secret for communication with Diode server.
        # By default, the secret is read from a file /run/secrets/netbox_to_diode
        # but may be specified directly as a string in netbox_to_diode_client_secret
        "netbox_to_diode_client_id": "netbox-to-diode",
        "netbox_to_diode_client_secret": None,
        "secrets_path": "/run/secrets/",
        "netbox_to_diode_client_secret_name": "netbox_to_diode",
        "diode_max_auth_retries": 3,

        # List of audiences to require for the diode-to-netbox token.
        # If empty, no audience is required.
        "required_token_audience": [],
    }


config = NetBoxDiodePluginConfig
