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
    min_version = "4.4.10"
    max_version = "4.5.99"
    middleware = [
        "netbox_diode_plugin.api.profile.DiodeProfileMiddleware",
    ]
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

        # TTL in seconds for caching find_existing_object results in Redis.
        # Set to 0 to disable caching.
        "find_obj_cache_ttl": 5,

        # Override the displayed Diode target URL without affecting internal
        # communication (e.g. to show the external ingress address).
        "diode_target_display": None,

        # Max number of retries when the batch apply endpoint hits a
        # Postgres deadlock (40P01) or serialization failure (40001).
        # 0 disables retries; default 3 means up to three retries after
        # the initial attempt (4 attempts total).
        "batch_apply_deadlock_retry_max_count": 3,
    }


config = NetBoxDiodePluginConfig
