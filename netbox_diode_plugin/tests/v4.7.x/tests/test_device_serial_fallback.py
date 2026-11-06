#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Tests for conservative device reconciliation by site, manufacturer, and serial."""

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.test import TestCase

from netbox_diode_plugin.api.common import ChangeType
from netbox_diode_plugin.api.differ import generate_changeset


class DeviceSerialFallbackTestCase(TestCase):
    """A unique serial within a site and manufacturer identifies an existing device."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Lamont", slug="lamont")
        cls.role = DeviceRole.objects.create(name="Router", slug="router")
        cls.manufacturer = Manufacturer.objects.create(name="Cisco", slug="cisco")
        cls.device_type = DeviceType.objects.create(
            manufacturer=cls.manufacturer, model="Lamont Router", slug="lamont-router",
        )
        cls.device = Device.objects.create(
            name="Lamont",
            serial="2F400131230C",
            site=cls.site,
            role=cls.role,
            device_type=cls.device_type,
        )

    def test_hostname_change_updates_existing_device(self):
        """A renamed device at the same site retains its NetBox identity."""
        payload = {
            "name": "lamont-mt",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.UPDATE)
        self.assertEqual(device_writes[0].object_id, self.device.pk)
        self.assertEqual(device_writes[0].data["name"], "lamont-mt")

    def test_same_serial_different_site_creates_device(self):
        """A matching serial at another site must not update the original."""
        Site.objects.create(name="Browne", slug="browne")

        payload = {
            "name": "browne-mt",
            "serial": "2F400131230C",
            "site": {"name": "Browne"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.CREATE)
        self.assertIsNone(device_writes[0].object_id)
        self.assertEqual(device_writes[0].data["name"], "browne-mt")

    def test_same_serial_different_manufacturer_creates_device(self):
        """A serial match from another manufacturer must not update the device."""
        juniper = Manufacturer.objects.create(name="Juniper", slug="juniper")
        DeviceType.objects.create(
            manufacturer=juniper,
            model="Juniper Router",
            slug="juniper-router",
        )

        payload = {
            "name": "lamont-juniper",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Juniper Router",
                "manufacturer": {"name": "Juniper"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.CREATE)
        self.assertIsNone(device_writes[0].object_id)
        self.assertEqual(device_writes[0].data["name"], "lamont-juniper")

    def test_ambiguous_serial_match_creates_device(self):
        """Two matching devices must not result in an arbitrary update."""
        second_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model="Alternate Cisco Router",
            slug="alternate-cisco-router",
        )
        Device.objects.create(
            name="Lamont-duplicate",
            serial="2F400131230C",
            site=self.site,
            role=self.role,
            device_type=second_type,
        )

        payload = {
            "name": "lamont-mt",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.CREATE)
        self.assertIsNone(device_writes[0].object_id)
        self.assertEqual(device_writes[0].data["name"], "lamont-mt")

    def test_unknown_serial_creates_device(self):
        """An unusable serial must not trigger fallback reconciliation."""
        payload = {
            "name": "lamont-unknown",
            "serial": "unknown",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.CREATE)
        self.assertIsNone(device_writes[0].object_id)
        self.assertEqual(device_writes[0].data["name"], "lamont-unknown")

    def test_same_manufacturer_different_model_updates_device(self):
        """A model change must not prevent a unique serial match."""
        DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model="Updated Cisco Router",
            slug="updated-cisco-router",
        )

        payload = {
            "name": "lamont-mt",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Updated Cisco Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.UPDATE)
        self.assertEqual(device_writes[0].object_id, self.device.pk)
        self.assertEqual(device_writes[0].data["name"], "lamont-mt")

    def test_explicit_netbox_id_takes_precedence_over_serial(self):
        """Explicit source-match identity wins over a unique serial match."""
        target = Device.objects.create(
            name="Explicit-target",
            serial="DIFFERENT-SERIAL",
            site=self.site,
            role=self.role,
            device_type=self.device_type,
        )

        payload = {
            "name": "lamont-mt",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
            "metadata": {
                "source_match": {"netbox_id": target.pk},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.UPDATE)
        self.assertEqual(device_writes[0].object_id, target.pk)
        self.assertNotEqual(device_writes[0].object_id, self.device.pk)
        self.assertEqual(device_writes[0].data["name"], "lamont-mt")

    def test_missing_serial_creates_device(self):
        """A missing serial must not trigger fallback reconciliation."""
        payload = {
            "name": "lamont-no-serial",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        changes = generate_changeset(payload, "dcim.device").change_set.changes
        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.CREATE)
        self.assertIsNone(device_writes[0].object_id)
        self.assertEqual(device_writes[0].data["name"], "lamont-no-serial")

    def test_serial_fallback_with_cache_enabled(self):
        """Serial reconciliation works with caching on without caching its result."""
        from unittest import mock

        from django.core.cache import cache as django_cache
        from netbox_diode_plugin.api.matcher import _find_obj_cache_key

        payload = {
            "name": "lamont-cache-test",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        django_cache.clear()
        try:
            with mock.patch(
                "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
                return_value=5,
            ):
                changes = generate_changeset(
                    payload, "dcim.device"
                ).change_set.changes

                device_writes = [
                    change for change in changes
                    if change.object_type == "dcim.device"
                    and change.change_type != ChangeType.NOOP
                ]

                self.assertEqual(len(device_writes), 1)
                self.assertEqual(device_writes[0].change_type, ChangeType.UPDATE)
                self.assertEqual(device_writes[0].object_id, self.device.pk)

                cache_key = _find_obj_cache_key(
                    {
                        "name": "lamont-cache-test",
                        "serial": "2F400131230C",
                        "site": self.site.pk,
                        "role": self.role.pk,
                        "device_type": self.device_type.pk,
                    },
                    "dcim.device",
                )
                self.assertIsNotNone(cache_key)
                self.assertIsNone(django_cache.get(cache_key))
        finally:
            django_cache.clear()

    def test_normal_name_match_takes_precedence_over_serial(self):
        """A normal name match wins even when serial identifies another device."""
        from unittest import mock

        name_match = Device.objects.create(
            name="lamont-mt",
            serial="DIFFERENT-SERIAL",
            site=self.site,
            role=self.role,
            device_type=self.device_type,
        )

        payload = {
            "name": "lamont-mt",
            "serial": "2F400131230C",
            "site": {"name": "Lamont"},
            "role": {"name": "Router"},
            "device_type": {
                "model": "Lamont Router",
                "manufacturer": {"name": "Cisco"},
            },
        }

        with mock.patch(
            "netbox_diode_plugin.api.transformer.find_device_by_serial"
        ) as serial_fallback:
            changes = generate_changeset(
                payload, "dcim.device"
            ).change_set.changes

        device_writes = [
            change for change in changes
            if change.object_type == "dcim.device"
            and change.change_type != ChangeType.NOOP
        ]

        self.assertEqual(len(device_writes), 1)
        self.assertEqual(device_writes[0].change_type, ChangeType.UPDATE)
        self.assertEqual(device_writes[0].object_id, name_match.pk)
        self.assertNotEqual(device_writes[0].object_id, self.device.pk)
        serial_fallback.assert_not_called()
