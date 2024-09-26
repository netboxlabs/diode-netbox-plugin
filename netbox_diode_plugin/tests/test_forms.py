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
        with mock.patch("netbox_diode_plugin.forms.get_plugin_config") as mock_get_plugin_config:
            mock_get_plugin_config.return_value = None
            form = SettingsForm(instance=self.setting)
            mock_get_plugin_config.assert_called_with("netbox_diode_plugin", "diode_target_override")
            self.assertFalse(form.fields["diode_target"].disabled)
            self.assertNotIn("This field is not allowed to be modified.", form.fields["diode_target"].help_text)

    def test_form_initialization_with_diode_targer_override(self):
        """Test form initialization when override is disallowed."""
        with mock.patch("netbox_diode_plugin.forms.get_plugin_config") as mock_get_plugin_config:
            mock_get_plugin_config.return_value = "grpc://localhost:8080/diode"
            form = SettingsForm(instance=self.setting)
            mock_get_plugin_config.assert_called_with("netbox_diode_plugin", "diode_target_override")
            self.assertTrue(form.fields["diode_target"].disabled)
            self.assertEqual("This field is not allowed to be modified.", form.fields["diode_target"].help_text)
