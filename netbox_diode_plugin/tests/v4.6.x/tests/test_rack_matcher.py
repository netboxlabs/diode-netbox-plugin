#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Unit tests for RackSiteNameMatcher, rack differ preserve, and rack routing."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Location,
    Manufacturer,
    PowerFeed,
    PowerPanel,
    Rack,
    RackReservation,
    Site,
)
from django.test import TestCase
from users.models import User

from netbox_diode_plugin.api.common import ChangeType, UnresolvedReference
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.matcher import (
    AmbiguousObjectMatch,
    RackSiteNameMatcher,
    _find_obj_cache_key,
    find_existing_object,
    pre_save_match_binds_only,
    requires_pre_save_match,
)
from netbox_diode_plugin.api.plugin_utils import get_object_type_model


def _matcher():
    return RackSiteNameMatcher(
        model_class=get_object_type_model("dcim.rack"),
        name="logical_rack_site_name_no_location",
    )


class RackSiteNameMatcherGateTestCase(TestCase):
    """Gate, abstain and routing rules (no DB rows needed)."""

    def setUp(self):
        """Set up the matcher under test."""
        self.matcher = _matcher()

    def test_gate_shape(self):
        """Fires on the location-less {name, site} shape; declines a location key."""
        self.assertTrue(self.matcher.has_required_fields({"name": "r1", "site": 1}))
        self.assertFalse(self.matcher.has_required_fields(
            {"name": "r1", "site": 1, "location": 5}))
        self.assertFalse(self.matcher.has_required_fields(
            {"name": "r1", "site": 1, "location": None}))

    def test_build_queryset_abstains_on_malformed_site(self):
        """An in-batch site ref or a bool site must not become a pk filter."""
        ref = UnresolvedReference(object_type="dcim.site", uuid="u-1")
        self.assertIsNone(self.matcher.build_queryset({"name": "r1", "site": ref}))
        self.assertIsNone(self.matcher.build_queryset({"name": "r1", "site": True}))

    def test_location_less_create_takes_pre_save_match(self):
        """No location key: NULLS DISTINCT territory, no DB backstop."""
        self.assertTrue(requires_pre_save_match("dcim.rack", {"name": "r", "site": 1}))

    def test_explicit_null_create_skips_pre_save_match(self):
        """Explicit null is not admitted: its only matcher is site-blind."""
        self.assertFalse(requires_pre_save_match(
            "dcim.rack", {"name": "r", "site": 1, "location": None}))
    def test_located_create_skips_pre_save_match(self):
        """A real location value keeps its DB-constraint backstop."""
        self.assertFalse(requires_pre_save_match(
            "dcim.rack", {"name": "r", "site": 1, "location": 5}))

    def test_rack_pre_save_match_is_bind_only(self):
        """(site, name) identity is non-authoritative: bind, never write."""
        self.assertTrue(pre_save_match_binds_only("dcim.rack"))

    def test_find_obj_cache_key_disabled(self):
        """No cache key for dcim.rack, ever -- a cache hit would skip resolve()."""
        data = {"name": "r1", "site": 1, "status": "active"}
        self.assertIsNone(_find_obj_cache_key(data, "dcim.rack"))


class RackSiteNameMatcherResolveTestCase(TestCase):
    """find_existing_object behavior against real rows (registration included)."""

    @classmethod
    def setUpTestData(cls):
        """One site with a location; helpers build racks/devices/reservations."""
        cls.site = Site.objects.create(name="rsm-site", slug="rsm-site")
        cls.site2 = Site.objects.create(name="rsm-site2", slug="rsm-site2")
        cls.location = Location.objects.create(
            name="rsm-loc", slug="rsm-loc", site=cls.site
        )
        cls.user = User.objects.create(username="rsm-user")
        mfr = Manufacturer.objects.create(name="rsm-mfr", slug="rsm-mfr")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="rsm-dt", slug="rsm-dt")
        cls.role = DeviceRole.objects.create(name="rsm-role", slug="rsm-role")

    def _rack(self, name="r1", location=None, site=None):
        return Rack.objects.create(name=name, site=site or self.site, location=location)

    def test_asset_tag_rule(self):
        """A tag already on another rack wins; a new tag binds only a tagless (site, name) rack."""
        rack = self._rack()
        tagged = Rack.objects.create(name="other", site=self.site2, asset_tag="rsm-AT1")
        self.assertIsNone(
            _matcher().fingerprint({"name": "r1", "site": self.site.pk, "asset_tag": "rsm-NEW"})
        )
        self.assertEqual(
            find_existing_object({"name": "r1", "site": self.site.pk, "asset_tag": "rsm-AT1"}, "dcim.rack"),
            tagged,
        )
        self.assertEqual(
            find_existing_object({"name": "r1", "site": self.site.pk, "asset_tag": "rsm-NEW"}, "dcim.rack"),
            rack,
        )
        # a same-named rack carrying a DIFFERENT tag is another physical rack
        rack.asset_tag = "rsm-AT-A"
        rack.save()
        self.assertIsNone(
            find_existing_object({"name": "r1", "site": self.site.pk, "asset_tag": "rsm-AT-B"}, "dcim.rack")
        )

    def test_facility_id_rule(self):
        """A candidate carrying the submitted facility id wins; a different one is excluded."""
        older_null = self._rack()
        located = self._rack(location=self.location)
        located.facility_id = "rsm-F1"
        located.save()
        self.assertEqual(
            find_existing_object({"name": "r1", "site": self.site.pk, "facility_id": "rsm-F1"}, "dcim.rack"),
            located,
        )
        self.assertEqual(
            find_existing_object({"name": "r1", "site": self.site.pk, "facility_id": "rsm-F2"}, "dcim.rack"),
            older_null,
        )
        self.assertNotEqual(
            _matcher().fingerprint({"name": "r1", "site": self.site.pk, "facility_id": "rsm-F1"}),
            _matcher().fingerprint({"name": "r1", "site": self.site.pk, "facility_id": "rsm-F2"}),
        )

    def test_power_feeds_count_as_populated(self):
        """A rack holding only power feeds is populated; it beats an empty null duplicate."""
        self._rack()  # empty location-null duplicate
        located = self._rack(location=self.location)
        panel = PowerPanel.objects.create(site=self.site, name="rsm-panel")
        PowerFeed.objects.create(power_panel=panel, rack=located, name="rsm-feed")
        self.assertEqual(self._find(), located)

    def _populate_device(self, rack):
        return Device.objects.create(
            name=f"rsm-dev-{rack.pk}", site=rack.site, rack=rack,
            device_type=self.dt, role=self.role,
        )

    def _populate_reservation(self, rack):
        return RackReservation.objects.create(
            rack=rack, units=[1], user=self.user, description="rsm"
        )

    def _find(self, name="r1"):
        return find_existing_object({"name": name, "site": self.site.pk}, "dcim.rack")

    def test_single_located_rack_binds(self):
        """One candidate wins outright."""
        rack = self._rack(location=self.location)
        self.assertEqual(self._find(), rack)

    def test_sole_populated_wins_over_empty_null(self):
        """A populated located rack beats an empty null duplicate."""
        located = self._rack(location=self.location)
        self._populate_device(located)
        self._rack()  # empty null duplicate
        self.assertEqual(self._find(), located)

    def test_no_populated_prefers_oldest_null(self):
        """All empty: the oldest location-null row is the shape this payload made."""
        self._rack(location=self.location)  # empty located, created first
        old_null = self._rack()
        self._rack()  # newer null
        self.assertEqual(self._find(), old_null)

    def test_two_populated_raises_naming_both_pks(self):
        """Two populated candidates: binding would move references on a name."""
        loc2 = Location.objects.create(name="rsm-loc3", slug="rsm-loc3", site=self.site)
        a = self._rack(location=self.location)
        self._populate_device(a)
        b = self._rack(location=loc2)
        self._populate_reservation(b)
        with self.assertRaises(AmbiguousObjectMatch) as cm:
            self._find()
        message = str(cm.exception)
        self.assertIn(str(a.pk), message)
        self.assertIn(str(b.pk), message)

    def test_other_site_same_name_no_match(self):
        """Site scope is hard."""
        self._rack(site=self.site2)
        self.assertIsNone(self._find())


class RackCreatePreservesExplicitNullTestCase(TestCase):
    """
    CREATE change data keeps an explicitly-submitted location: null.

    CREATE data drops None values, which erases the difference between
    "not submitted" and "explicitly null" -- and the rack pre-save gate
    depends on that difference at apply time.
    """

    @classmethod
    def setUpTestData(cls):
        """A site for the rack entities to resolve against."""
        cls.site = Site.objects.create(name="rpn-site", slug="rpn-site")

    def _create_change(self, entity):
        """Generate a changeset and extract the rack CREATE change."""
        cs = generate_changeset(entity, "dcim.rack").change_set
        creates = [c for c in cs.changes
                   if c.object_type == "dcim.rack"
                   and c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        return creates[0]

    def test_explicit_null_survives_into_create_data(self):
        """location: null is a producer assertion; it must reach apply."""
        change = self._create_change(
            {"name": "rpn-r1", "site": {"name": "rpn-site"}, "location": None}
        )
        self.assertIn("location", change.data)
        self.assertIsNone(change.data["location"])

    def test_absent_key_stays_absent(self):
        """A location-less entity's CREATE data has no location key."""
        change = self._create_change(
            {"name": "rpn-r2", "site": {"name": "rpn-site"}}
        )
        self.assertNotIn("location", change.data)
