#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

from django.test import TestCase
from google.protobuf.json_format import MessageToJson

from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2, reconciler_pb2
from netbox_diode_plugin.templatetags.diode_filters import proto_to_json


class TestProtoToJsonTestCase(TestCase):
    """Test case for the proto_to_json template filter."""

    def test_ingestion_error_with_message(self):
        """Create a mock IngestionError with a message."""
        error = reconciler_pb2.IngestionError(message="Test error")
        expected_json = MessageToJson(error, indent=4)
        self.assertEqual(proto_to_json(error), expected_json)

    def test_ingestion_error_without_message(self):
        """Create a mock IngestionError without a message."""
        error = reconciler_pb2.IngestionError(message="")
        self.assertIsNone(proto_to_json(error))

    def test_entity(self):
        """Create a mock Entity."""
        entity = ingester_pb2.Entity(
            site=ingester_pb2.Site(
                name="Test Site",
            )
        )
        expected_json = MessageToJson(entity)
        self.assertEqual(proto_to_json(entity), expected_json)

    def test_invalid_type(self):
        """Test an invalid type."""
        self.assertIsNone(proto_to_json("invalid"))
