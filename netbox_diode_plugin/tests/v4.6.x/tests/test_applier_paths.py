#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Applier path-helper tests."""

from django.test import SimpleTestCase

from netbox_diode_plugin.api.applier import _get_path, _set_path


class PathHelperListIndexTestCase(SimpleTestCase):
    """_get_path / _set_path must treat all-digit segments as list indices."""

    def test_get_path_into_list_of_dicts(self):
        data = {
            "a_terminations": [
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u0"},
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u1"},
            ],
        }
        self.assertEqual(_get_path(data, "a_terminations.0.object_id"), "new_object:dcim.interface:u0")
        self.assertEqual(_get_path(data, "a_terminations.1.object_id"), "new_object:dcim.interface:u1")

    def test_set_path_into_list_of_dicts(self):
        data = {
            "b_terminations": [
                {"object_type": "dcim.interface", "object_id": "new_object:dcim.interface:u9"},
            ],
        }
        _set_path(data, "b_terminations.0.object_id", 123)
        self.assertEqual(data["b_terminations"][0]["object_id"], 123)
        self.assertEqual(data["b_terminations"][0]["object_type"], "dcim.interface")

    def test_string_keys_still_work(self):
        data = {"name": {"nested": "x"}}
        self.assertEqual(_get_path(data, "name.nested"), "x")
        _set_path(data, "name.nested", "y")
        self.assertEqual(data["name"]["nested"], "y")
