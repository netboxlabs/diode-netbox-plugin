#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

from unittest import mock
from uuid import uuid4

from core.models import ObjectType
from dcim.models import Manufacturer, RackType, Site
from django.contrib.auth import get_user_model
from extras.models import CustomField
from extras.models.customfields import CustomFieldTypeChoices
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

class GenerateDiffTestCase(APITestCase):
    """GenerateDiff test cases."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

        self.authorization_header = {"Authorization": "Bearer mocked_oauth_token"}
        self.diode_user = get_diode_user()
        self.auth_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            'authenticate',
            return_value=(self.diode_user, None)
        )
        self.auth_patcher.start()

        self.object_type = ObjectType.objects.get_for_model(Site)
        
        self.uuid_field = CustomField.objects.create(
            name='myuuid',
            type=CustomFieldTypeChoices.TYPE_TEXT,
            required=False,
            unique=True,
        )
        self.uuid_field.object_types.set([self.object_type])
        self.uuid_field.save()

        self.json_field = CustomField.objects.create(
            name='some_json',
            type=CustomFieldTypeChoices.TYPE_JSON,
            required=False,
            unique=False,
        )
        self.json_field.object_types.set([self.object_type])
        self.json_field.save()

        self.site_uuid = str(uuid4())
        self.site = Site.objects.create(
            name="Site Generate Diff 1",
            slug="site-generate-diff-1",
            facility="Alpha",
            description="First test site",
            physical_address="123 Fake St Lincoln NE 68588",
            shipping_address="123 Fake St Lincoln NE 68588",
            comments="Lorem ipsum etcetera",
        )
        self.site.custom_field_data[self.uuid_field.name] = self.site_uuid
        self.site.custom_field_data[self.json_field.name] = {
            "some_key": "some_value",
        }
        self.site.save()

        self.manufacturer = Manufacturer.objects.create(
            name="Manufacturer 1",
        )
        self.manufacturer.save()
        self.rack_type = RackType.objects.create(
            model="Rack Type 1",
            slug="rack-type-1",
            manufacturer=self.manufacturer,
        )
        self.rack_type.save()

    def tearDown(self):
        """Clean up after tests."""
        self.auth_patcher.stop()
        super().tearDown()

    def test_generate_diff_create_site(self):
        """Test generate diff create site."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "A New Site",
                    "slug": "a-new-site",
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.site")
        self.assertEqual(change.get("change_type"), "create")
        self.assertEqual(change.get("object_id"), None)
        self.assertIsNotNone(change.get("ref_id"))

        data = change.get("data", {})
        self.assertEqual(data.get("name"), "A New Site")
        self.assertEqual(data.get("slug"), "a-new-site")

    def test_generate_diff_create_site_with_custom_field(self):
        """Test generate diff create site with custom field."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "A New Site",
                    "slug": "a-new-site",
                    "custom_fields": {
                        "some_json": {
                            "json": '{"some_key": 1234567890}',
                        },
                    },
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.site")
        self.assertEqual(change.get("change_type"), "create")
        self.assertEqual(change.get("object_id"), None)
        self.assertIsNotNone(change.get("ref_id"))

        data = change.get("data", {})
        self.assertEqual(data.get("name"), "A New Site")
        self.assertEqual(data.get("slug"), "a-new-site")
        self.assertEqual(data.get("custom_fields", {}).get("some_json", {}).get("some_key"), 1234567890)

    def test_generate_diff_update_site(self):
        """Test generate diff update site."""
        """Test generate diff create site."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Site Generate Diff 1",
                    "slug": "site-generate-diff-1",
                    "comments": "An updated comment",
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.site")
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)
        self.assertEqual(change.get("ref_id"), None)
        self.assertEqual(change.get("data").get("name"), "Site Generate Diff 1")

        data = change.get("data", {})
        self.assertEqual(data.get("name"), "Site Generate Diff 1")
        self.assertEqual(data.get("slug"), "site-generate-diff-1")
        self.assertEqual(data.get("comments"), "An updated comment")

    def test_match_site_by_custom_field(self):
        """Test match site by custom field."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    # here name and slug are not present in the payload
                    # but we expect to match the existing site by the
                    # unique custom field myuuid
                    "comments": "A custom comment",
                    "custom_fields": {
                        "myuuid": {
                            "text": self.site_uuid,
                        },
                    },
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.site")
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)
        self.assertEqual(change.get("ref_id"), None)

        data = change.get("data", {})
        self.assertEqual(data.get("comments"), "A custom comment")
        self.assertEqual(data.get("custom_fields", {}).get("myuuid"), self.site_uuid)

        before = change.get("before", {})
        self.assertEqual(before.get("name"), "Site Generate Diff 1")
        self.assertEqual(before.get("slug"), "site-generate-diff-1")

    def test_generate_diff_update_rack_type_autoslug(self):
        """Test generate diff update rack type autoslug."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.racktype",
            "entity": {
                "rack_type": {
                    "model": "Rack Type 1",
                    "form_factor": "wall-frame",
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.racktype")
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.rack_type.id)
        self.assertEqual(change.get("ref_id"), None)

        data = change.get("data", {})
        self.assertEqual(data.get("model"), "Rack Type 1")
        self.assertEqual(data.get("slug"), None) # slug is not set, use prior slug
        self.assertEqual(data.get("form_factor"), "wall-frame")

        before = change.get("before", {})
        self.assertEqual(before.get("model"), "Rack Type 1")
        # correct slug is present in before data
        self.assertEqual(before.get("slug"), "rack-type-1")

    def test_generate_diff_update_rack_type_camel_case(self):
        """Test generate diff update rack type with came cased protoJSON."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.racktype",
            "entity": {
                "rackType": {
                    "slug": "rack-type-1",
                    "model": "Rack Type 1",
                    "formFactor": "wall-frame",
                },
            }
        }

        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("object_type"), "dcim.racktype")
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.rack_type.id)
        self.assertEqual(change.get("ref_id"), None)

        data = change.get("data", {})
        self.assertEqual(data.get("model"), "Rack Type 1")
        self.assertEqual(data.get("form_factor"), "wall-frame")

        before = change.get("before", {})
        self.assertEqual(before.get("model"), "Rack Type 1")

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response
