#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Template Tags."""
from django import template
from google.protobuf.json_format import MessageToJson

from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2, reconciler_pb2

register = template.Library()


@register.filter("proto_to_json")
def proto_to_json(value):
    """Converts a protobuf message to a JSON string."""
    if isinstance(value, reconciler_pb2.IngestionError) and value.message != "":
        return MessageToJson(value, indent=4)

    if isinstance(value, ingester_pb2.Entity):
        return MessageToJson(value)

    return None
