#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest.mock import patch

from django.test import TestCase

from netbox_diode_plugin.plugin_config import (
    get_diode_user_types,
    get_diode_user_types_with_labels,
    get_diode_username_for_user_type,
    get_diode_usernames,
)


class PluginConfigTestCase(TestCase):
    """Test case for plugin config helpers."""

    def test_get_diode_user_types(self):
        """Test get_diode_user_types function."""
        expected = ("diode_to_netbox", "netbox_to_diode", "diode")
        self.assertEqual(get_diode_user_types(), expected)

    def test_get_diode_user_types_with_labels(self):
        """Test get_diode_user_types_with_labels function."""
        expected = (
            ("diode_to_netbox", "Diode to NetBox"),
            ("netbox_to_diode", "NetBox to Diode"),
            ("diode", "Diode"),
        )
        self.assertEqual(get_diode_user_types_with_labels(), expected)

    @patch("netbox_diode_plugin.plugin_config.get_plugin_config")
    def test_get_diode_usernames(self, mock_get_plugin_config):
        """Test get_diode_usernames function."""
        mock_usernames = {
            "diode_to_netbox_username": "diode-to-netbox",
            "netbox_to_diode_username": "netbox-to-diode",
            "diode_username": "diode-ingestion",
        }
        mock_get_plugin_config.side_effect = lambda plugin, key: mock_usernames[key]
        expected = {
            "diode_to_netbox": "diode-to-netbox",
            "netbox_to_diode": "netbox-to-diode",
            "diode": "diode-ingestion",
        }
        self.assertEqual(get_diode_usernames(), expected)

    @patch("netbox_diode_plugin.plugin_config.get_plugin_config")
    def test_get_diode_username_for_user_type(self, mock_get_plugin_config):
        """Test get_diode_username_for_user_type function."""
        mock_usernames = {
            "diode_to_netbox_username": "diode-to-netbox",
            "netbox_to_diode_username": "netbox-to-diode",
            "diode_username": "diode-ingestion",
        }
        mock_get_plugin_config.side_effect = lambda plugin, key: mock_usernames[key]
        self.assertEqual(
            get_diode_username_for_user_type("netbox_to_diode"), "netbox-to-diode"
        )
        self.assertEqual(
            get_diode_username_for_user_type("diode_to_netbox"), "diode-to-netbox"
        )
        self.assertEqual(get_diode_username_for_user_type("diode"), "diode-ingestion")
