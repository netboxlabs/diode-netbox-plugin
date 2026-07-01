#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - cabling generic-object transform."""

from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api import transformer
from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.supported_models import extract_supported_models


class TransformerImportTestCase(SimpleTestCase):
    """Imports needed by the generic-object-list arm are wired."""

    def test_get_generic_object_variant_imported(self):
        self.assertTrue(
            hasattr(transformer, "get_generic_object_variant"),
            "transformer must import get_generic_object_variant from plugin_utils",
        )


class IsSupportedGenericObjectTestCase(TestCase):
    """is_supported() accepts the generic-object-list field by its list name."""

    def test_a_terminations_is_supported(self):
        supported = extract_supported_models()
        result = transformer._transform_proto_json_1(
            {"a_terminations": [], "b_terminations": []},
            "dcim.cable",
            supported,
        )
        node = result[0]
        # empty lists are legal fields and must NOT be warned as unsupported
        self.assertNotIn("a_terminations", node["_warnings"])
        self.assertNotIn("b_terminations", node["_warnings"])

    def test_a_terminations_list_processing_non_empty(self):
        """Non-empty a_terminations with a valid generic-object item returns 200.

        Each item is a GenericObject dict such as {"object_interface": {...}}.
        The list-processing branch calls get_generic_object_variant to resolve
        the concrete object_type ("dcim.interface") and recurses with that type.
        The termination list item is emitted as
        {'object_type': 'dcim.interface', 'object_id': UnresolvedReference(...)}.
        OBS-1080 payload with non-empty terminations must not raise ValidationError.
        """
        supported = extract_supported_models()
        # Minimal OBS-1080-style payload: one interface termination on side A.
        payload = {
            "a_terminations": [
                {"object_interface": {"name": "eth0", "device": {"name": "router1"}}}
            ],
            "b_terminations": [],
        }
        # Must not raise — the list loop resolves the concrete object_type.
        result = transformer._transform_proto_json_1(payload, "dcim.cable", supported)
        cable_node = result[0]

        # a_terminations must be present and not warned as unsupported
        self.assertNotIn("a_terminations", cable_node["_warnings"])

        # The field is stored as a list of termination dicts
        a_terms = cable_node.get("a_terminations")
        self.assertIsInstance(a_terms, list)
        self.assertEqual(len(a_terms), 1)

        term = a_terms[0]
        self.assertIsInstance(term, dict, "termination item must be a dict")
        self.assertEqual(term.get("object_type"), "dcim.interface")
        self.assertIsInstance(
            term.get("object_id"),
            UnresolvedReference,
            "object_id must be an UnresolvedReference before resolution",
        )
        self.assertEqual(term["object_id"].object_type, "dcim.interface")

    def test_a_terminations_unknown_variant_warns_and_skips(self):
        """An unrecognised variant key emits a warning and skips the item."""
        supported = extract_supported_models()
        payload = {
            "a_terminations": [
                {"object_totally_unknown_thing": {"name": "x"}}
            ],
            "b_terminations": [],
        }
        result = transformer._transform_proto_json_1(payload, "dcim.cable", supported)
        cable_node = result[0]

        # The item is skipped; a warning is recorded on a_terminations
        self.assertIn("a_terminations", cable_node["_warnings"])
        # The list is present but empty (item was skipped)
        a_terms = cable_node.get("a_terminations")
        self.assertIsInstance(a_terms, list)
        self.assertEqual(len(a_terms), 0)


def _wrap(variant_key, inner):
    return {variant_key: inner}


class GenericObjectListExtractionTestCase(TestCase):
    """Each certified termination variant extracts to {object_type, object_id}."""

    CERTIFIED = [
        ("object_interface", "dcim.interface"),
        ("object_front_port", "dcim.frontport"),
        ("object_rear_port", "dcim.rearport"),
        ("object_console_port", "dcim.consoleport"),
        ("object_console_server_port", "dcim.consoleserverport"),
        ("object_power_port", "dcim.powerport"),
        ("object_power_outlet", "dcim.poweroutlet"),
        ("object_power_feed", "dcim.powerfeed"),
        ("object_circuit_termination", "circuits.circuittermination"),
    ]

    def _inner_for(self, object_type):
        if object_type == "circuits.circuittermination":
            return {"term_side": "A", "circuit": {"cid": "C1"}}
        if object_type == "dcim.powerfeed":
            return {"name": "feed1", "power_panel": {"name": "panel1"}}
        return {"name": "p1", "device": {"name": "Device A"}}

    def test_each_variant_extracts_termination_dict_and_refs(self):
        supported = extract_supported_models()
        for variant_key, expected_ot in self.CERTIFIED:
            with self.subTest(variant=variant_key):
                entity = {
                    "a_terminations": [_wrap(variant_key, self._inner_for(expected_ot))],
                    "b_terminations": [_wrap("object_interface", {"name": "eth1", "device": {"name": "Device B"}})],
                }
                nodes = transformer._transform_proto_json_1(entity, "dcim.cable", supported)
                cable = nodes[0]
                self.assertNotIn("a_terminations", cable["_warnings"])
                terms = cable["a_terminations"]
                self.assertEqual(len(terms), 1)
                t = terms[0]
                self.assertEqual(set(t.keys()), {"object_type", "object_id"})
                self.assertEqual(t["object_type"], expected_ot)
                self.assertIsInstance(t["object_id"], UnresolvedReference)
                self.assertEqual(t["object_id"].object_type, expected_ot)
                self.assertIn(t["object_id"].uuid, cable["_refs"])
                child_types = {n["_object_type"] for n in nodes[1:]}
                self.assertIn(expected_ot, child_types)
