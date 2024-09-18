#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

import datetime

import zoneinfo
from django.conf import settings
from django.test import TestCase

from netbox_diode_plugin.reconciler.sdk.v1 import reconciler_pb2
from netbox_diode_plugin.tables import IngestionLogsTable


class IngestionLogsTableTestCase(TestCase):
    """Test case for the IngestionLogsTable."""

    def setUp(self):
        """Set up mock data for the table."""
        self.mock_data = [
            reconciler_pb2.IngestionLog(
                ingestion_ts=1638316800000000000,  # Example timestamp in nanoseconds
                state=reconciler_pb2.State.RECONCILED,
                data_type="dcim.site",
                request_id="12345",
                producer_app_name="TestApp",
                producer_app_version="1.0.0",
                sdk_name="TestSDK",
                sdk_version="1.0.0",
            ),
            reconciler_pb2.IngestionLog(),
        ]

    def test_ingestion_ts_rendering(self):
        """Test rendering of the ingestion_ts column."""
        table = IngestionLogsTable(self.mock_data)
        current_tz = zoneinfo.ZoneInfo(settings.TIME_ZONE)
        expected_date = (
            datetime.datetime.fromtimestamp(
                self.mock_data[0].ingestion_ts / 1_000_000_000
            )
            .astimezone(current_tz)
            .date()
            .isoformat()
        )
        expected_time = (
            datetime.datetime.fromtimestamp(
                self.mock_data[0].ingestion_ts / 1_000_000_000
            )
            .astimezone(current_tz)
            .time()
            .isoformat(timespec="seconds")
        )
        self.assertEqual(
            table.rows[0].get_cell("ingestion_ts"), f"{expected_date} {expected_time}"
        )
        self.assertEqual(table.rows[1].get_cell("ingestion_ts"), None)

    def test_state_rendering(self):
        """Test rendering of the state column."""
        table = IngestionLogsTable(self.mock_data)
        self.assertEqual(table.rows[0].get_cell("state"), "Reconciled")
        self.assertEqual(table.rows[1].get_cell("state"), None)

    def test_data_type_rendering(self):
        """Test rendering of the data_type column."""
        table = IngestionLogsTable(self.mock_data)
        self.assertEqual(table.rows[0].get_cell("object_type"), "Site")
        self.assertEqual(table.rows[1].get_cell("object_type"), table.default)

    def test_producer_rendering(self):
        """Test rendering of the producer column."""
        table = IngestionLogsTable(self.mock_data)
        self.assertEqual(table.rows[0].get_cell("producer"), "TestApp/1.0.0")

    def test_sdk_rendering(self):
        """Test rendering of the sdk column."""
        table = IngestionLogsTable(self.mock_data)
        self.assertEqual(table.rows[0].get_cell("sdk"), "TestSDK/1.0.0")

    def test_request_id_rendering(self):
        """Test rendering of the request_id column."""
        table = IngestionLogsTable(self.mock_data)
        self.assertEqual(table.rows[0].get_cell("request_id"), "12345")
