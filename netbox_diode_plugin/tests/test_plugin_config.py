#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_diode_plugin.plugin_config import (
    get_diode_auth_introspect_url,
    get_diode_user,
)

User = get_user_model()


class PluginConfigTestCase(TestCase):
    """Test case for plugin config helpers."""

    def test_get_diode_auth_introspect_url(self):
        """Test get_diode_auth_introspect_url function."""
        expected = "http://localhost:8080/diode/auth/introspect"
        self.assertEqual(get_diode_auth_introspect_url(), expected)

    def test_get_diode_user(self):
        """Test get_diode_user function."""
        diode_user = get_diode_user()
        expected_diode_user = User.objects.get(username="diode")
        self.assertEqual(diode_user, expected_diode_user)

