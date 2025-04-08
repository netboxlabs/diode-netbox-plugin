#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

import logging
from uuid import uuid4

from dcim.models import Device, Interface, Site
from django.contrib.auth import get_user_model
from ipam.models import IPAddress
from rest_framework import status
from users.models import Token
from utilities.testing import APITestCase

logger = logging.getLogger(__name__)

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

    def test_generate_diff_and_apply_create_interface_with_tags(self):
        """Test generate diff and apply create interface with tags."""
        interface_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.interface",
            "entity": {
                "interface": {
                    "name": f"Interface {interface_uuid}",
                    "mtu": "1500",
                    "mode": "access",
                    "tags": [
                        {"name": "tag 1"}
                    ],
                    "type": "1000base-t",
                    "device": {
                        "name": f"Device {uuid4()}",
                        "deviceType": {
                            "model": f"Device Type {uuid4()}",
                            "manufacturer": {
                                "name": f"Manufacturer {uuid4()}"
                            }
                        },
                        "role": {
                            "name": f"Role {uuid4()}"
                        },
                        "site": {
                            "name": f"Site {uuid4()}"
                        }
                    },
                    "enabled": True,
                    "description": "Physical interface"
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        new_interface = Interface.objects.get(name=f"Interface {interface_uuid}")
        self.assertEqual(new_interface.tags.count(), 1)
        self.assertEqual(new_interface.tags.first().name, "tag 1")


    def test_generate_diff_and_apply_create_site(self):
        """Test generate diff and apply create site."""
        """Test generate diff create site."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "slug": f"site-{site_uuid}",
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.slug, f"site-{site_uuid}")

    def test_generate_diff_and_apply_create_interface_with_primay_mac_address(self):
        """Test generate diff and apply create interface with primary mac address."""
        interface_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.interface",
            "entity": {
                "interface": {
                    "name": f"Interface {interface_uuid}",
                    "type": "1000base-t",
                    "device": {
                        "name": f"Device {uuid4()}",
                        "role": {
                            "Name": f"Role {uuid4()}",
                        },
                        "site": {
                            "Name": f"Site {uuid4()}",
                        },
                        "deviceType": {
                            "manufacturer": {
                                "Name": f"Manufacturer {uuid4()}",
                            },
                            "model": f"Device Type {uuid4()}",
                        },
                    },
                    "primaryMacAddress": {
                        "mac_address": "00:00:00:00:00:01",
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_interface = Interface.objects.get(name=f"Interface {interface_uuid}")
        self.assertEqual(new_interface.primary_mac_address.mac_address, "00:00:00:00:00:01")

    def test_generate_diff_and_apply_create_device_with_primary_ip4(self):
        """Test generate diff and apply create device with primary ip4."""
        device_uuid = str(uuid4())
        interface_uuid = str(uuid4())
        addr = "192.168.1.1"
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ipAddress": {
                    "address": addr,
                    "assignedObjectInterface": {
                        "name": f"Interface {interface_uuid}",
                        "type": "1000base-t",
                        "device": {
                            "name": f"Device {device_uuid}",
                            "role": {
                                "name": f"Role {uuid4()}",
                            },
                            "site": {
                                "name": f"Site {uuid4()}",
                            },
                            "deviceType": {
                                "manufacturer": {
                                    "name": f"Manufacturer {uuid4()}",
                                },
                                "model": f"Device Type {uuid4()}",
                            },
                            "primaryIp4": {
                                "address": addr,
                            },
                        },
                    },
                },
            },
        }

        _, response = self.diff_and_apply(payload)
        new_ipaddress = IPAddress.objects.get(address=addr)
        self.assertEqual(new_ipaddress.assigned_object.name, f"Interface {interface_uuid}")
        device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(device.primary_ip4.pk, new_ipaddress.pk)

    def diff_and_apply(self, payload):
        """Diff and apply the payload."""
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.user_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})

        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.user_header
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        return (response1, response2)
