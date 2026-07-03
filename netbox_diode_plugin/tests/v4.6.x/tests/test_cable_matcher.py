#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Unit tests for CableTerminationSetMatcher and dcim.cable pre-save routing."""

from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.test import TestCase

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    CableTerminationSetMatcher,
    _find_obj_cache_key,
    find_existing_object,
    get_model_matchers,
    requires_pre_save_match,
)
from netbox_diode_plugin.api.plugin_utils import get_object_type_model


class RequiresPreSaveMatchTestCase(TestCase):
    """Tests for requires_pre_save_match on dcim.cable."""

    def test_dcim_cable_requires_pre_save_match(self):
        """Dcim cable requires pre save match."""
        # Cable has no DB unique constraint; uniqueness lives on CableTermination,
        # so the applier must look up an existing cable before CREATE (spec 5.3, contract H).
        self.assertTrue(requires_pre_save_match("dcim.cable"))


class CableTerminationSetMatcherFingerprintTestCase(TestCase):
    """Tests for CableTerminationSetMatcher.fingerprint."""

    def setUp(self):
        """Set up test fixtures."""
        self.matcher = CableTerminationSetMatcher(
            model_class=get_object_type_model("dcim.cable"),
            name="logical_cable_termination_set",
        )

    def _data(self, a, b):
        return {
            "a_terminations": [{"object_type": t, "object_id": i} for t, i in a],
            "b_terminations": [{"object_type": t, "object_id": i} for t, i in b],
        }

    def test_has_required_fields_both_ends_nonempty(self):
        """Has required fields both ends nonempty."""
        self.assertTrue(self.matcher.has_required_fields(
            self._data([("dcim.interface", 1)], [("dcim.interface", 2)])))

    def test_has_required_fields_missing_end(self):
        """Has required fields missing end."""
        self.assertFalse(self.matcher.has_required_fields(
            {"a_terminations": [{"object_type": "dcim.interface", "object_id": 1}]}))

    def test_has_required_fields_empty_list(self):
        """Has required fields empty list."""
        self.assertFalse(self.matcher.has_required_fields(
            {"a_terminations": [], "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}]}))

    def test_fingerprint_equal_under_ab_swap(self):
        """Fingerprint equal under ab swap."""
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.interface", 2)], [("dcim.interface", 1)]))
        self.assertIsNotNone(fp1)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_equal_under_within_end_reorder(self):
        """Fingerprint equal under within end reorder."""
        fp1 = self.matcher.fingerprint(self._data(
            [("dcim.interface", 1), ("dcim.interface", 3)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data(
            [("dcim.interface", 3), ("dcim.interface", 1)], [("dcim.interface", 2)]))
        self.assertEqual(fp1, fp2)

    def test_fingerprint_differs_for_different_set(self):
        """Fingerprint differs for different set."""
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 9)]))
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_distinguishes_object_type(self):
        """Fingerprint distinguishes object type."""
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.frontport", 1)], [("dcim.interface", 2)]))
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_none_when_not_matchable(self):
        """Fingerprint none when not matchable."""
        self.assertIsNone(self.matcher.fingerprint({"a_terminations": []}))


class FindObjCacheKeyCableTestCase(TestCase):
    """Tests for _find_obj_cache_key with dcim.cable."""

    def test_find_obj_cache_key_none_for_cable(self):
        """Find obj cache key none for cable."""
        data = {
            "status": "connected",
            "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
        }
        self.assertIsNone(_find_obj_cache_key(data, "dcim.cable"))


class CableTerminationSetMatcherRegistrationTestCase(TestCase):
    """
    dcim.cable must resolve to a non-empty matcher list including the set matcher.

    Regression for: CableTerminationSetMatcher was defined but never wired into
    _LOGICAL_MATCHERS, so get_model_matchers("dcim.cable") returned [] and the
    matcher's build_queryset/fingerprint logic never ran for real cable ingestion.
    """

    def test_get_model_matchers_returns_cable_termination_set_matcher(self):
        """Get model matchers returns cable termination set matcher."""
        model_class = get_object_type_model("dcim.cable")
        matchers = get_model_matchers(model_class)
        self.assertTrue(
            any(isinstance(m, CableTerminationSetMatcher) for m in matchers),
            "CableTerminationSetMatcher is not registered for dcim.cable "
            "(get_model_matchers returned no such matcher)",
        )


class FindExistingObjectCableDbTestCase(TestCase):
    """
    DB-backed regression: two distinct cables must not cross-resolve.

    Exercises find_existing_object end-to-end (registration + build_queryset)
    against real Cable/CableTermination rows, per brief Step 8.
    """

    def setUp(self):
        """Set up test fixtures."""
        manufacturer = Manufacturer.objects.create(name="CableTestMfr", slug="cable-test-mfr")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="CableTestModel", slug="cable-test-model")
        device_role = DeviceRole.objects.create(name="CableTestRole", slug="cable-test-role", color="ff0000")
        site = Site.objects.create(name="CableTestSite", slug="cable-test-site")

        self.device1 = Device.objects.create(
            name="CableTestDevice1", device_type=device_type, role=device_role, site=site,
        )
        self.device2 = Device.objects.create(
            name="CableTestDevice2", device_type=device_type, role=device_role, site=site,
        )
        self.device3 = Device.objects.create(
            name="CableTestDevice3", device_type=device_type, role=device_role, site=site,
        )
        self.device4 = Device.objects.create(
            name="CableTestDevice4", device_type=device_type, role=device_role, site=site,
        )

        # A termination (interface) can belong to at most one cable, so each
        # cable in this fixture uses its own dedicated pair of interfaces.
        self.if1 = Interface.objects.create(device=self.device1, name="eth0", type="1000base-t")
        self.if2 = Interface.objects.create(device=self.device2, name="eth0", type="1000base-t")
        self.if3 = Interface.objects.create(device=self.device3, name="eth0", type="1000base-t")
        self.if4 = Interface.objects.create(device=self.device4, name="eth0", type="1000base-t")

        self.cable_12 = Cable.objects.create(status="connected")
        self.cable_12.a_terminations = [self.if1]
        self.cable_12.b_terminations = [self.if2]
        self.cable_12.save()

        self.cable_34 = Cable.objects.create(status="connected")
        self.cable_34.a_terminations = [self.if3]
        self.cable_34.b_terminations = [self.if4]
        self.cable_34.save()

    def _data(self, a_if, b_if):
        return {
            "status": "connected",
            "a_terminations": [{"object_type": "dcim.interface", "object_id": a_if.pk}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": b_if.pk}],
        }

    def test_finds_correct_cable_for_its_own_termination_set(self):
        """Finds correct cable for its own termination set."""
        result = find_existing_object(self._data(self.if1, self.if2), "dcim.cable")
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.cable_12.pk)

    def test_does_not_cross_resolve_distinct_cable(self):
        """Does not cross resolve distinct cable."""
        result_34 = find_existing_object(self._data(self.if3, self.if4), "dcim.cable")
        self.assertIsNotNone(result_34)
        self.assertEqual(result_34.pk, self.cable_34.pk)
        self.assertNotEqual(result_34.pk, self.cable_12.pk)

    def test_ab_swap_still_resolves_same_cable(self):
        """Ab swap still resolves same cable."""
        # A/B order is insignificant for identity (spec 5.3 contract).
        result = find_existing_object(self._data(self.if2, self.if1), "dcim.cable")
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.cable_12.pk)

    def test_no_match_for_nonexistent_termination_set(self):
        """No match for nonexistent termination set."""
        result = find_existing_object(self._data(self.if1, self.if3), "dcim.cable")
        self.assertIsNone(result)


class CableTerminationSetMatcherQuerysetTestCase(TestCase):
    """Tests for CableTerminationSetMatcher.build_queryset."""

    @classmethod
    def setUpTestData(cls):
        """Set Up Test Data."""
        site = Site.objects.create(name="S1", slug="s1")
        mfr = Manufacturer.objects.create(name="M1", slug="m1")
        dt = DeviceType.objects.create(manufacturer=mfr, model="DT1", slug="dt1")
        role = DeviceRole.objects.create(name="R1", slug="r1")
        dev = Device.objects.create(name="D1", site=site, device_type=dt, role=role)
        cls.if1 = Interface.objects.create(device=dev, name="eth0", type="1000base-t")
        cls.if2 = Interface.objects.create(device=dev, name="eth1", type="1000base-t")
        cls.if3 = Interface.objects.create(device=dev, name="eth2", type="1000base-t")
        cls.cable = Cable(a_terminations=[cls.if1], b_terminations=[cls.if2])
        cls.cable.save()
        cls.matcher = CableTerminationSetMatcher(
            model_class=get_object_type_model("dcim.cable"),
            name="logical_cable_termination_set",
        )

    def _data(self, *pairs):
        terms = [{"object_type": t, "object_id": i} for t, i in pairs]
        return {"a_terminations": terms[:1], "b_terminations": terms[1:]}

    def test_exact_set_matches(self):
        """Exact set matches."""
        data = self._data(("dcim.interface", self.if1.pk), ("dcim.interface", self.if2.pk))
        qs = self.matcher.build_queryset(data)
        self.assertIsNotNone(qs)
        self.assertEqual(list(qs.values_list("pk", flat=True)), [self.cable.pk])

    def test_exact_set_matches_under_ab_swap(self):
        """Exact set matches under ab swap."""
        data = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": self.if2.pk}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": self.if1.pk}],
        }
        self.assertEqual(
            list(self.matcher.build_queryset(data).values_list("pk", flat=True)),
            [self.cable.pk],
        )

    def test_empty_end_returns_none(self):
        """Empty end returns none."""
        qs = self.matcher.build_queryset(
            {"a_terminations": [{"object_type": "dcim.interface", "object_id": self.if1.pk}],
             "b_terminations": []}
        )
        self.assertIsNone(qs)  # has_required_fields False for empty b

    def test_superset_rejected(self):
        """Superset rejected."""
        data = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": self.if1.pk}],
            "b_terminations": [
                {"object_type": "dcim.interface", "object_id": self.if2.pk},
                {"object_type": "dcim.interface", "object_id": self.if3.pk},
            ],
        }
        self.assertEqual(list(self.matcher.build_queryset(data)), [])

    def test_partial_overlap_rejected(self):
        """Partial overlap rejected."""
        data = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": self.if1.pk}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": self.if3.pk}],
        }
        self.assertEqual(list(self.matcher.build_queryset(data)), [])

    def test_unresolved_returns_none(self):
        """Unresolved returns none."""
        data = {
            "a_terminations": [{"object_type": "dcim.interface",
                                "object_id": UnresolvedReference("dcim.interface", "u-1")}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": self.if2.pk}],
        }
        self.assertIsNone(self.matcher.build_queryset(data))

    def test_multi_termination_per_end_exact_match(self):
        """
        A cable with 2 terminations on one end matches its exact 3-set.

        Proves successive build_queryset filters create separate joins (one
        CableTermination row per requested pair), not a single-alias AND that
        would make multi-termination matching impossible. Uses fresh
        interfaces: a termination may only belong to one cable.
        """
        dev = self.if1.device
        m1 = Interface.objects.create(device=dev, name="m0", type="1000base-t")
        m2 = Interface.objects.create(device=dev, name="m1", type="1000base-t")
        m3 = Interface.objects.create(device=dev, name="m2", type="1000base-t")
        multi = Cable(a_terminations=[m1, m2], b_terminations=[m3])
        multi.save()
        data = {
            "a_terminations": [
                {"object_type": "dcim.interface", "object_id": m1.pk},
                {"object_type": "dcim.interface", "object_id": m2.pk},
            ],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": m3.pk}],
        }
        qs = self.matcher.build_queryset(data)
        self.assertEqual(list(qs.values_list("pk", flat=True)), [multi.pk])

    def test_multi_termination_superset_rejected(self):
        """A 2-per-end request must NOT match a cable missing one of them."""
        dev = self.if1.device
        m1 = Interface.objects.create(device=dev, name="n0", type="1000base-t")
        m2 = Interface.objects.create(device=dev, name="n1", type="1000base-t")
        m3 = Interface.objects.create(device=dev, name="n2", type="1000base-t")
        # cable only has {m1, m2}; request adds a third (m3)
        Cable(a_terminations=[m1], b_terminations=[m2]).save()
        data = {
            "a_terminations": [
                {"object_type": "dcim.interface", "object_id": m1.pk},
                {"object_type": "dcim.interface", "object_id": m3.pk},
            ],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": m2.pk}],
        }
        self.assertEqual(list(self.matcher.build_queryset(data)), [])

    def test_find_existing_object_matches_via_matcher(self):
        """Find existing object matches via matcher."""
        data = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": self.if1.pk}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": self.if2.pk}],
        }
        found = find_existing_object(data, "dcim.cable")
        self.assertIsNotNone(found)
        self.assertEqual(found.pk, self.cable.pk)


class CableFingerprintsNoCrashTestCase(TestCase):
    """Regression: cable fingerprinting must not crash on edge-case input."""

    def test_fingerprints_no_crash_on_termination_dicts(self):
        """Fingerprints no crash on termination dicts."""
        # regression: list-of-dict terminations must not raise
        # "unhashable type: 'dict'" in _fingerprint_all.
        from netbox_diode_plugin.api.matcher import fingerprints
        data = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
        }
        fps = fingerprints(data, "dcim.cable")  # must not raise
        self.assertIsInstance(fps, list)
        self.assertGreaterEqual(len(fps), 1)
