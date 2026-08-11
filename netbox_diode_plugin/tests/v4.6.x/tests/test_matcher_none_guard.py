"""Unit tests for the all-None matcher guard and None-safe data preparation."""
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Location,
    MACAddress,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleType,
    Rack,
    Site,
    VirtualChassis,
)
from django.test import TestCase

from netbox_diode_plugin.api.matcher import find_existing_object, fingerprints, get_model_matchers


def _matcher(model_class, name):
    for m in get_model_matchers(model_class):
        if getattr(m, "name", None) == name:
            return m
    raise AssertionError(f"matcher {name} not found")


class AllNoneGuardTests(TestCase):
    """All-None lookups must skip the matcher; partial nulls keep matching."""

    @classmethod
    def setUpTestData(cls):
        """Seed rows that today's NULL-lookup hijacks would wrongly bind."""
        cls.site = Site.objects.create(name="ng-site", slug="ng-site")
        mfr = Manufacturer.objects.create(name="ng-mfr", slug="ng-mfr")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="ng-dt", slug="ng-dt")
        role = DeviceRole.objects.create(name="ng-role", slug="ng-role")
        cls.dev = Device.objects.create(name="ng-dev", site=cls.site, device_type=cls.dt, role=role)
        cls.mt = ModuleType.objects.create(manufacturer=mfr, model="ng-mt")
        bay = ModuleBay.objects.create(device=cls.dev, name="ng-bay1")
        # a module with NULL asset_tag — the row the unguarded matcher hijacks
        cls.module = Module.objects.create(device=cls.dev, module_bay=bay, module_type=cls.mt)
        # a masterless VC named differently from any payload below
        cls.vc = VirtualChassis.objects.create(name="ng-vc-existing")
        # a rack with NULL location — the multi-field clear-FK dedupe case
        cls.rack = Rack.objects.create(name="ng-rack", site=cls.site)

    def test_single_field_null_lookup_skips_matcher(self):
        """asset_tag: None must not bind the NULL-asset_tag module."""
        m = _matcher(Module, "unique_asset_tag")
        self.assertIsNone(m.build_queryset({"asset_tag": None}))
        self.assertIsNone(m.fingerprint({"asset_tag": None}))

    def test_unique_master_null_lookup_skips_matcher(self):
        """master: None must not bind an arbitrary masterless VC (any name)."""
        found = find_existing_object(
            {"name": "ng-vc-DIFFERENT", "master": None}, "dcim.virtualchassis"
        )
        self.assertIsNone(found)

    def test_partial_null_multi_field_still_matches(self):
        """Rack (location: None, name) keeps today's clear-FK dedupe."""
        found = find_existing_object(
            {"name": "ng-rack", "site": self.site.pk, "location": None}, "dcim.rack"
        )
        self.assertEqual(found, self.rack)

    def test_macaddress_isnull_variant_matches_without_crash(self):
        """Explicit-null assignment matches by MAC via the isnull variant."""
        mac = MACAddress.objects.create(mac_address="00:11:22:33:44:55")
        found = find_existing_object(
            {
                "mac_address": "00:11:22:33:44:55",
                "assigned_object_type": None,
                "assigned_object_id": None,
            },
            "dcim.macaddress",
        )
        self.assertEqual(found, mac)

    def test_condition_scoped_null_matchers_survive(self):
        """VM name/cluster-null style matchers still work under the guard."""
        from virtualization.models import VirtualMachine
        vm = VirtualMachine.objects.create(name="ng-vm")
        found = find_existing_object({"name": "ng-vm"}, "virtualization.virtualmachine")
        self.assertEqual(found, vm)

    def test_gfk_null_payload_does_not_raise(self):
        """Cleared generic refs must not 500 the matcher loop."""
        found = find_existing_object(
            {"name": "ng-cluster-x", "scope_type": None, "scope_id": None},
            "virtualization.cluster",
        )
        self.assertIsNone(found)  # no such cluster; the point is: no exception

    def test_none_keyed_fingerprint_fusion_removed(self):
        """Two null-asset_tag modules in different bays share NO fingerprint."""
        a = {"device": self.dev.pk, "module_bay": 1, "module_type": self.mt.pk, "asset_tag": None}
        b = {"device": self.dev.pk, "module_bay": 2, "module_type": self.mt.pk, "asset_tag": None}
        shared = set(fingerprints(a, "dcim.module")) & set(fingerprints(b, "dcim.module"))
        self.assertEqual(shared, set(), shared)
