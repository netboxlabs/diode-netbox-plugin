#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

import datetime
import logging
from uuid import uuid4

from core.models import ObjectType
from dcim.models import Device, Interface, Site
from django.contrib.auth import get_user_model
from extras.models import CustomField
from extras.models.customfields import CustomFieldTypeChoices
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

        self.datetime_field = CustomField.objects.create(
            name='mydatetime',
            type=CustomFieldTypeChoices.TYPE_DATETIME,
            required=False,
            unique=False,
        )
        self.datetime_field.object_types.set([self.object_type])
        self.datetime_field.save()

        self.date_field = CustomField.objects.create(
            name='mydate',
            type=CustomFieldTypeChoices.TYPE_DATE,
            required=False,
            unique=False,
        )
        self.date_field.object_types.set([self.object_type])
        self.date_field.save()

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
                        "device_type": {
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


    def test_generate_diff_and_apply_create_and_update_device_role(self):
        """Test generate diff and apply create and update device role."""
        device_uuid = str(uuid4())
        role_1_uuid = str(uuid4())
        role_2_uuid = str(uuid4())
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": f"Device {device_uuid}",
                    "device_type": {
                        "model": f"Device Type {uuid4()}",
                        "manufacturer": {
                            "name": f"Manufacturer {uuid4()}"
                        }
                    },
                    "role": {
                        "name": f"Role {role_1_uuid}"
                    },
                    "site": {
                        "name": f"Site {site_uuid}"
                    }
                },
            }
        }
        _, response = self.diff_and_apply(payload)
        new_device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(new_device.site.name, f"Site {site_uuid}")
        self.assertEqual(new_device.role.name, f"Role {role_1_uuid}")
        payload = {
            "timestamp": 1,
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": f"Device {device_uuid}",
                    "deviceType": {
                        "model": f"Device Type {uuid4()}",
                        "manufacturer": {
                            "name": f"Manufacturer {uuid4()}"
                        }
                    },
                    "role": {
                        "name": f"Role {role_2_uuid}"
                    },
                    "site": {
                        "name": f"Site {site_uuid}"
                    }
                },
            }
        }
        _, response = self.diff_and_apply(payload)
        device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(device.site.name, f"Site {site_uuid}")
        self.assertEqual(device.role.name, f"Role {role_2_uuid}")


    def test_generate_diff_and_apply_create_site_autoslug(self):
        """Test generate diff and apply create site."""
        """Test generate diff create site."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.slug, f"site-{site_uuid}")

    def test_generate_diff_and_apply_tags_merged(self):
        """Test generate diff and apply merges tags."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "tags": [
                        {"name": "tag 1"},
                        {"name": "tag 2"},
                    ],
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.tags.count(), 2)
        tag_names = [tag.name for tag in new_site.tags.all()]
        self.assertIn("tag 1", tag_names)
        self.assertIn("tag 2", tag_names)

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "tags": [
                        {"name": "tag 3"},
                    ],
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.tags.count(), 3)
        tag_names = [tag.name for tag in new_site.tags.all()]
        self.assertIn("tag 1", tag_names)
        self.assertIn("tag 2", tag_names)
        self.assertIn("tag 3", tag_names)

    def test_generate_diff_and_apply_refs_not_merged(self):
        """Test generate diff and apply does not merge reference lists."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "asns": [
                        {"asn": "1", "rir": {"name": "RIR 1"}},
                        {"asn": "2", "rir": {"name": "RIR 1"}},
                    ],
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.asns.count(), 2)
        asns = [asn.asn for asn in new_site.asns.all()]
        self.assertIn(1, asns)
        self.assertIn(2, asns)

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "asns": [
                        {"asn": "3", "rir": {"name": "RIR 1"}},
                    ],
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.asns.count(), 1)
        asns = [asn.asn for asn in new_site.asns.all()]
        self.assertNotIn(1, asns)
        self.assertNotIn(2, asns)
        self.assertIn(3, asns)


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
                        "device_type": {
                            "manufacturer": {
                                "Name": f"Manufacturer {uuid4()}",
                            },
                            "model": f"Device Type {uuid4()}",
                        },
                    },
                    "primary_mac_address": {
                        "mac_address": "00:00:00:00:00:01",
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_interface = Interface.objects.get(name=f"Interface {interface_uuid}")
        self.assertEqual(new_interface.primary_mac_address.mac_address, "00:00:00:00:00:01")

    def test_generate_diff_and_apply_create_device_with_primary_ip4_camel_case(self):
        """Test generate diff and apply create device with primary ip4 (camel case)."""
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

    def test_generate_diff_and_apply_create_device_with_primary_ip4(self):
        """Test generate diff and apply create device with primary ip4."""
        device_uuid = str(uuid4())
        interface_uuid = str(uuid4())
        addr = "192.168.1.1"
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": addr,
                    "assigned_object_interface": {
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
                            "device_type": {
                                "manufacturer": {
                                    "name": f"Manufacturer {uuid4()}",
                                },
                                "model": f"Device Type {uuid4()}",
                            },
                            "primary_ip4": {
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

    def test_generate_diff_and_apply_create_and_update_site_with_custom_field(self):
        """Test generate diff and apply create and update site with custom field."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "A New Custom Site",
                    "slug": "a-new-custom-site",
                    "custom_fields": {
                        "myuuid": {
                            "text": site_uuid,
                        },
                        "some_json": {
                            "json": '{"some_key": 9876543210}',
                        },
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name="A New Custom Site")
        self.assertEqual(new_site.custom_field_data[self.uuid_field.name], site_uuid)
        self.assertEqual(new_site.custom_field_data[self.json_field.name], {"some_key": 9876543210})

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "comments": "An updated comment",
                    "custom_fields": {
                        "myuuid": {
                            "text": site_uuid,
                        },
                        "some_json": {
                            "json": '{"some_key": 1234567890}',
                        },
                        "mydatetime": {
                            "datetime": "2026-01-01T09:00:00Z",
                        },
                        "mydate": {
                            "date": "2026-01-01T00:00:00Z",
                        },
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name="A New Custom Site")
        self.assertEqual(new_site.cf[self.uuid_field.name], site_uuid)
        self.assertEqual(new_site.cf[self.json_field.name], {"some_key": 1234567890})
        self.assertEqual(new_site.cf[self.datetime_field.name], datetime.datetime(2026, 1, 1, 9, 0, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(new_site.cf[self.date_field.name], datetime.date(2026, 1, 1))

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "custom_fields": {
                        "myuuid": {
                            "text": site_uuid,
                        },
                        "mydatetime": {
                            "datetime": "2026-01-01T10:00:00Z",
                        },
                        "mydate": {
                            "date": "2026-01-02T00:00:00Z",
                        },
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name="A New Custom Site")
        self.assertEqual(new_site.cf[self.uuid_field.name], site_uuid)
        self.assertEqual(new_site.cf[self.json_field.name], {"some_key": 1234567890})
        self.assertEqual(new_site.cf[self.datetime_field.name], datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(new_site.cf[self.date_field.name], datetime.date(2026, 1, 2))

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "custom_fields": {
                        "myuuid": {
                            "text": site_uuid,
                        },
                        "mydatetime": {
                            "datetime": "2026-01-01T10:00:00Z",
                        },
                        "mydate": {
                            "date": "2026-01-02T00:00:00Z",
                        },
                    },
                },
            }
        }
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.user_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        self.assertEqual(diff.get("changes", []), [])

    def test_generate_diff_wrong_type_date(self):
        """Test generate diff wrong type date."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Site Generate Diff 1",
                    "slug": "site-generate-diff-1",
                    "custom_fields": {
                        "mydate": {
                            "date": 12,
                        },
                    },
                },
            }
        }
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.user_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        diff = response1.json().get("change_set", {})

        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.user_header
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)


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
