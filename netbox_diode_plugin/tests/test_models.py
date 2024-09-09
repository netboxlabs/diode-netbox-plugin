#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from netbox_diode_plugin.models import Setting


class SettingModelTestCase(TestCase):
    """Test case for the models."""

    def test_validators(self):
        """Check Setting model field validators are functional."""
        setting = Setting(reconciler_target="http://localhost:8080")

        with self.assertRaises(ValidationError):
            setting.clean_fields()


    def test_str(self):
        """Check Setting model string representation."""
        setting = Setting(reconciler_target="http://localhost:8080")
        self.assertEqual(str(setting), "")


    def test_absolute_url(self):
        """Check Setting model absolute URL."""
        setting = Setting()
        self.assertEqual(setting.get_absolute_url(), "/netbox/plugins/diode/settings/")
