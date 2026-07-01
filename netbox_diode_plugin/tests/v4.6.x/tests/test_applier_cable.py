#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Cable apply integration tests."""

from dcim.models import (
    Cable,
    CableTermination,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.test import TestCase

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType


class CableApplyTestCase(TestCase):
    """A cable CREATE materializes two CableTermination rows via CableSerializer."""

    @classmethod
    def setUpTestData(cls):
        """Set up test fixtures."""
        mfr = Manufacturer.objects.create(name="MFR-Cable", slug="mfr-cable")
        dt = DeviceType.objects.create(manufacturer=mfr, model="DT-Cable", slug="dt-cable")
        role = DeviceRole.objects.create(name="Role-Cable", slug="role-cable")
        site = Site.objects.create(name="Site-Cable", slug="site-cable")
        dev_a = Device.objects.create(name="Cable Device A", device_type=dt, role=role, site=site)
        dev_b = Device.objects.create(name="Cable Device B", device_type=dt, role=role, site=site)
        dev_c = Device.objects.create(name="Cable Device C", device_type=dt, role=role, site=site)
        cls.iface_a = Interface.objects.create(device=dev_a, name="Cable Iface A", type="1000base-t")
        cls.iface_b = Interface.objects.create(device=dev_b, name="Cable Iface B", type="1000base-t")
        cls.iface_c = Interface.objects.create(device=dev_c, name="Cable Iface C", type="1000base-t")

    def _cable_change(self, a_pk, b_pk, label="Cable 1"):
        return Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.cable",
            ref_id="new_object:dcim.cable:c1",
            data={
                "status": "connected",
                "label": label,
                "a_terminations": [{"object_type": "dcim.interface", "object_id": a_pk}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": b_pk}],
            },
            new_refs=[],
        )

    def test_cable_create_materializes_two_terminations(self):
        """Cable create materializes two terminations."""
        cs = ChangeSet(id="cs-cable-1", changes=[self._cable_change(self.iface_a.pk, self.iface_b.pk)])
        apply_changeset(cs, request=None)

        cable = Cable.objects.get(label="Cable 1")
        terms = CableTermination.objects.filter(cable=cable)
        self.assertEqual(terms.count(), 2)
        term_ids = {(t.cable_end, t.termination_id) for t in terms}
        self.assertIn(("A", self.iface_a.pk), term_ids)
        self.assertIn(("B", self.iface_b.pk), term_ids)

    def test_already_cabled_interface_fails_change_with_clean_error(self):
        """Already cabled interface fails change with clean error."""
        # First cable connects A<->B. A second, distinct-termination-set cable
        # that reuses interface A (now already cabled) cannot be matched by
        # CableTerminationSetMatcher (different termination set: A<->C vs A<->B),
        # so it falls through to CableSerializer.is_valid()/Cable.clean(), which
        # rejects it because interface A already has a cable connection.
        cs1 = ChangeSet(id="cs-cable-a", changes=[self._cable_change(self.iface_a.pk, self.iface_b.pk)])
        apply_changeset(cs1, request=None)

        cs2 = ChangeSet(id="cs-cable-b", changes=[self._cable_change(self.iface_a.pk, self.iface_c.pk, label="Cable 2")])
        with self.assertRaises(Exception) as ctx:
            apply_changeset(cs2, request=None)
        msg = str(ctx.exception).lower()
        self.assertTrue(
            any(s in msg for s in ("already", "cabled", "connect", "duplicate termination")),
            f"expected a cable-conflict clean() message, got: {ctx.exception}",
        )
        self.assertFalse(Cable.objects.filter(label="Cable 2").exists())
