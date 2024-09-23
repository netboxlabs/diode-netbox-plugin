#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.test import TestCase

from netbox_diode_plugin.forms import SettingsForm
from netbox_diode_plugin.models import Setting


class SettingsFormTestCase(TestCase):
    """Test case for the SettingsForm."""

    def setUp(self):
        """Set up the test case."""
        self.setting = Setting.objects.create(diode_target="grpc://localhost:8080/diode")

    def test_form_initialization_with_override_allowed(self):
        """Test form initialization when override is allowed."""
        with mock.patch("netbox_diode_plugin.forms.netbox_settings") as mock_settings:
            mock_settings.PLUGINS_CONFIG = {
                "netbox_diode_plugin": {
                    "disallow_diode_target_override": False
                }
            }
            form = SettingsForm(instance=self.setting)
            self.assertFalse(form.fields["diode_target"].disabled)
            self.assertNotIn("This field is not allowed to be overridden.", form.fields["diode_target"].help_text)

    def test_form_initialization_with_override_disallowed(self):
        """Test form initialization when override is disallowed."""
        with mock.patch("netbox_diode_plugin.forms.netbox_settings") as mock_settings:
            mock_settings.PLUGINS_CONFIG = {
                "netbox_diode_plugin": {
                    "disallow_diode_target_override": True
                }
            }
            form = SettingsForm(instance=self.setting)
            self.assertTrue(form.fields["diode_target"].disabled)
            self.assertEqual("This field is not allowed to be overridden.", form.fields["diode_target"].help_text)
