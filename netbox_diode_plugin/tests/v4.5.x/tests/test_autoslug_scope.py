#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - the auto-slug lookup stays inside the payload's scope."""

from unittest import mock

from dcim.models import DeviceType, Location, Manufacturer, Platform, RackType, Region, Site
from django.core.cache import cache as django_cache
from django.test import TestCase
from ipam.models import VLANGroup

from netbox_diode_plugin.api.common import AutoSlug, ChangeType
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.matcher import (
    _find_obj_cache_key,
    _request_obj_cache,
    enter_request_obj_cache,
    exit_request_obj_cache,
    find_existing_object,
    get_model_matchers,
)


def _changes(entity, object_type):
    """Return the planned changes of one object type."""
    return [c for c in generate_changeset(entity, object_type).change_set.changes if c.object_type == object_type]


def _effective(changes):
    """Return the changes that are not NOOPs."""
    return [c for c in changes if c.change_type != ChangeType.NOOP]


def _autoslug(model_class):
    """Return the auto-slug matcher of a model."""
    for matcher in get_model_matchers(model_class):
        if getattr(matcher, "name", None) == "unique_autoslug_slug":
            return matcher
    raise AssertionError(f"no auto-slug matcher on {model_class.__name__}")


class AutoSlugScopeTestCase(TestCase):
    """A slugless payload never binds a same-slug row from a different scope."""

    @classmethod
    def setUpTestData(cls):
        """Same-slug objects spread over sites, manufacturers, parents and VLAN group scopes."""
        cls.site_a = Site.objects.create(name="Site A", slug="site-a")
        cls.site_b = Site.objects.create(name="Site B", slug="site-b")
        cls.hall = Location.objects.create(name="Hall", slug="hall", site=cls.site_b)
        cls.floor_1 = Location.objects.create(name="Floor 1", slug="floor-1", site=cls.site_a)
        cls.room_2 = Location.objects.create(name="Room 2", slug="room-2", site=cls.site_a, parent=cls.floor_1)
        cls.vendor_a = Manufacturer.objects.create(name="Vendor A", slug="vendor-a")
        cls.vendor_b = Manufacturer.objects.create(name="Vendor B", slug="vendor-b")
        cls.platform_a = Platform.objects.create(name="Platform A", slug="platform-a", manufacturer=cls.vendor_a)
        cls.linux = Platform.objects.create(name="Linux", slug="linux")
        cls.europe = Region.objects.create(name="Europe", slug="europe")
        cls.west = Region.objects.create(name="West", slug="west", parent=cls.europe)
        cls.asia = Region.objects.create(name="Asia", slug="asia")
        cls.model_a = DeviceType.objects.create(model="Model A", slug="model-a", manufacturer=cls.vendor_a)
        cls.group_a = VLANGroup.objects.create(name="Group A", slug="group-a", scope=cls.site_b)

    def test_location_new_in_site_does_not_bind_same_slug_in_other_site(self):
        """A location new in its site is created, not taken from another site."""
        changes = _changes({"name": "Hall", "site": {"name": "Site A"}}, "dcim.location")
        self.assertEqual([c.change_type for c in changes], [ChangeType.CREATE])

    def test_location_in_new_site_does_not_bind_other_site(self):
        """A location in a site the payload creates is created too."""
        changes = _changes({"name": "Hall", "site": {"name": "Site C"}}, "dcim.location")
        self.assertEqual([c.change_type for c in _effective(changes)], [ChangeType.CREATE])

    def test_location_without_parent_binds_nested_slug_in_its_site(self):
        """A nested location named without its parent still binds within its site."""
        changes = _effective(_changes({"name": "Room 2", "site": {"name": "Site A"}}, "dcim.location"))
        self.assertNotIn(ChangeType.CREATE, [c.change_type for c in changes])
        self.assertTrue(all(c.object_id == self.room_2.pk for c in changes))

    def test_platform_does_not_bind_same_slug_of_other_manufacturer(self):
        """A platform new for its manufacturer is created, not taken from another manufacturer."""
        changes = _changes({"name": "Platform A", "manufacturer": {"name": "Vendor B"}}, "dcim.platform")
        self.assertEqual([c.change_type for c in _effective(changes)], [ChangeType.CREATE])

    def test_platform_without_manufacturer_binds_by_slug(self):
        """A platform named without a manufacturer still binds by slug."""
        changes = _changes({"name": "Platform A"}, "dcim.platform")
        self.assertNotIn(ChangeType.CREATE, [c.change_type for c in changes])

    def test_platform_with_manufacturer_adopts_manufacturerless_slug(self):
        """A platform created without a manufacturer is still adopted."""
        changes = _effective(_changes({"name": "Linux", "manufacturer": {"name": "Vendor B"}}, "dcim.platform"))
        self.assertNotIn(ChangeType.CREATE, [c.change_type for c in changes])
        self.assertTrue(all(c.object_id == self.linux.pk for c in changes))

    def test_platform_with_new_manufacturer_adopts_manufacturerless_slug(self):
        """A platform created without a manufacturer is adopted when the payload's manufacturer is new."""
        changes = _effective(_changes({"name": "Linux", "manufacturer": {"name": "Vendor C"}}, "dcim.platform"))
        self.assertNotIn(ChangeType.CREATE, [c.change_type for c in changes])
        self.assertTrue(all(c.object_id == self.linux.pk for c in changes))

    def test_in_scope_row_wins_over_manufacturerless_row(self):
        """The same-slug row of the payload's manufacturer wins over the one without a manufacturer."""
        linux_b = Platform.objects.create(name="LINUX", slug="linux", manufacturer=self.vendor_b)
        changes = _effective(_changes({"name": "Linux", "manufacturer": {"name": "Vendor B"}}, "dcim.platform"))
        self.assertTrue(changes)
        self.assertTrue(all(c.object_id == linux_b.pk for c in changes))

    def test_child_region_does_not_bind_same_slug_under_other_parent(self):
        """A region new under its parent is created, not taken from another parent."""
        changes = _changes({"name": "West", "parent": {"name": "Asia"}}, "dcim.region")
        self.assertEqual([c.change_type for c in _effective(changes)], [ChangeType.CREATE])

    def test_device_type_does_not_bind_same_slug_of_other_manufacturer(self):
        """A device type new for its manufacturer is created, not taken from another manufacturer."""
        changes = _changes({"model": "Model A", "manufacturer": {"name": "Vendor B"}}, "dcim.devicetype")
        self.assertEqual([c.change_type for c in _effective(changes)], [ChangeType.CREATE])

    def test_vlan_group_does_not_bind_same_slug_in_other_scope(self):
        """A VLAN group new in its scope is created, not taken from another scope."""
        changes = _changes({"name": "Group A", "scope_site": {"name": "Site A"}}, "ipam.vlangroup")
        self.assertEqual([c.change_type for c in _effective(changes)], [ChangeType.CREATE])

    def test_vlan_group_binds_same_slug_in_its_own_scope(self):
        """A VLAN group whose auto slug matches a group in the payload's own scope binds that group."""
        group_c = VLANGroup.objects.create(name="GROUP C", slug="group-c", scope=self.site_b)
        changes = _effective(_changes({"name": "Group C", "scope_site": {"name": "Site B"}}, "ipam.vlangroup"))
        self.assertTrue(changes)
        self.assertTrue(all(c.object_id == group_c.pk for c in changes))

    def test_in_scope_region_wins_over_top_level_region(self):
        """The same-slug region under the payload's parent wins over the top-level one."""
        Region.objects.create(name="North", slug="north")
        north_eu = Region.objects.create(name="NORTH", slug="north", parent=self.europe)
        changes = _effective(_changes({"name": "North", "parent": {"name": "Europe"}}, "dcim.region"))
        self.assertTrue(changes)
        self.assertTrue(all(c.object_id == north_eu.pk for c in changes))

    def test_rack_type_with_unique_slug_stays_unscoped(self):
        """A globally unique slug is not scoped; a scoped slug takes its constraint's other fields."""
        self.assertEqual(_autoslug(RackType).scope_fields, ())
        self.assertEqual(_autoslug(Location).scope_fields, ("parent", "site"))


@mock.patch("netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl", return_value=30)
class AutoSlugScopeCacheTestCase(TestCase):
    """An adopted empty-scope row is not cached past a same-slug row appearing in the payload's scope."""

    def setUp(self):
        """Start from an empty lookup cache."""
        django_cache.clear()
        self.vendor = Manufacturer.objects.create(name="Vendor A", slug="vendor-a")
        self.linux = Platform.objects.create(name="Linux", slug="linux")

    def tearDown(self):
        """Leave an empty lookup cache."""
        django_cache.clear()

    def test_in_scope_row_wins_after_empty_scope_row_was_adopted(self, _ttl):
        """A same-slug platform created under the payload's manufacturer wins on the next lookup."""
        payload = {"name": "Linux", "manufacturer": {"name": "Vendor A"}}
        self.assertTrue(all(c.object_id == self.linux.pk for c in _effective(_changes(payload, "dcim.platform"))))
        linux_a = Platform.objects.create(name="Linux (A)", slug="linux", manufacturer=self.vendor)
        changes = _effective(_changes(payload, "dcim.platform"))
        self.assertTrue(changes)
        self.assertTrue(all(c.object_id == linux_a.pk for c in changes))

    def test_in_scope_answer_is_cached(self, _ttl):
        """A row bound inside the payload's scope is cached as before."""
        linux_a = Platform.objects.create(name="Linux (A)", slug="linux", manufacturer=self.vendor)
        data = {"name": "Linux", "manufacturer": self.vendor.pk, "_auto_slug": AutoSlug("slug", "linux")}
        self.assertEqual(find_existing_object(data, "dcim.platform"), linux_a)
        self.assertEqual(django_cache.get(_find_obj_cache_key(data, "dcim.platform")), linux_a.pk)

    def test_in_scope_row_wins_within_one_request(self, _ttl):
        """Inside one request too, a same-slug row created in the payload's scope wins the next lookup."""
        data = {"name": "Linux", "manufacturer": self.vendor.pk, "_auto_slug": AutoSlug("slug", "linux")}
        token = enter_request_obj_cache()
        try:
            self.assertEqual(find_existing_object(data, "dcim.platform"), self.linux)
            linux_a = Platform.objects.create(name="Linux (A)", slug="linux", manufacturer=self.vendor)
            self.assertEqual(find_existing_object(data, "dcim.platform"), linux_a)
        finally:
            exit_request_obj_cache(token)

    def test_in_scope_answer_is_request_cached(self, _ttl):
        """A row bound inside the payload's scope stays in the request cache."""
        linux_a = Platform.objects.create(name="Linux (A)", slug="linux", manufacturer=self.vendor)
        data = {"name": "Linux", "manufacturer": self.vendor.pk, "_auto_slug": AutoSlug("slug", "linux")}
        token = enter_request_obj_cache()
        try:
            find_existing_object(data, "dcim.platform")
            self.assertEqual(_request_obj_cache.get().get(_find_obj_cache_key(data, "dcim.platform")), linux_a)
        finally:
            exit_request_obj_cache(token)
