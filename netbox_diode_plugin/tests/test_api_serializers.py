#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest.mock import MagicMock

from dcim.models import Site
from django.test import TestCase
from extras.api.serializers import TagSerializer
from extras.models import Tag

from netbox_diode_plugin.api.serializers import DiodeIPAddressSerializer, DiodeSiteSerializer, get_diode_serializer


class SerializersTestCase(TestCase):
    """Test case for the serializers."""

    def test_get_diode_serializer(self):
        """Check the diode serializer is found."""
        site = Site.objects.create(name="test")
        assert get_diode_serializer(site) == DiodeSiteSerializer

        tag = Tag.objects.create(name="test")
        assert get_diode_serializer(tag) == TagSerializer


    def test_get_assigned_object_returns_none_if_no_assigned_object(self):
        """Check the assigned object is None if not provided."""
        obj = MagicMock()
        obj.assigned_object = None
        serializer = DiodeIPAddressSerializer()
        result = serializer.get_assigned_object(obj)
        self.assertIsNone(result)
