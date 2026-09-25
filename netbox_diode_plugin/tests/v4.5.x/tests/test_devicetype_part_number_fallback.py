#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Tests for binding a device type by its part number when its model matches nothing."""

from dcim.models import DeviceType, Manufacturer, ModuleType
from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    PartNumberFallbackMatcher,
    find_existing_object,
    get_model_matchers,
    part_number_key,
)

PART = "SW-9200-48P"
CATALOG_MODEL = "Series 9200 48-port"


class PartNumberKeyTestCase(SimpleTestCase):
    """What a payload names its part by."""

    def test_asserted_part_number_wins_over_model(self):
        """An asserted part number is the key; the model is not consulted."""
        self.assertEqual(part_number_key({"model": "anything", "part_number": " PN-1 "}), "PN-1")

    def test_blank_part_number_falls_back_to_model(self):
        """An explicitly empty part number reads as absent."""
        self.assertEqual(part_number_key({"model": " SW-1 ", "part_number": ""}), "SW-1")

    def test_placeholders_and_blanks_are_never_keys(self):
        """No key from blank, whitespace or an unknown placeholder."""
        for data in (
            {"model": ""},
            {"model": "   "},
            {"model": "Unknown"},
            {"model": "UNKNOWN"},
            {"model": "SW-1", "part_number": "unknown"},
            {},
        ):
            self.assertIsNone(part_number_key(data), data)


class PartNumberFallbackMatcherTestCase(TestCase):
    """The fallback binds a type by part number, and only after everything else missed."""

    @classmethod
    def setUpTestData(cls):
        """A vendor with a catalog-style type: marketing model, part ID in part_number."""
        cls.mfr = Manufacturer.objects.create(name="pnf-vendor", slug="pnf-vendor")
        cls.other_mfr = Manufacturer.objects.create(name="pnf-other", slug="pnf-other")
        cls.catalog = DeviceType.objects.create(
            manufacturer=cls.mfr, model=CATALOG_MODEL, slug="pnf-vendor-sw-9200-48p", part_number=PART,
        )

    def setUp(self):
        """Each test answers from the database, not from a lookup an earlier test cached."""
        django_cache.clear()

    def tearDown(self):
        """Leave no cached lookups behind for other test modules."""
        django_cache.clear()

    def _find(self, **data):
        return find_existing_object({"manufacturer": self.mfr.pk, **data}, "dcim.devicetype")

    def test_model_equal_to_a_part_number_binds_that_type(self):
        """The payload's model is the catalog type's part number."""
        self.assertEqual(self._find(model=PART), self.catalog)

    def test_asserted_part_number_binds_when_model_misses(self):
        """An asserted part number is matched even when the model names something else."""
        self.assertEqual(self._find(model="SW 9200 family", part_number=PART), self.catalog)

    def test_model_matcher_still_wins(self):
        """A type whose model is the part ID is found first; the fallback never runs."""
        duplicate = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup")
        self.assertEqual(self._find(model=PART), duplicate)

    def test_other_manufacturer_is_never_bound(self):
        """Part numbers are scoped to the manufacturer."""
        found = find_existing_object({"manufacturer": self.other_mfr.pk, "model": PART}, "dcim.devicetype")
        self.assertIsNone(found)

    def test_unresolved_manufacturer_abstains(self):
        """A manufacturer created in the same batch cannot own an existing type."""
        matcher = PartNumberFallbackMatcher(model_class=DeviceType, name="t")
        ref = UnresolvedReference(object_type="dcim.manufacturer", uuid="u-1")
        self.assertIsNone(matcher.build_queryset({"manufacturer": ref, "model": PART}))
        self.assertIsNone(matcher.build_queryset({"manufacturer": True, "model": PART}))

    def test_placeholder_model_is_never_bound(self):
        """A type whose part number is a placeholder never absorbs unidentified devices."""
        DeviceType.objects.create(manufacturer=self.mfr, model="pnf-junk", slug="pnf-junk", part_number="Unknown")
        self.assertIsNone(self._find(model="Unknown"))

    def test_shared_part_number_binds_nothing_and_names_both(self):
        """Two types share the part number: bind neither, and say which."""
        second = DeviceType.objects.create(
            manufacturer=self.mfr, model="Series 9200 48-port rev B", slug="pnf-rev-b", part_number=PART,
        )
        with self.assertLogs("netbox_diode_plugin.api.matcher", level="WARNING") as logs:
            self.assertIsNone(self._find(model=PART))
        message = "\n".join(logs.output)
        self.assertIn(f"pk={self.catalog.pk}", message)
        self.assertIn(f"pk={second.pk}", message)

    def test_fallback_is_the_last_device_type_matcher(self):
        """Consulted only after every constraint, custom-field and slug matcher."""
        names = [m.name for m in get_model_matchers(DeviceType)]
        self.assertEqual(names[-1], "fallback_devicetype_part_number")
        self.assertEqual(names.count("fallback_devicetype_part_number"), 1)

    def test_no_fallback_for_other_types(self):
        """Module types keep their matchers unchanged."""
        names = [m.name for m in get_model_matchers(ModuleType)]
        self.assertEqual([n for n in names if n.startswith("fallback_")], [])

    def test_fallback_never_fingerprints(self):
        """In-batch dedupe is not widened by part number."""
        matcher = PartNumberFallbackMatcher(model_class=DeviceType, name="t")
        self.assertIsNone(matcher.fingerprint({"manufacturer": 1, "model": PART}))
