#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - slugless sibling objects in one ingest stay apart."""

from dcim.models import (
    Cable,
    CableTermination,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Location,
    Manufacturer,
    Site,
)
from django.test import TestCase

from netbox_diode_plugin.api import transformer
from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import ChangeType
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.supported_models import extract_supported_models


def _iface(device_name, location=None, platform=None, site="Site A", region=None):
    """Return an interface termination whose device carries the given location, platform and site."""
    site_payload = {"name": site}
    if region is not None:
        site_payload["region"] = region
    device = {
        "name": device_name,
        "device_type": {"manufacturer": {"name": "Vendor A"}, "model": "Model A"},
        "role": {"name": "Role A"},
        "site": site_payload,
    }
    if location is not None:
        device["location"] = location
    if platform is not None:
        device["platform"] = platform
    return {"object_interface": {"name": "eth0", "type": "1000base-t", "device": device}}


def _cable(a, b):
    """Return a cable payload between two terminations."""
    return {"a_terminations": [a], "b_terminations": [b]}


def _nodes(out, object_type):
    """Return the sorted (name, slug) pairs of the transformed nodes of one object type."""
    return sorted((n.get("name"), n.get("slug")) for n in out if n["_object_type"] == object_type)


def _transform(payload):
    """Transform a cable payload."""
    return transformer.transform_proto_json(payload, "dcim.cable", extract_supported_models())


class SluglessSiblingTransformTestCase(TestCase):
    """Different slugless objects sharing a scope in one payload are not merged."""

    def test_cable_between_two_slugless_locations_keeps_both(self):
        """Two slugless locations in one site stay two nodes."""
        out = _transform(
            _cable(
                _iface("Device Hall", location={"name": "Hall", "site": {"name": "Site A"}}),
                _iface("Device Lab", location={"name": "Lab", "site": {"name": "Site A"}}),
            )
        )
        self.assertEqual([n for n, _ in _nodes(out, "dcim.location")], ["Hall", "Lab"])

    def test_cable_between_two_slugless_platforms_keeps_both(self):
        """Two slugless platforms of one manufacturer stay two nodes."""
        out = _transform(
            _cable(
                _iface("Device A", platform={"name": "Platform A", "manufacturer": {"name": "Vendor A"}}),
                _iface("Device B", platform={"name": "Platform B", "manufacturer": {"name": "Vendor A"}}),
            )
        )
        self.assertEqual([n for n, _ in _nodes(out, "dcim.platform")], ["Platform A", "Platform B"])

    def test_two_slugless_child_regions_under_one_parent_keep_both(self):
        """Two slugless child regions under one parent stay two nodes."""
        out = _transform(
            _cable(
                _iface("Device West", site="Site West", region={"name": "West", "parent": {"name": "Europe"}}),
                _iface("Device East", site="Site East", region={"name": "East", "parent": {"name": "Europe"}}),
            )
        )
        self.assertEqual([n for n, _ in _nodes(out, "dcim.region")], ["East", "Europe", "West"])

    def test_one_location_twice_with_one_slug_merges(self):
        """One location named twice, with the slug on one copy only, is still one node."""
        out = _transform(
            _cable(
                _iface("Device A", location={"name": "Hall", "slug": "hall", "site": {"name": "Site A"}}),
                _iface("Device B", location={"name": "Hall", "site": {"name": "Site A"}}),
            )
        )
        self.assertEqual(_nodes(out, "dcim.location"), [("Hall", "hall")])


class SluglessSiblingApplyTestCase(TestCase):
    """A cable between devices in two existing locations of one site plans and applies."""

    @classmethod
    def setUpTestData(cls):
        """Site with two locations, one with an operator-chosen slug, and a device in each."""
        site = Site.objects.create(name="Site A", slug="site-a")
        hall = Location.objects.create(name="Hall", slug="hall-custom", site=site)
        lab = Location.objects.create(name="Lab", slug="lab", site=site)
        mfr = Manufacturer.objects.create(name="Vendor A", slug="vendor-a")
        dt = DeviceType.objects.create(manufacturer=mfr, model="Model A", slug="model-a")
        role = DeviceRole.objects.create(name="Role A", slug="role-a")
        dev_hall = Device.objects.create(name="Device Hall", device_type=dt, role=role, site=site, location=hall)
        dev_lab = Device.objects.create(name="Device Lab", device_type=dt, role=role, site=site, location=lab)
        cls.iface_hall = Interface.objects.create(device=dev_hall, name="eth0", type="1000base-t")
        cls.iface_lab = Interface.objects.create(device=dev_lab, name="eth0", type="1000base-t")

    def test_cable_between_devices_in_two_locations_applies(self):
        """The cable is created and neither location is changed."""
        entity = _cable(
            _iface("Device Hall", location={"name": "Hall", "site": {"name": "Site A"}}),
            _iface("Device Lab", location={"name": "Lab", "site": {"name": "Site A"}}),
        )
        result = generate_changeset(entity, "dcim.cable")
        location_changes = [
            c for c in result.change_set.changes if c.object_type == "dcim.location" and c.change_type != ChangeType.NOOP
        ]
        self.assertEqual(location_changes, [])

        apply_changeset(result.change_set, request=None)

        self.assertEqual(Cable.objects.count(), 1)
        terms = {(t.cable_end, t.termination_id) for t in CableTermination.objects.all()}
        self.assertEqual(terms, {("A", self.iface_hall.pk), ("B", self.iface_lab.pk)})
        self.assertEqual(sorted(Location.objects.values_list("name", "slug")), [("Hall", "hall-custom"), ("Lab", "lab")])


class SluglessSiblingCrossSiteTestCase(TestCase):
    """A slugless location new in its site is created, not taken from another site."""

    @classmethod
    def setUpTestData(cls):
        """Annex exists only in Site B; both devices are in Site A."""
        cls.site_a = Site.objects.create(name="Site A", slug="site-a")
        cls.site_b = Site.objects.create(name="Site B", slug="site-b")
        lab = Location.objects.create(name="Lab", slug="lab", site=cls.site_a)
        cls.annex_b = Location.objects.create(name="Annex", slug="annex", site=cls.site_b)
        mfr = Manufacturer.objects.create(name="Vendor A", slug="vendor-a")
        dt = DeviceType.objects.create(manufacturer=mfr, model="Model A", slug="model-a")
        role = DeviceRole.objects.create(name="Role A", slug="role-a")
        dev_annex = Device.objects.create(name="Device Annex", device_type=dt, role=role, site=cls.site_a)
        dev_lab = Device.objects.create(name="Device Lab", device_type=dt, role=role, site=cls.site_a, location=lab)
        Interface.objects.create(device=dev_annex, name="eth0", type="1000base-t")
        Interface.objects.create(device=dev_lab, name="eth0", type="1000base-t")

    def test_cable_to_new_location_does_not_move_other_site_location(self):
        """Site B's Annex stays in Site B and a new Annex is created in Site A."""
        entity = _cable(
            _iface("Device Annex", location={"name": "Annex", "site": {"name": "Site A"}}),
            _iface("Device Lab", location={"name": "Lab", "site": {"name": "Site A"}}),
        )
        result = generate_changeset(entity, "dcim.cable")
        apply_changeset(result.change_set, request=None)

        self.assertEqual(Location.objects.get(pk=self.annex_b.pk).site, self.site_b)
        self.assertEqual(Location.objects.filter(name="Annex", site=self.site_a).count(), 1)
        self.assertEqual(Cable.objects.count(), 1)
