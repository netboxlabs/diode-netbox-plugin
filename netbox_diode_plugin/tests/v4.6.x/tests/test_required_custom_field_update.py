#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests for updating an object that lacks a required custom field value."""

from types import SimpleNamespace
from unittest import mock

from core.models import ObjectType
from extras.models import CustomField
from extras.models.customfields import CustomFieldTypeChoices
from rest_framework import status
from tenancy.models import Contact
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class RequiredCustomFieldUpdateTestCase(APITestCase):
    """
    An ingest that supplies a required custom field the existing object lacks.

    Reproduces netboxlabs/diode#603: the contact exists, a required custom
    field is added afterwards (so the contact has no value for it), and the
    ingest supplies that value. The plan must be an update that sets it, not
    a "Custom field ... is required." validation error.
    """

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=self.diode_user
        )
        self.introspect_patcher.start()

        contact_type = ObjectType.objects.get_for_model(Contact)
        self.global_id_field = CustomField.objects.create(
            name="ifxGlobalId",
            type=CustomFieldTypeChoices.TYPE_TEXT,
            required=False,
        )
        self.global_id_field.object_types.set([contact_type])

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def _add_required_unique_email_field(self):
        # added after the contact exists, as in the issue, so the contact
        # has no value for it
        field = CustomField.objects.create(
            name="uniqueEmail",
            type=CustomFieldTypeChoices.TYPE_TEXT,
            required=True,
            unique=True,
        )
        field.object_types.set([ObjectType.objects.get_for_model(Contact)])

    def _payload(self):
        return {
            "timestamp": 1,
            "object_type": "tenancy.contact",
            "entity": {
                "contact": {
                    "name": "Bugs Bunny",
                    "email": "bugs.bunny@acme.io",
                    "phone": "+1 234 567 890",
                    "custom_fields": {
                        "ifxGlobalId": {"text": "G1234567"},
                        "uniqueEmail": {"text": "bugs.bunny@acme.io"},
                    },
                },
            },
        }

    def _assert_update_sets_unique_email(self, contact):
        response = self.client.post(
            self.url, data=self._payload(), format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        changes = response.json()["change_set"]["changes"]
        contact_changes = [c for c in changes if c["object_type"] == "tenancy.contact"]
        self.assertEqual(len(contact_changes), 1)
        change = contact_changes[0]
        self.assertEqual(change["change_type"], "update")
        self.assertEqual(change["object_id"], contact.id)
        self.assertEqual(change["data"]["custom_fields"]["uniqueEmail"], "bugs.bunny@acme.io")

    def test_update_supplies_required_custom_field_existing_object_lacks(self):
        """The existing contact holds another custom field value (the issue's case)."""
        contact = Contact.objects.create(name="Bugs Bunny")
        contact.custom_field_data["ifxGlobalId"] = "G1234567"
        contact.save()
        self._add_required_unique_email_field()

        self._assert_update_sets_unique_email(contact)

    def test_update_supplies_required_custom_field_no_prior_custom_field_values(self):
        """Control: the existing contact holds no custom field values at all."""
        contact = Contact.objects.create(name="Bugs Bunny")
        self._add_required_unique_email_field()

        self._assert_update_sets_unique_email(contact)

    def test_update_omitting_required_custom_field_existing_object_lacks_fails(self):
        """
        An update that leaves a required custom field empty still fails at plan time.

        The planned data names every custom field (unset ones default to null),
        so the required check must look at the value, not only the key, or the
        plan passes and NetBox refuses the save at apply time instead.
        """
        contact = Contact.objects.create(name="Bugs Bunny")
        contact.custom_field_data["ifxGlobalId"] = "G1234567"
        contact.save()
        self._add_required_unique_email_field()

        payload = self._payload()
        del payload["entity"]["contact"]["custom_fields"]["uniqueEmail"]
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.json())
        self.assertEqual(
            response.json()["errors"]["tenancy.contact"]["custom_fields"],
            [["Custom field 'uniqueEmail' is required."]],
        )
