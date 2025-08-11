# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Plugin Settings."""

import logging
import os
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from netbox.plugins import get_plugin_config

__all__ = (
    "get_diode_auth_introspect_url",
    "get_diode_user",
)

User = get_user_model()

logger = logging.getLogger("netbox.diode_data")

def _parse_diode_target(target: str) -> tuple[str, str, bool]:
    """Parse the target into authority, path and tls_verify."""
    parsed_target = urlparse(target)

    if parsed_target.scheme not in ["grpc", "grpcs", "http", "https"]:
        raise ValueError("target should start with grpc://, grpcs://, http:// or https://")

    tls_verify = parsed_target.scheme in ["grpcs", "https"]

    authority = parsed_target.netloc

    if ":" not in authority:
        if parsed_target.scheme in ["grpc", "http"]:
            authority += ":80"
        elif parsed_target.scheme in ["grpcs", "https"]:
            authority += ":443"

    return authority, parsed_target.path, tls_verify


def get_diode_auth_introspect_url():
    """Returns the Diode Auth introspect URL."""
    diode_auth_base_url = get_diode_auth_base_url()
    return f"{diode_auth_base_url}/introspect"

def get_diode_auth_base_url():
    """Returns the Diode Auth service base URL."""
    diode_target = get_plugin_config("netbox_diode_plugin", "diode_target")
    diode_target_override = get_plugin_config(
        "netbox_diode_plugin", "diode_target_override"
    )

    authority, path, tls_verify = _parse_diode_target(
        diode_target_override or diode_target
    )
    scheme = "https" if tls_verify else "http"
    path = path.rstrip("/")

    return f"{scheme}://{authority}{path}/auth"

def get_diode_credentials():
    """Returns the Diode credentials."""
    client_id = get_plugin_config("netbox_diode_plugin", "netbox_to_diode_client_id")
    secrets_path = get_plugin_config("netbox_diode_plugin", "secrets_path")
    secret_name = get_plugin_config("netbox_diode_plugin", "netbox_to_diode_client_secret_name")
    client_secret = get_plugin_config("netbox_diode_plugin", "netbox_to_diode_client_secret")

    if not client_secret:
        secret_file = os.path.join(secrets_path, secret_name)
        client_secret = _read_secret(secret_file, client_secret)

    return client_id, client_secret

def get_diode_max_auth_retries():
    """Returns the Diode max auth retries."""
    return get_plugin_config("netbox_diode_plugin", "diode_max_auth_retries")

# Read secret from file
def _read_secret(secret_file: str, default: str | None = None) -> str | None:
    try:
        f = open(secret_file, encoding='utf-8')
    except OSError:
        return default
    else:
        with f:
            return f.readline().strip()

def get_diode_user():
    """Returns the Diode user."""
    diode_username = get_plugin_config("netbox_diode_plugin", "diode_username")

    try:
        diode_user = User.objects.get(username=diode_username)
    except User.DoesNotExist:
        diode_user = User.objects.create(username=diode_username, is_active=True)

    return diode_user

def get_required_token_audience():
    """Returns the require token audience."""
    return get_plugin_config("netbox_diode_plugin", "required_token_audience")
