#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
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


class PluginCompatibilityTestCase(TestCase):
    """
    Guards for the plugin's own compatibility declarations.

    A stale max_version does not break this test suite (it runs with the
    plugin already loaded) - it breaks NetBox upgrades in the field, which
    is exactly how the 4.6->4.7 gap was noticed. This ties the declared
    range to whatever NetBox version the suite runs against.
    """

    def test_declared_version_range_covers_running_netbox(self):
        """The running NetBox version must fall inside min/max_version."""
        from django.conf import settings
        from packaging import version

        from netbox_diode_plugin import NetBoxDiodePluginConfig

        current = version.parse(settings.RELEASE.version)
        self.assertGreaterEqual(
            current,
            version.parse(NetBoxDiodePluginConfig.min_version),
            "running NetBox is older than the plugin's min_version",
        )
        self.assertLessEqual(
            current,
            version.parse(NetBoxDiodePluginConfig.max_version),
            "running NetBox is newer than the plugin's max_version - "
            "bump max_version (and the README compatibility table) before "
            "supporting a new NetBox release",
        )

    def test_diode_user_is_not_superuser_and_cannot_log_in(self):
        """
        The service user stays unprivileged at the account level.

        Superuser was revoked for cause (migration
        0005_revoke_superuser_status); NetBox 4.7's permission needs are
        met by explicit ObjectPermissions instead. The password must be
        unusable so no login path can ever authenticate as this account.
        """
        diode_user = get_diode_user()
        self.assertFalse(diode_user.is_superuser)
        self.assertFalse(diode_user.has_usable_password())

    def test_deactivated_diode_user_stays_deactivated(self):
        """Deactivation is an operator kill switch and must be respected."""
        get_diode_user()
        User.objects.filter(username="diode").update(is_active=False)
        diode_user = get_diode_user()
        self.assertFalse(diode_user.is_active)

    def test_provisioned_view_permission_covers_all_object_types(self):
        """
        NetBox 4.7 scopes attribute-based reference resolution by view permission.

        The provisioned grant must span every object type, or references
        to a type NetBox adds later fail as 'related object not found' -
        a data-shaped symptom pointing away from the real cause.
        """
        from core.models import ObjectType
        from users.models import ObjectPermission

        from netbox_diode_plugin.provisioning import (
            VIEW_PERMISSION_NAME,
            provision_diode_permissions,
        )

        provision_diode_permissions()
        permission = ObjectPermission.objects.get(name=VIEW_PERMISSION_NAME)
        self.assertTrue(permission.enabled)
        self.assertEqual(permission.actions, ["view"])
        self.assertIsNone(permission.constraints)
        self.assertIn(get_diode_user(), permission.users.all())
        self.assertEqual(
            set(permission.object_types.values_list("pk", flat=True)),
            set(ObjectType.objects.values_list("pk", flat=True)),
        )

    def test_diode_user_can_resolve_by_attributes_and_add_macaddress(self):
        """The two NetBox 4.7 permission gates are open for the service user."""
        from dcim.models import Site
        from utilities.api import get_related_object_by_attrs

        from netbox_diode_plugin.provisioning import provision_diode_permissions

        provision_diode_permissions()
        site = Site.objects.create(name="Perm Probe Site", slug="perm-probe-site")
        diode_user = get_diode_user()

        resolved = get_related_object_by_attrs(
            Site.objects.all(), {"name": "Perm Probe Site"}, user=diode_user
        )
        self.assertEqual(resolved, site)
        self.assertTrue(diode_user.has_perm("dcim.add_macaddress"))
