#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

from django.test import TestCase

from netbox_diode_plugin.version import version_display, version_semver


class VersionTestCase(TestCase):
    """Test case for the version module."""

    def test_version(self):
        """Check the injected semver."""
        assert version_semver() == "0.0.0"

    def test_version_display(self):
        """Check the injected display."""
        assert version_display() == "v0.0.0-dev-unknown"
