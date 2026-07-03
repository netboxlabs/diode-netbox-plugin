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
        """Get generic object variant imported."""
        self.assertTrue(
            hasattr(transformer, "get_generic_object_variant"),
            "transformer must import get_generic_object_variant from plugin_utils",
        )


class IsSupportedGenericObjectTestCase(TestCase):
    """is_supported() accepts the generic-object-list field by its list name."""

    def test_a_terminations_is_supported(self):
        """A terminations is supported."""
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
        """
        Non-empty a_terminations with a valid generic-object item returns 200.

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
        """Each variant extracts termination dict and refs."""
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


class UnsupportedVariantTestCase(TestCase):
    """Unknown/unsupported variant warns and skips that one termination."""

    def test_unknown_key_warns_and_skips_keeps_cable(self):
        """Unknown key warns and skips keeps cable."""
        supported = extract_supported_models()
        entity = {
            "a_terminations": [
                {"object_interface": {"name": "eth0", "device": {"name": "Device A"}}},
                {"object_not_a_real_variant": {"name": "x"}},
            ],
            "b_terminations": [
                {"object_cable_termination": {"foo": "bar"}},  # deprecated -> variant map returns None
                {"object_interface": {"name": "eth1", "device": {"name": "Device B"}}},
            ],
        }
        nodes = transformer._transform_proto_json_1(entity, "dcim.cable", supported)
        cable = nodes[0]
        self.assertEqual(len(cable["a_terminations"]), 1)
        self.assertEqual(cable["a_terminations"][0]["object_type"], "dcim.interface")
        self.assertEqual(len(cable["b_terminations"]), 1)
        self.assertEqual(cable["b_terminations"][0]["object_type"], "dcim.interface")
        self.assertIn("a_terminations", cable["_warnings"])
        self.assertIn("b_terminations", cable["_warnings"])
        self.assertTrue(any("object_not_a_real_variant" in w for w in cable["_warnings"]["a_terminations"]))
        self.assertTrue(any("object_cable_termination" in w for w in cable["_warnings"]["b_terminations"]))


class Obs1080AndMultiTerminationTestCase(TestCase):
    """OBS-1080 payload transforms cleanly; multi-object-per-end is supported."""

    def test_obs_1080_payload_extracts_both_interfaces(self):
        """Obs 1080 payload extracts both interfaces."""
        # NOTE: IsSupportedGenericObjectTestCase.test_a_terminations_list_processing_non_empty
        # already covers the single-interface-per-end path.  This test is distinct in that it
        # exercises both a_terminations AND b_terminations simultaneously and asserts the two
        # child interface nodes are emitted (len == 2), which the earlier test does not check.
        supported = extract_supported_models()
        entity = {
            "a_terminations": [{"object_interface": {"name": "eth0", "device": {"name": "A"}}}],
            "b_terminations": [{"object_interface": {"name": "eth1", "device": {"name": "B"}}}],
        }
        nodes = transformer._transform_proto_json_1(entity, "dcim.cable", supported)
        cable = nodes[0]
        self.assertEqual(cable["a_terminations"][0]["object_type"], "dcim.interface")
        self.assertEqual(cable["b_terminations"][0]["object_type"], "dcim.interface")
        ifaces = [n for n in nodes[1:] if n["_object_type"] == "dcim.interface"]
        self.assertEqual(len(ifaces), 2)
        a_uuid = cable["a_terminations"][0]["object_id"].uuid
        b_uuid = cable["b_terminations"][0]["object_id"].uuid
        self.assertIn(a_uuid, cable["_refs"])
        self.assertIn(b_uuid, cable["_refs"])

    def test_multi_object_per_end(self):
        """Multi object per end."""
        supported = extract_supported_models()
        entity = {
            "a_terminations": [
                {"object_interface": {"name": "eth0", "device": {"name": "A"}}},
                {"object_interface": {"name": "eth1", "device": {"name": "A"}}},
            ],
            "b_terminations": [
                {"object_front_port": {"name": "fp0", "device": {"name": "B"}}},
                {"object_front_port": {"name": "fp1", "device": {"name": "B"}}},
            ],
        }
        nodes = transformer._transform_proto_json_1(entity, "dcim.cable", supported)
        cable = nodes[0]
        self.assertEqual(len(cable["a_terminations"]), 2)
        self.assertEqual(len(cable["b_terminations"]), 2)
        self.assertEqual({t["object_type"] for t in cable["a_terminations"]}, {"dcim.interface"})
        self.assertEqual({t["object_type"] for t in cable["b_terminations"]}, {"dcim.frontport"})
        for end in ("a_terminations", "b_terminations"):
            for t in cable[end]:
                self.assertIn(t["object_id"].uuid, cable["_refs"])
        for end in ("a_terminations", "b_terminations"):
            for t in cable[end]:
                hash(t["object_id"])  # must not raise


class SameTerminationObjectBothEndsTestCase(TestCase):
    """
    Same logical object in two termination slots dedupes with no dangling ref.

    Before the fix, _update_dict_refs did not recurse into dict items of
    termination lists, so when the two identical interfaces deduped to one
    surviving uuid, the second termination kept a stale uuid that was never
    created -- and _pre_apply's created[stale_uuid] raised an uncaught
    KeyError (HTTP 500), the OBS-1080 failure class. Runs the FULL pipeline
    (transform_proto_json) because the rewrite happens in the dedup stage.
    """

    def test_same_interface_both_ends_no_dangling_ref(self):
        """Same interface on both ends -> both refs point to the survivor."""
        supported = extract_supported_models()
        iface = {"name": "eth0", "device": {"name": "router1"}}
        entity = {
            "a_terminations": [{"object_interface": dict(iface)}],
            "b_terminations": [{"object_interface": dict(iface)}],
        }
        entities = transformer.transform_proto_json(entity, "dcim.cable", supported)
        cable = next(e for e in entities if e["_object_type"] == "dcim.cable")
        iface_uuids = {
            e["_uuid"] for e in entities if e["_object_type"] == "dcim.interface"
        }
        # the two identical interfaces dedupe to exactly one surviving node
        self.assertEqual(len(iface_uuids), 1)
        a_ref = cable["a_terminations"][0]["object_id"]
        b_ref = cable["b_terminations"][0]["object_id"]
        self.assertIsInstance(a_ref, UnresolvedReference)
        self.assertIsInstance(b_ref, UnresolvedReference)
        # BOTH terminations reference the surviving interface (no dangling uuid)
        self.assertIn(a_ref.uuid, iface_uuids)
        self.assertIn(b_ref.uuid, iface_uuids)
        self.assertEqual(a_ref.uuid, b_ref.uuid)
