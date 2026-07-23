"""Unit tests for the serializer-aware supported-models gate."""
from django.test import TestCase

from netbox_diode_plugin.api.supported_models import extract_supported_models


class SerializerAwareGateTests(TestCase):
    """The gate classifies legal wire fields against the running serializer."""

    @classmethod
    def setUpTestData(cls):
        """Extract once; the function is process-cached."""
        cls.supported = extract_supported_models()

    def test_aliased_field_supported_with_source(self):
        """Moduletype 'attributes' is supported, backed by attribute_data."""
        info = self.supported["dcim.moduletype"]["fields"]["attributes"]
        self.assertEqual(info["source"], "attribute_data")
        self.assertEqual(info["type"], "JSONField")

    def test_every_entry_has_source_and_id(self):
        """Every fields entry carries source; id is present on every type."""
        for object_type, entry in self.supported.items():
            self.assertIn("id", entry["fields"], object_type)
            for wire, info in entry["fields"].items():
                self.assertIn("source", info, f"{object_type}.{wire}")

    def test_custom_fields_never_in_fields(self):
        """custom_fields stays owned by its dedicated path, not the gate."""
        for object_type, entry in self.supported.items():
            self.assertNotIn("custom_fields", entry["fields"], object_type)

    def test_serializer_only_fields(self):
        """Writable serializer fields with no model field are set aside."""
        self.assertIn("assignments", self.supported["circuits.circuit"]["serializer_only_fields"])
        self.assertIn("a_terminations", self.supported["dcim.cable"]["serializer_only_fields"])
        self.assertIn("b_terminations", self.supported["dcim.cable"]["serializer_only_fields"])

    def test_version_stale_wires_dropped(self):
        """Wires absent from this version's serializer are not supported."""
        fp = self.supported["dcim.frontport"]
        self.assertNotIn("rear_port", fp["fields"])
        self.assertNotIn("rear_port", fp["serializer_only_fields"])
        self.assertNotIn("rear_port_position", fp["fields"])
        self.assertNotIn("group", self.supported["tenancy.contact"]["fields"])

    def test_no_serializer_fallback(self):
        """Models without a serializer keep the legacy model-walk behavior."""
        entry = self.supported.get("core.managedfile")
        if entry is None:
            self.skipTest("core.managedfile not extracted on this version")
        self.assertEqual(entry["serializer_only_fields"], set())
        self.assertNotIn("file", entry["fields"])

    def test_every_type_has_serializer_only_key(self):
        """The new entry key exists for every extracted type."""
        for object_type, entry in self.supported.items():
            self.assertIn("serializer_only_fields", entry, object_type)
