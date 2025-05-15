# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Client."""

from netbox_diode_plugin.diode.clients import get_api_client

def create_client(request, client_name, scope):
    return get_api_client().create_client(client_name, scope)

def delete_client(request, client_id):
    return get_api_client().delete_client(client_id)

def list_clients(request):
    response = get_api_client().list_clients()
    return response["data"]

def get_client(request, client_id):
    return get_api_client().get_client(client_id)