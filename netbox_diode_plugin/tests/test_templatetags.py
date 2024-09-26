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
        expected_json = MessageToJson(entity, indent=4)
        self.assertEqual(proto_to_json(entity), expected_json)

    def test_changeset(self):
        """Create a mock ChangeSet."""
        changeset = reconciler_pb2.ChangeSet(
            id="ac6481de-2351-49b6-9095-75a69fe47b1f",
            data=(
                b'\x1b\x92\x04\x00dq-\x95\x8fnV\xc0\x9c\xdbX"\\)\xfd1N\xdd\x04\x0e,\xc0\x00\xf8\x17\xb6\x17\x96\x05'
                b"4\xa6f\x0b8\x90\xa8\x0b\xc3P\xf3.z\x8e0\xd5\xb9r\xf0\x18?Y\xc4n\xb6\xe7\x82\xfa\x0b\xcb\x9f\x9f_\xf9"
                b"m\xbe\xee0Ai\xc1%\xdd\x19\x8d\xf5\x1a\x1d\xd5\x80\xa4\xc8c\xf4%\xd0`\x17\xab\x1e ^\r`:\xdb\t\xeer\xec"
                b"\xa9\xea\xa0MA6Dx\xc3c\xe5\xd0\xb0\xb1J\x8aUg\xed+`\xaf\x8d\x07\x13\x9cM\xe0\x81\x80\xa3\nH(\xe9\xed"
                b"\xfa~\x89\xb2\x03\x02\x9an\xb20\xfd\xc2C\xb9\x0f\x9fl\xed\x80\x80\xd7\xbb\xf7\x05Lp\xf5\xfc\x9c\xbe"
                b"\x95\xb7\xf7\xd746\xbc]\x7f0\xfc\xff\x0br\\\xc5-\xf8\x90\xd8a\xaa\xa6\xc2H\r\x8b\xd6\x84\xb6\x902\xb1"
                b"\xda`,\nJ\xab \t;7\x80_z\x9dkn\xb9\x9c\xe0\xb1\x19\x81|\xac\x16\xabO\x16\xa6\xc3:\x1e\xcec3\xddF\xd5"
                b"t\xf4-\x0c\xa4*x\xe5\xeeU\xf7\x8f\x9d\xef\xe6P@r\xeddU:\xf7\xadN\xc3\x0e\x1f\xd0\x96VA\xd7\xa44R\xd2"
                b"\x8cIyK\xa5G\x1a\xad\x018\xb0\xa2\xe2\xf1\xd1\xf5\x0b\x9bk4/o3\xe4?\x03Ly\x82~Y\x80\t~\xc0\xbe\x8bCW"
                b'_\x18=+\x03\xba6\xaa"1+\x8c\x81\x88\xaaM6ZB\x05\xd3k\xab\x8f\x0f\x83_\xf8\xa1\xf1\xbc\xfb\xf8x?/o\xcf'
                b"\xcap\x062oM\xdf\x8a\x80RtQ\x03U\x00I\xcc\xfb\xcf\xda\xc8\nx\xe5\x97\xeb\xf2\x8d\xdb\xb7wv\x9d\x0f1"
                b"\x91\xd2\xc6\xb6\xf2\xc5?"
            ),
        )

        decompress = proto_to_json(changeset)
        self.assertIsNotNone(decompress)
        self.assertIn("ac6481de-2351-49b6-9095-75a69fe47b1f", decompress)

    def test_invalid_type(self):
        """Test an invalid type."""
        self.assertIsNone(proto_to_json("invalid"))
