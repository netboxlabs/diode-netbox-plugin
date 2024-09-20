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

    def test_changeset(self):
        """Create a mock ChangeSet."""
        changeset = reconciler_pb2.ChangeSet(
            id="2a4e85a7-abad-4e1b-9ae0-db22f0900251",
            data=b"G5UEAGRxLZWPbr7lNKe3CTPJKTwA/P83XzTBAPgtaiybHgY0psYBL+qEl7HFY/In/JBulkUclor+WdLfisYDwj//d+jHv1mDAZyEnqMQSpGGoduCLN1gK86NDRvjogW11YXh6yR8Ctwtp8JRMMUwFl/IYS55lPc+RpeKL4mFe21sGOBtAhsUPFXAlrxWZ6sRsgkKmm78MDxgLatl3DKj1SnPwGF5msAAJXiBoxxPBxhA6nF27vB6KUw4255saISFStK3y1aQSwsoMeVquRhORiHPxeDiHg5UOYU2A+FoQNgVZ7pPHSlRFJjcGCWPLVYj6mEgbs7mkPgLRwN4tVab1pejrOBy2dwqcsA9tfHYV0bh5vWtoyTkcTXYWWhsLZVYjVAtxmS84/MyO9YpnHSI4XqpgAHSDpMJUltgdNVYcZXsZqfokYiku3FwnUkxfORvKPxhin821v+McZHTc7C4riAb2aXBUMBJ0f/ZFH4Fh76fyTfW8Pb+wZlSDN7Vq/bzAg==",
        )
        decompress = proto_to_json(changeset)
        self.assertIsNotNone(decompress)
        self.assertIn("2a4e85a7-abad-4e1b-9ae0-db22f0900251", decompress)

    def test_invalid_type(self):
        """Test an invalid type."""
        self.assertIsNone(proto_to_json("invalid"))
