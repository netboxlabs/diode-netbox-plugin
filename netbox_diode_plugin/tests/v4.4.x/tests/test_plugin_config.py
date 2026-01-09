#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_diode_plugin.plugin_config import _parse_diode_target, get_diode_auth_introspect_url, get_diode_user

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

    def test__parse_diode_target_handles_ftp_prefix(self):
        """Check that _parse_diode_target raises an error when the target contains ftp://."""
        with pytest.raises(ValueError):
            _parse_diode_target("ftp://localhost:8081")

    def test__parse_diode_target_parses_authority_correctly(self):
        """Check that _parse_diode_target parses the authority correctly."""
        authority, path, tls_verify = _parse_diode_target("grpc://localhost:8081")
        assert authority == "localhost:8081"
        assert path == ""
        assert tls_verify is False

    def test__parse_diode_target_adds_default_port_if_missing(self):
        """Check that _parse_diode_target adds the default port if missing."""
        authority, _, _ = _parse_diode_target("grpc://localhost")
        assert authority == "localhost:80"
        authority, _, _ = _parse_diode_target("http://localhost")
        assert authority == "localhost:80"
        authority, _, _ = _parse_diode_target("grpcs://localhost")
        assert authority == "localhost:443"
        authority, _, _ = _parse_diode_target("https://localhost")
        assert authority == "localhost:443"

    def test__parse_diode_target_parses_path_correctly(self):
        """Check that _parse_diode_target parses the path correctly."""
        _, path, _ = _parse_diode_target("grpc://localhost:8081/my/path")
        assert path == "/my/path"

    def test__parse_diode_target_handles_no_path(self):
        """Check that _parse_diode_target handles no path."""
        _, path, _ = _parse_diode_target("grpc://localhost:8081")
        assert path == ""

    def test__parse_diode_target_parses_tls_verify_correctly(self):
        """Check that _parse_diode_target parses tls_verify correctly."""
        _, _, tls_verify = _parse_diode_target("grpc://localhost:8081")
        assert tls_verify is False
        _, _, tls_verify = _parse_diode_target("http://localhost:8081")
        assert tls_verify is False
        _, _, tls_verify = _parse_diode_target("grpcs://localhost:8081")
        assert tls_verify is True
        _, _, tls_verify = _parse_diode_target("https://localhost:8081")
        assert tls_verify is True
