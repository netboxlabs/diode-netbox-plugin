#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Serializers."""

from netbox.api.serializers import NetBoxModelSerializer

from netbox_diode_plugin.models import Setting


class SettingSerializer(NetBoxModelSerializer):
    """Setting Serializer."""

    class Meta:
        """Meta class."""

        model = Setting
        fields = (
            "id",
            "diode_target",
            "custom_fields",
            "created",
            "last_updated",
        )
