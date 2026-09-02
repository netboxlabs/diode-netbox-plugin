#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Unit tests for RackReservationUnitOverlapMatcher and dcim.rackreservation routing."""

from dcim.models import Location, Rack, RackReservation, Site
from django.test import TestCase
from users.models import User

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    AmbiguousObjectMatch,
    RackReservationUnitOverlapMatcher,
    _find_obj_cache_key,
    find_existing_object,
    requires_pre_save_match,
)
from netbox_diode_plugin.api.plugin_utils import get_object_type_model


def _matcher():
    return RackReservationUnitOverlapMatcher(
        model_class=get_object_type_model("dcim.rackreservation"),
        name="logical_rackreservation_unit_overlap",
    )


class RackReservationMatcherFieldsTestCase(TestCase):
    """has_required_fields / build_queryset abstain rules (no DB rows needed)."""

    def setUp(self):
        """Set up the matcher under test."""
        self.matcher = _matcher()

    def test_has_required_fields_present(self):
        """Rack ref plus non-empty units satisfies the gate."""
        self.assertTrue(self.matcher.has_required_fields({"rack": 1, "units": [1]}))

    def test_has_required_fields_missing_units(self):
        """No units -> gate refuses."""
        self.assertFalse(self.matcher.has_required_fields({"rack": 1}))

    def test_has_required_fields_empty_units(self):
        """Empty units list -> gate refuses."""
        self.assertFalse(self.matcher.has_required_fields({"rack": 1, "units": []}))

    def test_has_required_fields_missing_rack(self):
        """No rack -> gate refuses."""
        self.assertFalse(self.matcher.has_required_fields({"units": [1]}))

    def test_build_queryset_abstains_on_unresolved_rack(self):
        """An in-batch rack ref has no pk to query -> abstain."""
        ref = UnresolvedReference(object_type="dcim.rack", uuid="u-1")
        self.assertIsNone(self.matcher.build_queryset({"rack": ref, "units": [1]}))

    def test_build_queryset_abstains_on_bool_rack(self):
        """rack=True must NOT be treated as pk 1."""
        self.assertIsNone(self.matcher.build_queryset({"rack": True, "units": [1]}))

    def test_build_queryset_abstains_on_non_int_unit(self):
        """A non-int (or bool) unit element -> abstain, not a crash."""
        self.assertIsNone(self.matcher.build_queryset({"rack": 1, "units": [1, "2"]}))
        self.assertIsNone(self.matcher.build_queryset({"rack": 1, "units": [1, True]}))


class RackReservationMatcherFingerprintTestCase(TestCase):
    """In-batch dedupe fingerprint: exact unit set, order/duplicate-insensitive."""

    def setUp(self):
        """Set up the matcher under test."""
        self.matcher = _matcher()

    def test_fingerprint_equal_under_reorder_and_duplicates(self):
        """Same rack + same unit set (any order, with dups) -> same key."""
        a = self.matcher.fingerprint({"rack": 7, "units": [1, 2, 3]})
        b = self.matcher.fingerprint({"rack": 7, "units": [3, 1, 2, 2]})
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_fingerprint_differs_on_unit_set(self):
        """Overlapping-but-different unit sets do NOT dedupe-merge in batch."""
        a = self.matcher.fingerprint({"rack": 7, "units": [1, 2]})
        b = self.matcher.fingerprint({"rack": 7, "units": [2, 3]})
        self.assertNotEqual(a, b)

    def test_fingerprint_differs_on_rack(self):
        """Same units on different racks are different reservations."""
        a = self.matcher.fingerprint({"rack": 7, "units": [1]})
        b = self.matcher.fingerprint({"rack": 8, "units": [1]})
        self.assertNotEqual(a, b)

    def test_fingerprint_unresolved_rack_keys_on_uuid(self):
        """An unresolved rack ref still fingerprints (in-batch grouping)."""
        ref = UnresolvedReference(object_type="dcim.rack", uuid="u-1")
        a = self.matcher.fingerprint({"rack": ref, "units": [1]})
        b = self.matcher.fingerprint({"rack": ref, "units": [1]})
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_fingerprint_none_when_units_missing(self):
        """No units -> no fingerprint."""
        self.assertIsNone(self.matcher.fingerprint({"rack": 7}))


class RackReservationCacheKeyTestCase(TestCase):
    """
    The scalar-only find-object cache must be disabled for this type.

    Identity lives in the units ArrayField, which the scalar-only cache key
    skips: without the carve-out, two different reservations on one rack
    share a key and serve each other's cached answers -- including inside
    this very test file (the lookup tests below differ only in units).
    """

    def test_find_obj_cache_key_disabled(self):
        """No cache key for dcim.rackreservation, ever."""
        data = {"rack": 1, "units": [1, 2], "description": "x", "status": "active"}
        self.assertIsNone(_find_obj_cache_key(data, "dcim.rackreservation"))


class RackReservationMatcherLookupTestCase(TestCase):
    """find_existing_object behavior against real rows (registration included)."""

    @classmethod
    def setUpTestData(cls):
        """One rack with one existing reservation on units [1, 2]."""
        cls.site = Site.objects.create(name="rrm-site", slug="rrm-site")
        cls.location = Location.objects.create(
            name="rrm-loc", slug="rrm-loc", site=cls.site
        )
        cls.rack = Rack.objects.create(
            name="rrm-rack", site=cls.site, location=cls.location
        )
        cls.user = User.objects.create(username="rrm-user")
        cls.existing = RackReservation.objects.create(
            rack=cls.rack, units=[1, 2], user=cls.user, description="seed"
        )

    def _data(self, units):
        return {"rack": self.rack.pk, "units": units, "description": "x"}

    def test_identical_units_match(self):
        """Same rack + same units -> the existing reservation."""
        found = find_existing_object(self._data([1, 2]), "dcim.rackreservation")
        self.assertEqual(found, self.existing)

    def test_overlap_matches(self):
        """Sharing one unit is identity: [2, 3] matches the [1, 2] row."""
        found = find_existing_object(self._data([2, 3]), "dcim.rackreservation")
        self.assertEqual(found, self.existing)

    def test_order_and_duplicates_insensitive(self):
        """[2, 2, 1] matches the [1, 2] row."""
        found = find_existing_object(self._data([2, 2, 1]), "dcim.rackreservation")
        self.assertEqual(found, self.existing)

    def test_disjoint_units_no_match(self):
        """[5, 6] shares nothing with [1, 2] -> create."""
        self.assertIsNone(
            find_existing_object(self._data([5, 6]), "dcim.rackreservation")
        )

    def test_other_rack_no_match(self):
        """Same units on another rack -> create."""
        rack2 = Rack.objects.create(
            name="rrm-rack2", site=self.site, location=self.location
        )
        data = {"rack": rack2.pk, "units": [1, 2], "description": "x"}
        self.assertIsNone(find_existing_object(data, "dcim.rackreservation"))

    def test_multi_overlap_raises_ambiguous(self):
        """A payload spanning two reservations refuses to pick one."""
        other = RackReservation.objects.create(
            rack=self.rack, units=[3], user=self.user, description="seed2"
        )
        with self.assertRaises(AmbiguousObjectMatch) as cm:
            find_existing_object(self._data([2, 3]), "dcim.rackreservation")
        msg = str(cm.exception)
        self.assertIn(str(self.existing.pk), msg)
        self.assertIn(str(other.pk), msg)


class RackReservationRoutingTestCase(TestCase):
    """Pre-save routing for dcim.rackreservation."""

    def test_requires_pre_save_match(self):
        """No DB constraint means no IntegrityError backstop: pre-save match."""
        self.assertTrue(requires_pre_save_match("dcim.rackreservation"))
