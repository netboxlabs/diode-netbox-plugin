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
