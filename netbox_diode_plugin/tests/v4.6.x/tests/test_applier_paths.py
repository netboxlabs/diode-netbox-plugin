#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Applier path-helper tests."""

from django.test import SimpleTestCase

from netbox_diode_plugin.api.applier import _get_path, _pre_apply, _set_path
from netbox_diode_plugin.api.common import Change, ChangeType
from netbox_diode_plugin.api.plugin_utils import get_object_type_model


class PathHelperListIndexTestCase(SimpleTestCase):
    """_get_path / _set_path must treat all-digit segments as list indices."""

    def test_get_path_into_list_of_dicts(self):
        """Get path into list of dicts."""
        data = {
            "a_terminations": [
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u0"},
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u1"},
            ],
        }
        self.assertEqual(_get_path(data, "a_terminations.0.object_id"), "new_object:dcim.interface:u0")
        self.assertEqual(_get_path(data, "a_terminations.1.object_id"), "new_object:dcim.interface:u1")

    def test_set_path_into_list_of_dicts(self):
        """Set path into list of dicts."""
        data = {
            "b_terminations": [
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u9"},
            ],
        }
        _set_path(data, "b_terminations.0.object_id", 123)
        self.assertEqual(data["b_terminations"][0]["object_id"], 123)
        self.assertEqual(data["b_terminations"][0]["object_type"], "dcim.interface")

    def test_string_keys_still_work(self):
        """String keys still work."""
        data = {"name": {"nested": "x"}}
        self.assertEqual(_get_path(data, "name.nested"), "x")
        _set_path(data, "name.nested", "y")
        self.assertEqual(data["name"]["nested"], "y")


class _FakeInstance:
    def __init__(self, pk):
        self.pk = pk


class PreApplyTerminationResolutionTestCase(SimpleTestCase):
    """_pre_apply resolves termination object_id refs to {object_type, object_id: pk}."""

    def test_termination_refs_resolve_to_pk_dict_shape(self):
        """Termination refs resolve to pk dict shape."""
        ref_a = "new_object:dcim.interface:ua"
        ref_b = "new_object:dcim.interface:ub"
        change = Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.cable",
            ref_id="new_object:dcim.cable:uc",
            data={
                "status": "connected",
                "a_terminations": [{"object_type": "dcim.interface", "object_id": ref_a}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": ref_b}],
            },
            new_refs=["a_terminations.0.object_id", "b_terminations.0.object_id"],
        )
        created = {ref_a: _FakeInstance(101), ref_b: _FakeInstance(202)}

        model_class = get_object_type_model("dcim.cable")
        data = _pre_apply(model_class, change, created)

        self.assertEqual(data["a_terminations"], [{"object_type": "dcim.interface", "object_id": 101}])
        self.assertEqual(data["b_terminations"], [{"object_type": "dcim.interface", "object_id": 202}])
        self.assertIn("a_terminations", data)
        self.assertIn("b_terminations", data)

    def test_original_change_data_not_mutated(self):
        """A retried apply (e.g. after deadlock) must see the original string refs again."""
        ref_a = "new_object:dcim.interface:ua"
        change = Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.cable",
            ref_id="new_object:dcim.cable:uc",
            data={
                "status": "connected",
                "a_terminations": [{"object_type": "dcim.interface", "object_id": ref_a}],
            },
            new_refs=["a_terminations.0.object_id"],
        )
        created = {ref_a: _FakeInstance(101)}
        model_class = get_object_type_model("dcim.cable")

        _pre_apply(model_class, change, created)

        self.assertEqual(change.data["a_terminations"][0]["object_id"], ref_a)
