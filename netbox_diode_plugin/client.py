# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Client."""

import logging

from netbox_diode_plugin.diode.clients import get_api_client

logger = logging.getLogger("netbox.diode_data")


def create_client(request, client_name: str, scope: str):
    """Create client."""
    logger.info(f"Creating client {client_name} with scope {scope}")
    return get_api_client().create_client(client_name, scope)


def delete_client(request, client_id: str):
    """Delete client."""
    sanitized_client_id = client_id.replace("\n", "").replace("\r", "")
    logger.info(f"Deleting client {sanitized_client_id}")
    return get_api_client().delete_client(client_id)


def list_clients(request):
    """List clients."""
    logger.info("Listing clients")
    response = get_api_client().list_clients()
    return response["data"]


def get_client(request, client_id: str):
    """Get client."""
    sanitized_client_id = client_id.replace("\n", "").replace("\r", "")
    logger.info(f"Getting client {sanitized_client_id}")
    return get_api_client().get_client(client_id)
