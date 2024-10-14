#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_diode_plugin.forms import SettingsForm, SetupForm
from netbox_diode_plugin.models import Setting

User = get_user_model()


class SettingsFormTestCase(TestCase):
    """Test case for the SettingsForm."""

    def setUp(self):
        """Set up the test case."""
        self.setting = Setting.objects.create(
            diode_target="grpc://localhost:8080/diode"
        )

    def test_form_initialization_with_override_allowed(self):
        """Test form initialization when override is allowed."""
        with mock.patch(
            "netbox_diode_plugin.forms.get_plugin_config"
        ) as mock_get_plugin_config:
            mock_get_plugin_config.return_value = None
            form = SettingsForm(instance=self.setting)
            mock_get_plugin_config.assert_called_with(
                "netbox_diode_plugin", "diode_target_override"
            )
            self.assertFalse(form.fields["diode_target"].disabled)
            self.assertNotIn(
                "This field is not allowed to be modified.",
                form.fields["diode_target"].help_text,
            )

    def test_form_initialization_with_diode_targer_override(self):
        """Test form initialization when override is disallowed."""
        with mock.patch(
            "netbox_diode_plugin.forms.get_plugin_config"
        ) as mock_get_plugin_config:
            mock_get_plugin_config.return_value = "grpc://localhost:8080/diode"
            form = SettingsForm(instance=self.setting)
            mock_get_plugin_config.assert_called_with(
                "netbox_diode_plugin", "diode_target_override"
            )
            self.assertTrue(form.fields["diode_target"].disabled)
            self.assertEqual(
                "This field is not allowed to be modified.",
                form.fields["diode_target"].help_text,
            )


class SetupFormTestCase(TestCase):
    """Test case for the SetupForm."""

    def setUp(self):
        """Set up the test case."""
        self.users = {
            "diode_to_netbox": {
                "username": "diode-to-netbox",
                "api_key_env_var_name": "DIODE_TO_NETBOX_API_KEY",
                "predefined_api_key": None,
                "api_key": None,
                "user": None,
            },
            "diode": {
                "username": "diode-ingestion",
                "api_key_env_var_name": "DIODE_API_KEY",
                "predefined_api_key": "5a52c45ee8231156cb620d193b0291912dd15433",
                "api_key": None,
                "user": User.objects.get(username="diode-ingestion"),
            },
        }

    def test_form_initialization(self):
        """Test form initialization with given users."""
        form = SetupForm(users=self.users)
        self.assertIn("diode_to_netbox_api_key", form.fields)
        self.assertFalse(form.fields["diode_to_netbox_api_key"].disabled)
        self.assertIn("diode_api_key", form.fields)
        self.assertTrue(form.fields["diode_api_key"].disabled)
        self.assertEqual(
            form.fields["diode_api_key"].initial,
            self.users["diode"]["predefined_api_key"],
        )
