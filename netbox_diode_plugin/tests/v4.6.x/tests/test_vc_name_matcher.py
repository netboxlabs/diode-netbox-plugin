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


class VirtualChassisAdoptionTests(TestCase):
    """Master-bearing VC creates must adopt same-named masterless VCs."""

    @classmethod
    def setUpTestData(cls):
        """Seed a masterless VC (bulk member-first aftermath) and its master-to-be."""
        site = Site.objects.create(name="vca-site", slug="vca-site")
        mfr = Manufacturer.objects.create(name="vca-mfr", slug="vca-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vca-dt", slug="vca-dt")
        role = DeviceRole.objects.create(name="vca-role", slug="vca-role")
        cls.vc = VirtualChassis.objects.create(name="vca-stack")
        cls.master = Device.objects.create(
            name="vca-sw1", site=site, device_type=dt, role=role
        )

    def _apply_create(self, data):
        from types import SimpleNamespace

        from netbox_diode_plugin.api.applier import apply_changeset
        from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType

        cs = ChangeSet(changes=[Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.virtualchassis",
            ref_id="vc1",
            data=data,
        )])
        request = SimpleNamespace(user=None)
        return apply_changeset(cs, request)

    def test_master_bearing_create_adopts_masterless_vc(self):
        """No duplicate VC; master dropped until the device is a member."""
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master)  # not a member yet -> master deferred

    def test_adoption_sets_master_once_member(self):
        """When the master device already belongs to the VC, adoption sets master."""
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=self.vc, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)

    def test_unique_master_match_leaves_masterless_decoy_untouched(self):
        """A same-named masterless decoy must not divert a CREATE from the row it already masters."""
        mastered = VirtualChassis.objects.create(name="vca-mastered", master=self.master)
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=mastered, vc_position=1
        )
        decoy = VirtualChassis.objects.create(name="vca-mastered")  # masterless, same name
        self._apply_create({
            "name": "vca-mastered",
            "master": self.master.pk,
            "description": "adopted-update",
        })
        self.assertEqual(VirtualChassis.objects.filter(name="vca-mastered").count(), 2)
        mastered.refresh_from_db()
        self.assertEqual(mastered.description, "adopted-update")
        decoy.refresh_from_db()
        self.assertEqual(decoy.description, "")
        self.assertIsNone(decoy.master)

    def test_two_pass_convergence_sets_master(self):
        """A second, identical CREATE apply after the device becomes a member finally sets master."""
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master)  # first pass: adopted, master deferred

        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=self.vc, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)  # second pass converges

    def test_adoption_prefers_row_with_membership_over_oldest(self):
        """Among several same-named masterless rows, adopt the one the master already belongs to."""
        older = self.vc  # from setUpTestData: masterless, empty, lowest pk
        newer = VirtualChassis.objects.create(name="vca-stack")  # masterless, higher pk
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=newer, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 2)
        newer.refresh_from_db()
        self.assertEqual(newer.master_id, self.master.pk)
        older.refresh_from_db()
        self.assertIsNone(older.master)
