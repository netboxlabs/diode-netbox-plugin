#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - fingerprints of constraints with fields absent from the payload."""

from dcim.models import Device, Location, Platform
from django.test import TestCase

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import fingerprints, get_model_matchers

SITE = UnresolvedReference(object_type="dcim.site", uuid="site-a")
PARENT = UnresolvedReference(object_type="dcim.location", uuid="hall")
VENDOR = UnresolvedReference(object_type="dcim.manufacturer", uuid="vendor-a")


def _matcher(model_class, name):
    """Return the constraint matcher registered under name."""
    for matcher in get_model_matchers(model_class):
        if getattr(matcher, "name", None) == name:
            return matcher
    raise AssertionError(f"matcher {name} not found")


class AbsentFieldFingerprintTestCase(TestCase):
    """A missing NOT NULL scalar is unknown, not NULL, when fingerprinting a nulls_distinct=False constraint."""

    def test_slug_constraint_skips_missing_none_or_blank_slug(self):
        """The slug constraint gives no fingerprint when the slug is missing, None or blank."""
        m = _matcher(Location, "dcim_location_parent_slug")
        self.assertIsNone(m.fingerprint({"name": "Hall", "site": SITE}))
        self.assertIsNone(m.fingerprint({"name": "Hall", "site": SITE, "slug": None}))
        self.assertIsNone(m.fingerprint({"name": "Hall", "site": SITE, "slug": ""}))

    def test_slug_constraint_fingerprints_present_slug(self):
        """The slug constraint still fingerprints a payload that carries a slug."""
        m = _matcher(Location, "dcim_location_parent_slug")
        self.assertIsNotNone(m.fingerprint({"name": "Hall", "site": SITE, "slug": "hall"}))

    def test_slugless_locations_in_one_site_share_no_fingerprint(self):
        """Two different slugless locations in one site share no fingerprint."""
        a = fingerprints({"name": "Hall", "site": SITE}, "dcim.location")
        b = fingerprints({"name": "Lab", "site": SITE}, "dcim.location")
        self.assertEqual(set(a) & set(b), set())

    def test_nested_slugless_locations_share_no_fingerprint(self):
        """Two different slugless locations under one parent share no fingerprint."""
        a = fingerprints({"name": "Hall", "site": SITE, "parent": PARENT}, "dcim.location")
        b = fingerprints({"name": "Lab", "site": SITE, "parent": PARENT}, "dcim.location")
        self.assertEqual(set(a) & set(b), set())

    def test_slugless_copies_of_one_location_share_the_name_fingerprint(self):
        """Two slugless copies of one location still meet on the name constraint."""
        m = _matcher(Location, "dcim_location_parent_name")
        fp = m.fingerprint({"name": "Hall", "site": SITE})
        self.assertIsNotNone(fp)
        self.assertEqual(fp, m.fingerprint({"name": "Hall", "site": SITE}))

    def test_nameless_locations_with_different_slugs_share_no_fingerprint(self):
        """Two nameless locations with different slugs share no fingerprint."""
        a = fingerprints({"slug": "hall", "site": SITE}, "dcim.location")
        b = fingerprints({"slug": "lab", "site": SITE}, "dcim.location")
        self.assertEqual(set(a) & set(b), set())

    def test_slugless_platforms_of_one_manufacturer_share_no_fingerprint(self):
        """Two different slugless platforms of one manufacturer share no fingerprint."""
        a = fingerprints({"name": "Platform A", "manufacturer": VENDOR}, "dcim.platform")
        b = fingerprints({"name": "Platform B", "manufacturer": VENDOR}, "dcim.platform")
        self.assertEqual(set(a) & set(b), set())
        self.assertIsNone(
            _matcher(Platform, "dcim_platform_manufacturer_slug").fingerprint({"name": "Platform A", "manufacturer": VENDOR})
        )

    def test_missing_nullable_relation_still_fingerprints(self):
        """A missing nullable relation still counts as NULL."""
        self.assertIsNotNone(
            _matcher(Device, "dcim_device_unique_name_site_tenant").fingerprint({"name": "edge-1", "site": SITE})
        )
        self.assertIsNotNone(_matcher(Location, "dcim_location_parent_name").fingerprint({"name": "Hall", "site": SITE}))
