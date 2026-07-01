#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Unit tests for CableTerminationSetMatcher and dcim.cable pre-save routing."""

from django.test import TestCase

from netbox_diode_plugin.api.matcher import (
    CableTerminationSetMatcher,
    _find_obj_cache_key,
    requires_pre_save_match,
)
from netbox_diode_plugin.api.plugin_utils import get_object_type_model


class RequiresPreSaveMatchTestCase(TestCase):
    def test_dcim_cable_requires_pre_save_match(self):
        # Cable has no DB unique constraint; uniqueness lives on CableTermination,
        # so the applier must look up an existing cable before CREATE (spec 5.3, contract H).
        self.assertTrue(requires_pre_save_match("dcim.cable"))


class CableTerminationSetMatcherFingerprintTestCase(TestCase):
    def setUp(self):
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
        self.assertTrue(self.matcher.has_required_fields(
            self._data([("dcim.interface", 1)], [("dcim.interface", 2)])))

    def test_has_required_fields_missing_end(self):
        self.assertFalse(self.matcher.has_required_fields(
            {"a_terminations": [{"object_type": "dcim.interface", "object_id": 1}]}))

    def test_has_required_fields_empty_list(self):
        self.assertFalse(self.matcher.has_required_fields(
            {"a_terminations": [], "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}]}))

    def test_fingerprint_equal_under_ab_swap(self):
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.interface", 2)], [("dcim.interface", 1)]))
        self.assertIsNotNone(fp1)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_equal_under_within_end_reorder(self):
        fp1 = self.matcher.fingerprint(self._data(
            [("dcim.interface", 1), ("dcim.interface", 3)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data(
            [("dcim.interface", 3), ("dcim.interface", 1)], [("dcim.interface", 2)]))
        self.assertEqual(fp1, fp2)

    def test_fingerprint_differs_for_different_set(self):
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 9)]))
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_distinguishes_object_type(self):
        fp1 = self.matcher.fingerprint(self._data([("dcim.interface", 1)], [("dcim.interface", 2)]))
        fp2 = self.matcher.fingerprint(self._data([("dcim.frontport", 1)], [("dcim.interface", 2)]))
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_none_when_not_matchable(self):
        self.assertIsNone(self.matcher.fingerprint({"a_terminations": []}))


class FindObjCacheKeyCableTestCase(TestCase):
    def test_find_obj_cache_key_none_for_cable(self):
        data = {
            "status": "connected",
            "a_terminations": [{"object_type": "dcim.interface", "object_id": 1}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": 2}],
        }
        self.assertIsNone(_find_obj_cache_key(data, "dcim.cable"))
