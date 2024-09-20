#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Template Tags."""

import brotli
from django import template
from google.protobuf.json_format import MessageToJson
import json

from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2, reconciler_pb2

register = template.Library()


@register.filter("proto_to_json")
def proto_to_json(value):
    """Converts a protobuf message to a JSON string."""
    if isinstance(value, reconciler_pb2.IngestionError) and value.message != "":
        return MessageToJson(value, indent=4)

    if isinstance(value, ingester_pb2.Entity):
        return MessageToJson(value)

    if isinstance(value, reconciler_pb2.ChangeSet):
        try:
            decompressed_data = brotli.decompress(value.data)
            decompressed_string = decompressed_data.decode("utf-8")
            json_data = json.loads(decompressed_string)
            return json.dumps(json_data, indent=4)
        except Exception:
            return None

    return None
