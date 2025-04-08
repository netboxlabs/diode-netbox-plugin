#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

from dcim.models import Site
from django.contrib.auth import get_user_model
from rest_framework import status
from users.models import Token
from utilities.testing import APITestCase

User = get_user_model()

class GenerateDiffTestCase(APITestCase):
    """GenerateDiff test cases."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

        self.user = User.objects.create_user(username="testcommonuser")
        self.add_permissions("netbox_diode_plugin.add_diode")
        self.user_token = Token.objects.create(user=self.user)

        self.user_header = {"HTTP_AUTHORIZATION": f"Token {self.user_token.key}"}

        self.site = Site.objects.create(
            name="Site Generate Diff 1",
            slug="site-generate-diff-1",
            facility="Alpha",
            description="First test site",
            physical_address="123 Fake St Lincoln NE 68588",
            shipping_address="123 Fake St Lincoln NE 68588",
            comments="Lorem ipsum etcetera",
        )


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



    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.user_header
        )
        self.assertEqual(response.status_code, status_code)
        return response
