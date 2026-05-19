#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase

from netbox_diode_plugin.models import Setting


class SettingModelTestCase(TestCase):
    """Test case for the models."""

    def test_validators(self):
        """Check Setting model field validators are functional."""
        setting = Setting(diode_target="http://localhost:8080")

        with self.assertRaises(ValidationError):
            setting.clean_fields()

    def test_str(self):
        """Check Setting model string representation."""
        setting = Setting(diode_target="http://localhost:8080")
        self.assertEqual(str(setting), "")

    def test_absolute_url(self):
        """Check Setting model absolute URL."""
        setting = Setting()
        self.assertEqual(setting.get_absolute_url(), "/netbox/plugins/diode/settings/")

    def test_branch_id_field_exists(self):
        """Check Setting model has branch_id field."""
        setting = Setting(diode_target="grpc://localhost:8080/diode")
        self.assertIsNone(setting.branch_id)

        # Set branch_id
        setting.branch_id = 123
        self.assertEqual(setting.branch_id, 123)

    def test_branch_property_returns_none_when_no_branch_id(self):
        """Check branch property returns None when branch_id is not set."""
        setting = Setting(diode_target="grpc://localhost:8080/diode")
        self.assertIsNone(setting.branch)

    def test_branch_property_returns_none_when_plugin_not_installed(self):
        """Check branch property returns None when branching plugin is not installed."""
        setting = Setting(diode_target="grpc://localhost:8080/diode", branch_id=123)

        # Mock the import to simulate plugin not being available
        with mock.patch.dict('sys.modules', {'netbox_branching.models': None}):
            self.assertIsNone(setting.branch)

    def test_branch_property_returns_branch_when_available(self):
        """Check branch property returns Branch object when available."""
        if not apps.is_installed("netbox_branching"):
            self.skipTest("netbox_branching plugin not installed")

        from netbox_branching.models import Branch

        # Create a test branch
        branch = Branch.objects.create(name="test-branch")

        setting = Setting(diode_target="grpc://localhost:8080/diode", branch_id=branch.id)

        # Check branch property returns the correct branch
        self.assertEqual(setting.branch.id, branch.id)
        self.assertEqual(setting.branch.name, "test-branch")

        # Clean up
        branch.delete()

    def test_branch_setter(self):
        """Check branch setter updates branch_id."""
        if not apps.is_installed("netbox_branching"):
            self.skipTest("netbox_branching plugin not installed")

        from netbox_branching.models import Branch

        # Create a test branch
        branch = Branch.objects.create(name="test-branch-setter")

        setting = Setting(diode_target="grpc://localhost:8080/diode")

        # Use setter to assign branch
        setting.branch = branch
        self.assertEqual(setting.branch_id, branch.id)

        # Set to None
        setting.branch = None
        self.assertIsNone(setting.branch_id)

        # Clean up
        branch.delete()

    def test_branch_schema_id_property(self):
        """Check branch_schema_id property returns schema_id when branch is set."""
        if not apps.is_installed("netbox_branching"):
            self.skipTest("netbox_branching plugin not installed")

        from netbox_branching.models import Branch

        # Create a test branch
        branch = Branch.objects.create(name="test-branch-schema")

        setting = Setting(diode_target="grpc://localhost:8080/diode", branch_id=branch.id)

        # Check branch_schema_id returns the schema_id
        self.assertEqual(setting.branch_schema_id, branch.schema_id)

        # Check it returns None when no branch
        setting.branch_id = None
        self.assertIsNone(setting.branch_schema_id)

        # Clean up
        branch.delete()
