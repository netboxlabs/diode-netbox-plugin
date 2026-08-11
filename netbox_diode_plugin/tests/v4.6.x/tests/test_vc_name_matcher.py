"""Unit tests for masterless VirtualChassis name matching."""
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import TestCase

from netbox_diode_plugin.api.matcher import (
    find_existing_object,
    fingerprints,
    get_model_matchers,
    requires_pre_save_match,
)


class VirtualChassisNameMatcherTests(TestCase):
    """Name-only VC payloads must bind existing VCs; master-bearing ones must not."""

    @classmethod
    def setUpTestData(cls):
        """Seed two same-named VCs (older mastered, newer empty) plus a distinct one."""
        site = Site.objects.create(name="vcm-site", slug="vcm-site")
        mfr = Manufacturer.objects.create(name="vcm-mfr", slug="vcm-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vcm-dt", slug="vcm-dt")
        role = DeviceRole.objects.create(name="vcm-role", slug="vcm-role")
        cls.master = Device.objects.create(
            name="vcm-sw1", site=site, device_type=dt, role=role
        )
        cls.vc_old = VirtualChassis.objects.create(name="vcm-stack", master=cls.master)
        Device.objects.filter(pk=cls.master.pk).update(
            virtual_chassis=cls.vc_old, vc_position=1
        )
        cls.vc_dup = VirtualChassis.objects.create(name="vcm-stack")  # newer, masterless
        cls.vc_other = VirtualChassis.objects.create(name="vcm-other")

    def test_matcher_registration_and_order(self):
        """The logical name matcher precedes the auto-derived unique_master."""
        names = [m.name for m in get_model_matchers(VirtualChassis)]
        self.assertEqual(names[0], "logical_vc_name_no_master")
        self.assertIn("unique_master", names)

    def test_name_only_payload_matches_mastered_vc(self):
        """A masterless payload binds by name even when the DB row HAS a master."""
        found = find_existing_object({"name": "vcm-other"}, "dcim.virtualchassis")
        self.assertEqual(found, self.vc_other)
        found = find_existing_object({"name": "vcm-stack"}, "dcim.virtualchassis")
        self.assertEqual(found, self.vc_old)  # oldest pk wins over the newer duplicate

    def test_explicit_null_master_counts_as_masterless(self):
        """master: None gates the same as an absent master key."""
        found = find_existing_object(
            {"name": "vcm-other", "master": None}, "dcim.virtualchassis"
        )
        self.assertEqual(found, self.vc_other)

    def test_master_bearing_payload_ignores_name_matcher(self):
        """With master present, unique_master is authoritative."""
        found = find_existing_object(
            {"name": "totally-different-name", "master": self.master.pk},
            "dcim.virtualchassis",
        )
        self.assertEqual(found, self.vc_old)

    def test_no_hit_returns_none(self):
        """Unknown name with no master creates (returns None)."""
        self.assertIsNone(
            find_existing_object({"name": "vcm-missing"}, "dcim.virtualchassis")
        )

    def test_non_string_name_never_reaches_filter(self):
        """A malformed name is ignored by the matcher, not raised."""
        self.assertIsNone(
            find_existing_object({"name": ["vcm-other"]}, "dcim.virtualchassis")
        )

    def test_fingerprint_emitted_regardless_of_master(self):
        """Name-only and master-bearing entities share the name fingerprint."""
        fp_no_master = fingerprints({"name": "x-stack"}, "dcim.virtualchassis")
        fp_master = fingerprints(
            {"name": "x-stack", "master": 1}, "dcim.virtualchassis"
        )
        shared = set(fp_no_master) & set(fp_master)
        self.assertTrue(shared, "expected a shared name-keyed fingerprint")

    def test_requires_pre_save_match(self):
        """VC creates must find-first at apply time (no DB name constraint)."""
        self.assertTrue(requires_pre_save_match("dcim.virtualchassis"))
