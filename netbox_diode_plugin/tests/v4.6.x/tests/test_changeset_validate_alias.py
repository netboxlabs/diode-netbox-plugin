"""Unit tests for ChangeSet.validate with serializer-aliased wire fields."""
from django.test import TestCase

from netbox_diode_plugin.api.common import NON_FIELD_ERRORS, Change, ChangeSet, ChangeType


class ValidateAliasedFieldsTests(TestCase):
    """validate() must map wire names to model attributes and never crash."""

    def _changeset(self, object_type, data):
        return ChangeSet(changes=[
            Change(change_type=ChangeType.CREATE, object_type=object_type, ref_id="1", data=data)
        ])

    def test_aliased_wire_field_validates_cleanly(self):
        """'attributes' must be renamed to attribute_data before model(**data)."""
        cs = self._changeset(
            "dcim.moduletype",
            {"model": "mt-alias", "manufacturer": 1, "attributes": {"ram": 64}},
        )
        self.assertIsNone(cs.validate())

    def test_unexpected_kwarg_becomes_error_not_crash(self):
        """A non-model key must surface as a validation error, not a TypeError."""
        cs = self._changeset(
            "dcim.module",
            {"device": 1, "module_bay": 1, "module_type": 1, "adopt_components": True},
        )
        errors = cs.validate()
        self.assertIsNotNone(errors)
        self.assertIn("adopt_components", str(errors["dcim.module"][NON_FIELD_ERRORS]))
