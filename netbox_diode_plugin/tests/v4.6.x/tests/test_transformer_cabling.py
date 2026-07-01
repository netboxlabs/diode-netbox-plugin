#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - cabling generic-object transform."""

from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api import transformer
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

    def test_a_terminations_list_processing_branch_gap(self):
        """Non-empty a_terminations exercises the list loop (lines 202-212).

        Each item is a GenericObject dict such as {"object_interface": {...}}.
        The list-processing branch recurses with ref_info.object_type="" because
        is_generic_object=True carries an empty object_type on the RefInfo.
        get_generic_object_variant is not yet called inside the loop to resolve
        the concrete object_type before recursing.

        Current behaviour (gap): _supported_diode_fields("") raises a
        ValidationError because "" is not a registered supported type.
        This test pins that behaviour and documents the coverage gap that must
        be closed before OBS-1080 can be declared resolved.
        """
        from rest_framework.exceptions import ValidationError

        supported = extract_supported_models()
        # Minimal OBS-1080-style payload: one interface termination on side A.
        payload = {
            "a_terminations": [
                {"object_interface": {"name": "eth0", "device": {"name": "router1"}}}
            ],
            "b_terminations": [],
        }
        # The list loop recurses with object_type="" which is not a supported
        # model, so the transformer currently raises rather than handling it.
        with self.assertRaises(ValidationError) as ctx:
            transformer._transform_proto_json_1(payload, "dcim.cable", supported)
        self.assertIn(
            "is not supported in this version",
            str(ctx.exception.detail),
        )
