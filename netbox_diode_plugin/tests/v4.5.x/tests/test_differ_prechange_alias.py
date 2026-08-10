"""Prechange extraction must read aliased fields through their model source."""
from dcim.models import Manufacturer, ModuleType, ModuleTypeProfile
from django.test import TestCase

from netbox_diode_plugin.api.differ import prechange_data_from_instance


class PrechangeAliasTests(TestCase):
    """getattr must target attribute_data while keying the result 'attributes'."""

    @classmethod
    def setUpTestData(cls):
        """Seed a module type whose property view differs from the raw JSON."""
        mfr = Manufacturer.objects.create(name="alias-mfr", slug="alias-mfr")
        profile = ModuleTypeProfile.objects.create(
            name="alias-profile",
            schema={"properties": {"ram": {"type": "integer", "title": "RAM (GB)"}}},
        )
        cls.mt = ModuleType.objects.create(
            manufacturer=mfr, model="alias-mt", profile=profile,
            attribute_data={"ram": 64},
        )

    def test_prechange_reads_raw_attribute_data(self):
        """The wire key must carry the raw JSON, not the title-cased view."""
        data = prechange_data_from_instance(self.mt)
        self.assertEqual(data.get("attributes"), {"ram": 64})
        self.assertNotIn("attribute_data", data)
