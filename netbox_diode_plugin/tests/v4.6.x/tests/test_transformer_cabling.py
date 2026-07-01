#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests - cabling generic-object transform."""

from django.test import SimpleTestCase

from netbox_diode_plugin.api import transformer


class TransformerImportTestCase(SimpleTestCase):
    """Imports needed by the generic-object-list arm are wired."""

    def test_get_generic_object_variant_imported(self):
        self.assertTrue(
            hasattr(transformer, "get_generic_object_variant"),
            "transformer must import get_generic_object_variant from plugin_utils",
        )
