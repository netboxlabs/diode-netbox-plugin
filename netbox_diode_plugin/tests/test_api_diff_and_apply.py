#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

from dcim.models import Interface, Site
from django.contrib.auth import get_user_model
from rest_framework import status
from users.models import Token
from utilities.testing import APITestCase

User = get_user_model()


class GenerateDiffAndApplyTestCase(APITestCase):
    """GenerateDiff -> ApplyChangeSet test cases."""

    def setUp(self):
        """Set up the test case."""
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.user = User.objects.create_user(username="testcommonuser")
        self.user_token = Token.objects.create(user=self.user)
        self.user_header = {"HTTP_AUTHORIZATION": f"Token {self.user_token.key}"}

        self.add_permissions("netbox_diode_plugin.add_diode")

    def test_generate_diff_and_apply_create_site(self):
        """Test generate diff and apply create site."""
        """Test generate diff create site."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Generate Diff and Apply Site",
                    "slug": "generate-diff-and-apply-site",
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        self.assertEqual(response.json().get("success"), True)

        new_site = Site.objects.get(name="Generate Diff and Apply Site")
        self.assertEqual(new_site.slug, "generate-diff-and-apply-site")

    def test_generate_diff_and_apply_create_interface_with_primay_mac_address(self):
        """Test generate diff and apply create interface with primary mac address."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.interface",
            "entity": {
                "interface": {
                    "name": "Interface 1x",
                    "type": "1000base-t",
                    "device": {
                        "name": "Device 1x",
                        "role": {
                            "Name": "Role ABC",
                        },
                        "site": {
                            "Name": "Site ABC",
                        },
                        "deviceType": {
                            "manufacturer": {
                                "Name": "Manufacturer A",
                            },
                            "model": "Device Type A",
                        },
                    },
                    "primaryMacAddress": {
                        "mac_address": "00:00:00:00:00:01",
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        self.assertEqual(response.json().get("success"), True)

        new_interface = Interface.objects.get(name="Interface 1x")
        self.assertEqual(new_interface.primary_mac_address.mac_address, "00:00:00:00:00:01")


    def diff_and_apply(self, payload):
        """Diff and apply the payload."""
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.user_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json()

        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.user_header
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        return (response1, response2)
