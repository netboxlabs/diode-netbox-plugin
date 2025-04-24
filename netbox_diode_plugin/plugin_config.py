# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Plugin Settings."""

from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from netbox.plugins import get_plugin_config

__all__ = (
    "get_diode_auth_introspect_url",
    "get_diode_user",
)

User = get_user_model()


def _parse_diode_target(target: str) -> tuple[str, str, bool]:
    """Parse the target into authority, path and tls_verify."""
    parsed_target = urlparse(target)

    if parsed_target.scheme not in ["grpc", "grpcs"]:
        raise ValueError("target should start with grpc:// or grpcs://")

    tls_verify = parsed_target.scheme == "grpcs"

    authority = parsed_target.netloc

    return authority, parsed_target.path, tls_verify


def get_diode_auth_introspect_url():
    """Returns the Diode Auth introspect URL."""
    diode_target = get_plugin_config("netbox_diode_plugin", "diode_target")
    diode_target_override = get_plugin_config(
        "netbox_diode_plugin", "diode_target_override"
    )

    authority, path, tls_verify = _parse_diode_target(
        diode_target_override or diode_target
    )
    scheme = "https" if tls_verify else "http"
    path = path.rstrip("/")

    return f"{scheme}://{authority}{path}/auth/introspect"


def get_diode_user():
    """Returns the Diode user."""
    diode_username = get_plugin_config("netbox_diode_plugin", "diode_username")

    try:
        diode_user = User.objects.get(username=diode_username)
    except User.DoesNotExist:
        diode_user = User.objects.create(username=diode_username, is_active=True)

    return diode_user
