#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tables."""
import datetime

import django_tables2 as tables
import zoneinfo
from django.conf import settings
from packaging import version

if version.parse(settings.VERSION).major >= 4:
    from core.models import ObjectType as NetBoxType
else:
    from django.contrib.contenttypes.models import ContentType as NetBoxType

from netbox.tables import BaseTable, columns
from utilities.object_types import object_type_identifier, object_type_name

from netbox_diode_plugin.reconciler.sdk.v1 import reconciler_pb2

INGESTION_LOGS_TABLE_ACTIONS_TEMPLATE = """
<button class="btn btn-xs btn-default" data-bs-toggle="collapse" data-bs-target="#ingestion-log-{{ record.id }}">
    <i class="mdi mdi-eye"></i>
</button>
"""


class IngestionStateColumn(tables.Column):
    """Renders the ingestion state as a human-readable string."""

    def render(self, value):
        """Renders the ingestion state as a human-readable string."""
        if value:
            state_name = reconciler_pb2.State.Name(value)
            return " ".join(state_name.title().split("_"))
        return None


class TimestampColumn(columns.DateTimeColumn):
    """Custom implementation of Timestamp to render an epoch timestamp as a human-readable date."""

    def render(self, value):
        """Renders an epoch timestamp as a human-readable date."""
        if value:
            current_tz = zoneinfo.ZoneInfo(settings.TIME_ZONE)
            value = datetime.datetime.fromtimestamp(value / 1_000_000_000).astimezone(
                current_tz
            )
            return f"{value.date().isoformat()} {value.time().isoformat(timespec=self.timespec)}"
        return None


class DataTypeColumn(columns.ContentTypeColumn):
    """Custom implementation of ContentTypeColumn to render a data type based on app_label and model."""

    def render(self, value):
        """Renders a data type based on app_label and model."""
        app_label, model_name = value.split(".")
        object_content_type = NetBoxType.objects.get_by_natural_key(
            app_label, model_name
        )
        return object_type_name(object_content_type, include_app=False)

    def value(self, value):
        """Returns the value."""
        return value


class IngestionLogsTable(BaseTable):
    """Ingestion logs table."""

    ingestion_ts = TimestampColumn(
        verbose_name="Ingestion Timestamp",
        accessor="ingestion_ts",
        orderable=False,
    )

    state = IngestionStateColumn(
        verbose_name="State",
        accessor="state",
        orderable=False,
    )

    object_type = DataTypeColumn(
        verbose_name="Data Type",
        accessor="data_type",
        orderable=False,
    )

    request_id = tables.Column(
        verbose_name="Request ID",
        accessor="request_id",
        orderable=False,
    )

    producer = tables.Column(
        verbose_name="Producer",
        empty_values=(),
        orderable=False,
    )

    sdk = tables.Column(
        verbose_name="SDK",
        empty_values=(),
        orderable=False,
    )

    actions = tables.TemplateColumn(
        template_code=INGESTION_LOGS_TABLE_ACTIONS_TEMPLATE,
        verbose_name="",
        orderable=False,
    )

    class Meta:
        """Meta class."""

        attrs = {
            "class": "table table-hover table-striped table-condensed",
            "td": {"class": "align-middle"},
        }
        fields = (
            "ingestion_ts",
            "object_type",
            "state",
            "producer",
            "sdk",
            "request_id",
            "actions",
        )
        empty_text = "No ingestion logs to display"
        footer = False

    def render_producer(self, record):
        """Renders the producer."""
        return f"{record.producer_app_name}/{record.producer_app_version}"

    def render_sdk(self, record):
        """Renders the SDK."""
        return f"{record.sdk_name}/{record.sdk_version}"
