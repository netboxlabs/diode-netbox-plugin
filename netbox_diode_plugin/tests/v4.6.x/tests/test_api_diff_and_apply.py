#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""

import copy
import datetime
import decimal
import logging
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import netaddr
from circuits.models import Circuit, Provider
from core.models import ObjectType
from dcim.models import Device, FrontPort, Interface, ModuleBay, RearPort, Site
from extras.models import CustomField
from extras.models.customfields import CustomFieldChoiceSet, CustomFieldChoiceSetBaseChoices, CustomFieldTypeChoices
from ipam.models import ASN, VRF, IPAddress, VLANGroup, VLANTranslationPolicy
from rest_framework import status
from users.models import Owner, OwnerGroup
from utilities.testing import APITestCase
from virtualization.models import Cluster, VMInterface

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)

def _get_error(response, object_name, field):
    return response.json().get("errors", {}).get(object_name, {}).get(field, [])

class GenerateDiffAndApplyTestCase(APITestCase):
    """GenerateDiff -> ApplyChangeSet test cases."""

    def setUp(self):
        """Set up the test case."""
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user = get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"}
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            '_introspect_token',
            return_value=self.diode_user
        )
        self.introspect_patcher.start()

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

        self.decimal_field = CustomField.objects.create(
            name='mydecimal',
            type=CustomFieldTypeChoices.TYPE_DECIMAL,
            required=False,
            unique=False,
        )
        self.decimal_field.object_types.set([self.object_type])
        self.decimal_field.save()

        self.long_text_field = CustomField.objects.create(
            name='my_long_text',
            type=CustomFieldTypeChoices.TYPE_LONGTEXT,
            required=False,
            unique=False,
        )
        self.long_text_field.object_types.set([self.object_type])
        self.long_text_field.save()

        choices = CustomFieldChoiceSet.objects.create(
            name='my_choices',
            base_choices=CustomFieldChoiceSetBaseChoices.IATA,
        )
        self.selection_field = CustomField.objects.create(
            name='my_selection',
            type=CustomFieldTypeChoices.TYPE_SELECT,
            required=False,
            unique=False,
            choice_set=choices,
        )
        self.selection_field.object_types.set([self.object_type])
        self.selection_field.save()

        self.multiple_selection_field = CustomField.objects.create(
            name='my_multiple_selection',
            type=CustomFieldTypeChoices.TYPE_MULTISELECT,
            required=False,
            unique=False,
            choice_set=choices,
        )
        self.multiple_selection_field.object_types.set([self.object_type])
        self.multiple_selection_field.save()

        self.object_field = CustomField.objects.create(
            name='my_object',
            type=CustomFieldTypeChoices.TYPE_OBJECT,
            required=False,
            unique=False,
            related_object_type=self.object_type,
        )
        self.object_field.object_types.set([self.object_type])
        self.object_field.save()

        self.multiple_objects_field = CustomField.objects.create(
            name='my_multiple_objects',
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            required=False,
            unique=False,
            related_object_type=self.object_type,
        )
        self.multiple_objects_field.object_types.set([self.object_type])
        self.multiple_objects_field.save()

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

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

    def test_generate_diff_and_apply_create_device_with_primary_ip6(self):
        """Test generate diff and apply create device with primary ip6."""
        device_uuid = str(uuid4())
        interface_uuid = str(uuid4())
        addr = "2001:db8::1"
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
                            "primary_ip6": {
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
        self.assertEqual(device.primary_ip6.pk, new_ipaddress.pk)

    def test_generate_diff_and_apply_create_device_with_oob_ip(self):
        """Test generate diff and apply create device with oob ip."""
        device_uuid = str(uuid4())
        interface_uuid = str(uuid4())
        addr = "192.168.1.1/24"
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
                            "oob_ip": {
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
        self.assertEqual(device.oob_ip.pk, new_ipaddress.pk)

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
                        "mydecimal": {
                            "decimal": 1234.567,
                        },
                        "some_json": {
                            "json": '{"some_key": 9876543210}',
                        },
                        "my_long_text": {
                            "long_text": "This is a long text",
                        },
                        "my_selection": {
                            "selection": "LAX",
                        },
                        "my_multiple_selection": {
                            "multiple_selection": ["JFK", "LAX"],
                        },
                        "my_object": {
                            "object": {
                                "site": {
                                    "name": "Custom Object Site Ref 1",
                                }
                            },
                        },
                        "my_multiple_objects": {
                            "multiple_objects": [
                                {
                                    "site": {
                                        "name": "Custom Object Site Ref 2",
                                    }
                                },
                                {
                                    "site": {
                                        "name": "Custom Object Site Ref 3",
                                    }
                                },
                            ],
                        },
                    },
                },
            }
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name="A New Custom Site")
        self.assertEqual(new_site.custom_field_data[self.uuid_field.name], site_uuid)
        self.assertEqual(new_site.custom_field_data[self.json_field.name], {"some_key": 9876543210})
        self.assertEqual(new_site.custom_field_data[self.decimal_field.name], 1234.567)
        self.assertEqual(new_site.custom_field_data[self.long_text_field.name], "This is a long text")
        self.assertEqual(new_site.custom_field_data[self.selection_field.name], "LAX")
        self.assertEqual(new_site.custom_field_data[self.multiple_selection_field.name], ["JFK", "LAX"])

        siteRef1 = Site.objects.get(name="Custom Object Site Ref 1")
        self.assertIsNotNone(siteRef1)
        self.assertEqual(new_site.custom_field_data[self.object_field.name], siteRef1.pk)
        siteRef2 = Site.objects.get(name="Custom Object Site Ref 2")
        self.assertIsNotNone(siteRef2)
        self.assertEqual(new_site.custom_field_data[self.multiple_objects_field.name][0], siteRef2.pk)
        siteRef3 = Site.objects.get(name="Custom Object Site Ref 3")
        self.assertIsNotNone(siteRef3)
        self.assertEqual(new_site.custom_field_data[self.multiple_objects_field.name][1], siteRef3.pk)

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
                        "my_multiple_objects": {
                            "multiple_objects": [
                                {
                                    "site": {
                                        "name": "Custom Object Site Ref 2",
                                    }
                                },
                                {
                                    "site": {
                                        "name": "Custom Object Site Ref 4",
                                    }
                                },
                            ],
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

        self.assertEqual(len(new_site.custom_field_data[self.multiple_objects_field.name]), 2)
        siteRef2 = Site.objects.get(name="Custom Object Site Ref 2")
        self.assertIsNotNone(siteRef2)
        self.assertEqual(new_site.custom_field_data[self.multiple_objects_field.name][0], siteRef2.pk)
        siteRef4 = Site.objects.get(name="Custom Object Site Ref 4")
        self.assertIsNotNone(siteRef3)
        self.assertEqual(new_site.custom_field_data[self.multiple_objects_field.name][1], siteRef4.pk)


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
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        self.assertEqual(diff.get("changes", []), [])

    def test_generate_diff_and_apply_circuit_with_install_date(self):
        """Test generate diff and apply circuit with date."""
        circuit_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "circuits.circuit",
            "entity": {
                "circuit": {
                    "cid": f"Circuit {circuit_uuid}",
                    "install_date": "2026-01-01T00:00:00Z",
                    "provider": {
                        "name": f"Provider {uuid4()}",
                    },
                    "type": {
                        "name": f"Ciruit Type {uuid4()}",
                    },
                },
            },
        }

        _, response = self.diff_and_apply(payload)
        new_circuit = Circuit.objects.get(cid=f"Circuit {circuit_uuid}")
        self.assertEqual(new_circuit.install_date, datetime.date(2026, 1, 1))

    def test_generate_diff_and_apply_site_with_lat_lon(self):
        """Test generate diff and apply site with lat and lon."""
        site_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "latitude":  23.456,
                    "longitude": 78.910,
                },
            },
        }

        _, response = self.diff_and_apply(payload)
        new_site = Site.objects.get(name=f"Site {site_uuid}")
        self.assertEqual(new_site.latitude, decimal.Decimal("23.456"))
        self.assertEqual(new_site.longitude, decimal.Decimal("78.910"))

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": f"Site {site_uuid}",
                    "latitude":  23.456,
                    "longitude": 78.910,
                },
            },
        }
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        self.assertEqual(diff.get("changes", []), [])

    def test_generate_diff_and_apply_wrong_type_date(self):
        """Test generate diff and apply wrong type date."""
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
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        diff = response1.json().get("change_set", {})

        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.authorization_header
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_diff_and_apply_vlan_group_with_vid_ranges(self):
        """Test generate diff and apply vlan group vid ranges."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vlangroup",
            "entity": {
                "vlan_group": {
                    "name": "VLAN Group 1",
                    "vid_ranges": [1,5,10,15],
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        new_vlan_group = VLANGroup.objects.get(name="VLAN Group 1")
        self.assertEqual(new_vlan_group.vid_ranges[0].lower, 1)
        self.assertEqual(new_vlan_group.vid_ranges[0].upper, 6)
        self.assertEqual(new_vlan_group.vid_ranges[1].lower, 10)
        self.assertEqual(new_vlan_group.vid_ranges[1].upper, 16)

        payload = {
            "timestamp": 1,
            "object_type": "ipam.vlangroup",
            "entity": {
                "vlan_group": {
                    "name": "VLAN Group 1",
                    "vid_ranges": [3,9,12,20],
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        new_vlan_group = VLANGroup.objects.get(name="VLAN Group 1")
        self.assertEqual(new_vlan_group.vid_ranges[0].lower, 3)
        self.assertEqual(new_vlan_group.vid_ranges[0].upper, 10)
        self.assertEqual(new_vlan_group.vid_ranges[1].lower, 12)
        self.assertEqual(new_vlan_group.vid_ranges[1].upper, 21)

    def test_generate_diff_and_apply_vrf_no_rd_dedup(self):
        """Re-ingesting an RD-less VRF resolves to the existing row (empty diff on second pass)."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {
                "vrf": {
                    "name": "VRF-A",
                },
            },
        }
        self.diff_and_apply(payload)
        # Second diff must be a no-op — proves the matcher resolved to the existing VRF.
        response = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json().get("change_set", {}).get("changes", []), [])
        vrfs = VRF.objects.filter(name="VRF-A")
        self.assertEqual(vrfs.count(), 1)
        self.assertIsNone(vrfs.first().rd)

    def test_generate_diff_and_apply_vrf_no_rd_does_not_match_rd_vrf(self):
        """An RD-less ingest must not collapse into an existing RD'd VRF with the same name."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-B", "rd": "65000:1"}},
        })
        self.diff_and_apply({
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-B"}},
        })
        vrfs = VRF.objects.filter(name="VRF-B").order_by("pk")
        self.assertEqual(vrfs.count(), 2)
        self.assertEqual(vrfs[0].rd, "65000:1")
        self.assertIsNone(vrfs[1].rd)

    def test_generate_diff_and_apply_vrf_rd_after_no_rd_does_not_collapse(self):
        """An RD'd ingest after a no-RD ingest with the same name should create a new VRF."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-C"}},
        })
        self.diff_and_apply({
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-C", "rd": "65000:2"}},
        })
        vrfs = VRF.objects.filter(name="VRF-C").order_by("pk")
        self.assertEqual(vrfs.count(), 2)
        self.assertIsNone(vrfs[0].rd)
        self.assertEqual(vrfs[1].rd, "65000:2")

    def test_generate_diff_and_apply_vrf_no_rd_update(self):
        """Repeat no-RD ingest with a different field value should update the same VRF, not duplicate."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-D", "description": "first"}},
        })
        self.diff_and_apply({
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-D", "description": "second"}},
        })
        vrfs = VRF.objects.filter(name="VRF-D")
        self.assertEqual(vrfs.count(), 1)
        self.assertEqual(vrfs.first().description, "second")

    def test_generate_diff_and_apply_vrf_no_rd_within_tenant_dedup(self):
        """Re-ingesting an RD-less VRF within the same tenant resolves to the existing row."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-E", "tenant": {"name": "Tenant E"}}},
        })
        # Second diff must be a no-op — proves logical_vrf_name_within_tenant matched.
        response = self.client.post(self.diff_url, data={
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-E", "tenant": {"name": "Tenant E"}}},
        }, format="json", **self.authorization_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json().get("change_set", {}).get("changes", []), [])
        self.assertEqual(VRF.objects.filter(name="VRF-E").count(), 1)

    def test_generate_diff_and_apply_vrf_no_rd_no_cross_tenant_collapse(self):
        """Two RD-less VRFs with the same name in different tenants must remain distinct."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-F", "tenant": {"name": "Tenant F1"}}},
        })
        self.diff_and_apply({
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-F", "tenant": {"name": "Tenant F2"}}},
        })
        vrfs = VRF.objects.filter(name="VRF-F").select_related("tenant").order_by("pk")
        self.assertEqual(vrfs.count(), 2)
        self.assertEqual({v.tenant.name for v in vrfs}, {"Tenant F1", "Tenant F2"})

    def test_generate_diff_and_apply_vrf_no_rd_no_tenant_does_not_match_tenant_vrf(self):
        """A no-tenant RD-less ingest must not collapse into a same-name VRF that has a tenant."""
        self.diff_and_apply({
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-G", "tenant": {"name": "Tenant G"}}},
        })
        self.diff_and_apply({
            "timestamp": 2,
            "object_type": "ipam.vrf",
            "entity": {"vrf": {"name": "VRF-G"}},
        })
        vrfs = VRF.objects.filter(name="VRF-G").order_by("pk")
        self.assertEqual(vrfs.count(), 2)
        self.assertEqual(vrfs[0].tenant.name, "Tenant G")
        self.assertIsNone(vrfs[1].tenant)

    def test_generate_diff_and_apply_ip_address_with_assigned_object_interface(self):
        """Test ip."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116",
                    "status": "deprecated",
                    "role": "secondary",
                    "assigned_object_interface": {
                        "device": {
                            "name": "Device ABC",
                            "device_type": {
                                "manufacturer": {
                                    "name": "Manufacturer ABC"
                                },
                                "model": "Device Type ABC"
                            },
                            "role": {
                                "name": "Role ABC"
                            },
                            "platform": {
                            "name": "Platform ABC",
                            "manufacturer": {
                                "name": "Manufacturer ABC"
                            }
                            },
                            "site": {
                                "name": "Site ABC"
                            }
                        },
                        "name": "Interface ABC",
                        "type": "1000base-t",
                        "mode": "access"
                    },
                    "description": "IP Address description",
                    "comments": "Lorem ipsum dolor sit amet",
                    "tags": [
                        {
                            "name": "tag 1"
                        },
                        {
                            "name": "tag 2"
                        }
                    ]
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_generate_diff_update_ip_address(self):
        """Test generate diff update ip address."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116",
                    "status": "deprecated",
                    "role": "secondary",
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116",
                    "status": "deprecated",
                    "role": "secondary",
                }
            }
        }

        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        self.assertEqual(diff.get("changes", []), [])

        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116/32",
                    "status": "deprecated",
                    "role": "secondary",
                }
            }
        }

        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        self.assertEqual(diff.get("changes", []), [])

        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116",
                    "status": "active",
                    "role": "secondary",
                }
            }
        }

        _ = self.diff_and_apply(payload)
        ip = IPAddress.objects.get(address="254.198.174.116")
        self.assertEqual(ip.status, "active")

        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116/24",
                    "status": "deprecated",
                }
            }
        }
        _ = self.diff_and_apply(payload)
        ip = IPAddress.objects.get(address="254.198.174.116/24")
        self.assertEqual(ip.role, "secondary")
        self.assertEqual(ip.status, "deprecated")
        self.assertEqual(ip.address, netaddr.IPNetwork("254.198.174.0/24"))

        vrf_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116/24",
                    "status": "active",
                    "vrf": {
                        "name": f"VRF {vrf_uuid}"
                    }
                }
            }
        }
        _ = self.diff_and_apply(payload)
        ip = IPAddress.objects.get(address="254.198.174.116/24", vrf__name=f"VRF {vrf_uuid}")
        self.assertEqual(ip.vrf.name, f"VRF {vrf_uuid}")
        self.assertEqual(ip.status, "active")

        ip2 = IPAddress.objects.get(address="254.198.174.116/24", vrf__isnull=True)
        self.assertEqual(ip2.vrf, None)
        self.assertEqual(ip2.status, "deprecated")

        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "254.198.174.116",
                    "status": "dhcp",
                    "vrf": {
                        "name": f"VRF {vrf_uuid}"
                    }
                }
            }
        }
        _ = self.diff_and_apply(payload)
        ip = IPAddress.objects.get(address="254.198.174.116", vrf__name=f"VRF {vrf_uuid}")
        self.assertEqual(ip.status, "dhcp")

        ip2 = IPAddress.objects.get(address="254.198.174.116/24", vrf__isnull=True)
        self.assertEqual(ip2.vrf, None)
        self.assertEqual(ip2.status, "deprecated")

    def test_generate_diff_and_apply_complex_vminterface(self):
        """Test generate diff and apply and update a complex vm interface."""
        payload = {
            "timestamp": 1,
            "object_type": "virtualization.vminterface",
            "entity": {
                "vm_interface": {
                    "virtual_machine": {
                        "name": "Virtual Machine 15e00bdf-4294-41df-a450-ffcfec6c7f2b",
                        "status": "active",
                        "site": {
                            "name": "Site 10"
                        },
                        "cluster": {
                            "name": "Cluster 10",
                            "type": {
                                "name": "Cluster type 10"
                            },
                            "group": {
                                "name": "Cluster group 10"
                            },
                            "status": "active",
                            "scope_site": {
                                "name": "Site 10"
                            }
                        },
                        "role": {
                            "name": "Role 10"
                        },
                        "platform": {
                            "name": "Platform 10",
                            "manufacturer": {
                            "name": "Manufacturer 10"
                            }
                        },
                        "vcpus": 1.0,
                        "memory": "4096",
                        "disk": "100",
                        "description": "Virtual Machine A description",
                        "comments": "Lorem ipsum dolor sit amet",
                        "tags": [
                            {
                            "name": "tag 1"
                            }
                        ]
                    },
                    "name": "Interface 47e8a593-8b74-4e94-9a8e-c02113f0bf88",
                    "enabled": False,
                    "mtu": "1500",
                    "primary_mac_address": {
                        "mac_address": "00:00:00:00:00:00"
                    },
                    "description": "Interface A description",
                    "tags": [
                        {
                            "name": "tag 1"
                        }
                    ]
                }
            }
        }
        _ = self.diff_and_apply(payload)

        payload2 = copy.deepcopy(payload)
        payload2['entity']['vm_interface']["mtu"] = "2000"
        payload2['entity']['vm_interface']["primary_mac_address"] = {
            "mac_address": "00:00:00:00:00:01"
        }
        _ = self.diff_and_apply(payload2)
        vm_interface = VMInterface.objects.get(name="Interface 47e8a593-8b74-4e94-9a8e-c02113f0bf88")
        self.assertEqual(vm_interface.mtu, 2000)
        self.assertEqual(vm_interface.primary_mac_address.mac_address, "00:00:00:00:00:01")

    def test_generate_diff_and_apply_dedupe_devicetype(self):
        """Test generate diff and apply dedupe devicetype in wireless link."""
        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "wireless.wirelesslink",
            "entity": {
                "wireless_link": {
                    "interface_a": {
                        "device": {
                            "name": "Device 1",
                            "device_type": {
                                "manufacturer": {"name": "Cisco"},
                                "model": "C2960S"
                            },
                            "role": {"name": "Device Role 1"},
                            "site": {"name": "Site 1"}
                        },
                        "name": "Radio0/1",
                        "type": "ieee802.11ac",
                        "enabled": True
                    },
                    "interface_b": {
                        "device": {
                            "name": "Device 2",
                            "device_type": {
                                "manufacturer": {"name": "Cisco"},
                                "model": "C2960S"
                            },
                            "role": {"name": "Device Role 1"},
                            "site": {"name": "Site 1"}
                        },
                        "name": "Radio0/1",
                        "type": "ieee802.11ac",
                        "enabled": True
                    },
                    "ssid": "P2P-Link-1",
                    "status": "connected",
                    "tenant": {"name": "Tenant 1"},
                    "auth_type": "wpa-personal",
                    "auth_cipher": "aes",
                    "auth_psk": "P2PLinkKey123!",
                    "distance": 1.5,
                    "distance_unit": "km",
                    "description": "Point-to-point wireless backhaul link",
                    "comments": "Building A to Building B wireless bridge",
                    "tags": [
                        {
                            "name": "Tag 1"
                        },
                        {
                            "name": "Tag 2"
                        }
                    ]
                }
            }
        }

        _ = self.diff_and_apply(payload)

    def test_generate_diff_and_apply_provider_with_accounts(self):
        """Test generate diff and apply provider with accounts."""
        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "circuits.provider",
            "entity": {
                "provider": {
                    "name": "Level 3 Communications",
                    "slug": "level3",
                    "description": "Global Tier 1 Internet Service Provider",
                    "comments": "Primary transit provider for data center connectivity",
                    "tags": [{"name": "Tag 1"}, {"name": "Tag 2"}],
                    "accounts": [
                        {
                            "provider": {"name": "Level 3 Communications"},
                            "name": "East Coast Account",
                            "account": "L3-12345",
                            "description": "East Coast regional services account",
                            "comments": "Managed through regional NOC"
                        },
                        {
                            "provider": {"name": "Level 3 Communications"},
                            "name": "West Coast Account",
                            "account": "L3-67890",
                            "description": "West Coast regional services account",
                            "comments": "Managed through regional NOC"
                        }
                    ],
                    "asns": [
                        {
                            "asn": "3356",
                            "rir": {"name": "ARIN"},
                            "tenant": {"name": "Tenant 1"},
                            "description": "Level 3 Global ASN",
                            "comments": "Primary transit ASN"
                        }
                    ]
                }
            }
        }

        _ = self.diff_and_apply(payload)
        provider = Provider.objects.get(name="Level 3 Communications")
        self.assertEqual(provider.accounts.count(), 2)
        self.assertEqual(provider.asns.count(), 1)

    def test_generate_diff_and_apply_module_bay_with_module(self):
        """Test generate diff and apply module bay with a module installed (non-circular)."""
        # First create the module bay
        bay_payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "dcim.modulebay",
            "entity": {
                "module_bay": {
                    "device": {
                        "name": "Device 1",
                        "role": {"name": "Device Role 1"},
                        "device_type": {
                            "manufacturer": {"name": "Cisco"},
                            "model": "C2960S"
                        },
                        "site": {"name": "Site 1"}
                    },
                    "name": "Module Bay 1",
                    "label": "STACK-1",
                    "position": "Rear",
                    "description": "Primary stacking module bay",
                    "tags": [{"name": "Tag 1"}, {"name": "Tag 2"}]
                }
            }
        }
        _ = self.diff_and_apply(bay_payload)
        module_bay = ModuleBay.objects.get(name="Module Bay 1")
        self.assertEqual(module_bay.device.name, "Device 1")

        # Then install a module into that bay (non-circular — module references
        # the same bay it's installed in, no sub-bays that reference back)
        module_payload = {
            "timestamp": "2025-04-16T02:58:21.564615Z",
            "object_type": "dcim.module",
            "entity": {
                "module": {
                    "device": {
                        "name": "Device 1",
                        "role": {"name": "Device Role 1"},
                        "device_type": {
                            "manufacturer": {"name": "Cisco"},
                            "model": "C2960S"
                        },
                        "site": {"name": "Site 1"}
                    },
                    "module_type": {
                        "manufacturer": {"name": "Cisco"},
                        "model": "C2960S-STACK"
                    },
                    "module_bay": {
                        "name": "Module Bay 1",
                        "device": {
                            "name": "Device 1",
                            "role": {"name": "Device Role 1"},
                            "device_type": {
                                "manufacturer": {"name": "Cisco"},
                                "model": "C2960S"
                            },
                            "site": {"name": "Site 1"}
                        }
                    }
                }
            }
        }
        _ = self.diff_and_apply(module_payload)
        from dcim.models import Module
        module = Module.objects.get(module_bay__name="Module Bay 1")
        self.assertEqual(module.device.name, "Device 1")
        self.assertEqual(module.module_type.manufacturer.name, "Cisco")
        self.assertEqual(module.module_type.model, "C2960S-STACK")
        self.assertEqual(module.module_bay.name, "Module Bay 1")

    def test_generate_diff_and_apply_module_bay_circular_ref_fails(self):
        """Test generate diff and apply module bay."""
        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "dcim.modulebay",
            "entity":     {
                "module_bay": {
                    "name": "Module Bay 1",
                    "device": {
                        "name": "Device 1",
                        "role": {"name": "Device Role 1"},
                        "device_type": {
                            "manufacturer": {"name": "Cisco"},
                            "model": "C2960S"
                        },
                        "site": {"name": "Site 1"}
                    },
                    "module": {
                        "asset_tag": "1234567890",
                        "device": {
                            "name": "Device 1",
                            "role": {"name": "Device Role 1"},
                            "device_type": {
                                "manufacturer": {"name": "Cisco"},
                                "model": "C2960S"
                            },
                            "site": {"name": "Site 1"}
                        },
                        "module_type": {
                            "manufacturer": {"name": "Cisco"},
                            "model": "C2960S-STACK"
                        },
                        "module_bay": {
                            "name": "Module Bay 1",
                            "device": {
                                "name": "Device 1",
                                "role": {"name": "Device Role 1"},
                                "device_type": {
                                    "manufacturer": {"name": "Cisco"},
                                    "model": "C2960S"
                                },
                                "site": {"name": "Site 1"}
                            },
                            "module": {
                                "asset_tag": "1234567890",
                            }
                        }
                    },
                    "label": "STACK-2",
                    "position": "Rear",
                    "description": "Secondary stacking module bay",
                    "tags": [{"name": "Tag 1"}, {"name": "Tag 2"}]
                }
            }
        }
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})

        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.authorization_header
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertIn(
            "A module bay cannot belong to a module installed within it.",
            _get_error(response2, "dcim.modulebay", "__all__")
        )

    def test_generate_diff_and_apply_virtual_machine_with_primary_ip_4_ok(self):
        """Test generate diff and apply virtual machine with primary ip 4 assigned."""
        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "virtualization.virtualmachine",
            "entity": {
                "timestamp": "2025-04-16T13:45:02.045208Z",
                "virtual_machine": {
                    "name": "app-server-01",
                    "status": "active",
                    "site": {"name": "Site 1"},
                    "cluster": {
                        "name": "Cluster 1",
                        "type": {"name": "Cluster Type 1"}
                    },
                    "device": {
                        "name": "Device 1",
                        "device_type": {
                            "manufacturer": {"name": "Cisco"},
                            "model": "C2960S"
                        },
                        "role": {"name": "Device Role 1"},
                        "site": {"name": "Site 1"},
                        "cluster": {
                            "name": "Cluster 1",
                            "type": {"name": "Cluster Type 1"}
                        }
                    },
                    "serial": "VM-2023-001",
                    "role": {"name": "Application Server"},
                    "tenant": {"name": "Tenant 1"},
                    "platform": {"name": "Ubuntu 22.04"},
                    "primary_ip4": {
                        "address": "192.168.2.10",
                        "assigned_object_vm_interface": {
                            "virtual_machine": {
                                "name": "app-server-01",
                                "cluster": {
                                    "name": "Cluster 1",
                                    "type": {"name": "Cluster Type 1"}
                                },
                                "tenant": {"name": "Tenant 1"},
                            },
                            "name": "eth0",
                            "enabled": True,
                            "mtu": "1500",
                        }
                    },
                    "vcpus": 4.0,
                    "memory": "214748364",
                    "disk": "147483647",
                    "description": "Primary application server instance",
                    "comments": "Hosts critical business applications",
                    "tags": [
                        {
                            "name": "Tag 1"
                        },
                        {
                            "name": "Tag 2"
                        }
                    ]
                }
            }
        }
        _ = self.diff_and_apply(payload)

    def test_generate_diff_and_apply_update_cluster_location(self):
        """Test generate diff and apply update cluster location, same site."""
        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "virtualization.cluster",
            "entity":     {
                "cluster": {
                    "name": "Cluster A",
                    "type": {"name": "Cluster Type 1"},
                    "group": {"name": "Cluster Group 1"},
                    "status": "active",
                    "tenant": {"name": "Tenant 1"},
                    "scope_site": {"name": "Site 1"},
                    "description": "Cluster 1 Description",
                    "comments": "Cluster 1 Comments",
                    "tags": [{"name": "Tag 1"}]
                }
            },
        }
        _ = self.diff_and_apply(payload)

        cluster = Cluster.objects.get(name="Cluster A")
        self.assertEqual(cluster.scope.name, "Site 1")

        payload = {
            "timestamp": "2025-04-16T02:58:20.564615Z",
            "object_type": "virtualization.cluster",
            "entity":     {
                "cluster": {
                    "name": "Cluster A",
                    "type": {"name": "Cluster Type 1"},
                    "group": {"name": "Cluster Group 1"},
                    "status": "active",
                    "tenant": {"name": "Tenant 1"},
                    "scope_location": {"name": "Location 1", "site": {"name": "Site 1"}},
                    "description": "Cluster 1 Description",
                    "comments": "Cluster 1 Comments",
                    "tags": [{"name": "Tag 1"}]
                }
            },
        }
        _ = self.diff_and_apply(payload)
        cluster = Cluster.objects.get(name="Cluster A")
        self.assertEqual(cluster.scope.name, "Location 1")

    def test_generate_diff_and_apply_create_and_update_owner(self):
        """Test generate diff and apply create and update owner (NetBox 4.5.0)."""
        owner_uuid = str(uuid4())
        group_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "users.owner",
            "entity": {
                "owner": {
                    "name": f"Owner {owner_uuid}",
                    "group": {
                        "name": f"Owner Group {group_uuid}",
                    },
                    "description": "Primary network owner",
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        new_owner = Owner.objects.get(name=f"Owner {owner_uuid}")
        self.assertEqual(new_owner.description, "Primary network owner")
        self.assertEqual(new_owner.group.name, f"Owner Group {group_uuid}")

        payload = {
            "timestamp": 1,
            "object_type": "users.owner",
            "entity": {
                "owner": {
                    "name": f"Owner {owner_uuid}",
                    "group": {
                        "name": f"Owner Group {group_uuid}",
                    },
                    "description": "Updated network owner",
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        updated_owner = Owner.objects.get(name=f"Owner {owner_uuid}")
        self.assertEqual(updated_owner.description, "Updated network owner")

    def test_generate_diff_and_apply_create_and_update_ownergroup(self):
        """Test generate diff and apply create and update ownergroup (NetBox 4.5.0)."""
        group_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "users.ownergroup",
            "entity": {
                "owner_group": {
                    "name": f"Owner Group {group_uuid}",
                    "description": "Network operations team",
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        new_group = OwnerGroup.objects.get(name=f"Owner Group {group_uuid}")
        self.assertEqual(new_group.description, "Network operations team")

        payload = {
            "timestamp": 1,
            "object_type": "users.ownergroup",
            "entity": {
                "owner_group": {
                    "name": f"Owner Group {group_uuid}",
                    "description": "Updated network operations team",
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        updated_group = OwnerGroup.objects.get(name=f"Owner Group {group_uuid}")
        self.assertEqual(updated_group.description, "Updated network operations team")

    def test_generate_diff_and_apply_create_device_with_owner(self):
        """Test generate diff and apply create device with owner field (NetBox 4.5.0)."""
        device_uuid = str(uuid4())
        owner_uuid = str(uuid4())
        group_uuid = str(uuid4())
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
                        "name": f"Role {uuid4()}"
                    },
                    "site": {
                        "name": f"Site {uuid4()}"
                    },
                    "owner": {
                        "name": f"Owner {owner_uuid}",
                        "group": {
                            "name": f"Owner Group {group_uuid}",
                        },
                    },
                },
            }
        }
        _, response = self.diff_and_apply(payload)
        new_device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(new_device.owner.name, f"Owner {owner_uuid}")

    def test_generate_diff_and_apply_create_circuit_with_owner(self):
        """Test generate diff and apply create circuit with owner field (NetBox 4.5.0)."""
        circuit_uuid = str(uuid4())
        owner_uuid = str(uuid4())
        group_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "circuits.circuit",
            "entity": {
                "circuit": {
                    "cid": f"Circuit {circuit_uuid}",
                    "provider": {
                        "name": f"Provider {uuid4()}",
                    },
                    "type": {
                        "name": f"Circuit Type {uuid4()}",
                    },
                    "owner": {
                        "name": f"Owner {owner_uuid}",
                        "group": {
                            "name": f"Owner Group {group_uuid}",
                        },
                    },
                },
            },
        }
        _, response = self.diff_and_apply(payload)
        new_circuit = Circuit.objects.get(cid=f"Circuit {circuit_uuid}")
        self.assertEqual(new_circuit.owner.name, f"Owner {owner_uuid}")

    def test_generate_diff_and_apply_update_device_owner(self):
        """Test generate diff and apply update device owner (NetBox 4.5.0)."""
        device_uuid = str(uuid4())
        site_uuid = str(uuid4())
        owner1_uuid = str(uuid4())
        owner2_uuid = str(uuid4())
        group_uuid = str(uuid4())

        # Create device with initial owner
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
                        "name": f"Role {uuid4()}"
                    },
                    "site": {
                        "name": f"Site {site_uuid}"
                    },
                    "owner": {
                        "name": f"Owner {owner1_uuid}",
                        "group": {
                            "name": f"Owner Group {group_uuid}",
                        },
                    },
                },
            }
        }
        _, response = self.diff_and_apply(payload)
        device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(device.owner.name, f"Owner {owner1_uuid}")

        # Update device to different owner
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
                        "name": f"Role {uuid4()}"
                    },
                    "site": {
                        "name": f"Site {site_uuid}"
                    },
                    "owner": {
                        "name": f"Owner {owner2_uuid}",
                        "group": {
                            "name": f"Owner Group {group_uuid}",
                        },
                    },
                },
            }
        }
        _, response = self.diff_and_apply(payload)
        device = Device.objects.get(name=f"Device {device_uuid}")
        self.assertEqual(device.owner.name, f"Owner {owner2_uuid}")

    def test_generate_diff_and_apply_create_frontport_with_positions(self):
        """Test generate diff and apply create frontport with positions field (NetBox 4.5.0)."""
        device_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.frontport",
            "entity": {
                "front_port": {
                    "device": {
                        "name": f"Device {device_uuid}",
                        "device_type": {
                            "manufacturer": {"name": f"Manufacturer {uuid4()}"},
                            "model": f"Device Type {uuid4()}"
                        },
                        "role": {"name": f"Role {uuid4()}"},
                        "site": {"name": f"Site {uuid4()}"}
                    },
                    "name": f"Front Port {uuid4()}",
                    "type": "8p8c",
                    "rear_port": {
                        "device": {
                            "name": f"Device {device_uuid}",
                            "device_type": {
                                "manufacturer": {"name": f"Manufacturer {uuid4()}"},
                                "model": f"Device Type {uuid4()}"
                            },
                            "role": {"name": f"Role {uuid4()}"},
                            "site": {"name": f"Site {uuid4()}"}
                        },
                        "name": f"Rear Port {uuid4()}",
                        "type": "8p8c",
                        "positions": "2"
                    },
                    "rear_port_position": "1",
                    "description": "Front port with positions"
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_generate_diff_and_apply_create_rearport(self):
        """Test generate diff and apply create rearport (NetBox 4.5.0)."""
        device_uuid = str(uuid4())
        rearport_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "dcim.rearport",
            "entity": {
                "rear_port": {
                    "device": {
                        "name": f"Device {device_uuid}",
                        "device_type": {
                            "manufacturer": {"name": f"Manufacturer {uuid4()}"},
                            "model": f"Device Type {uuid4()}"
                        },
                        "role": {"name": f"Role {uuid4()}"},
                        "site": {"name": f"Site {uuid4()}"}
                    },
                    "name": f"Rear Port {rearport_uuid}",
                    "type": "8p8c",
                    "positions": "4",
                    "description": "Rear port with multiple positions"
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        new_rearport = RearPort.objects.get(name=f"Rear Port {rearport_uuid}")
        self.assertEqual(new_rearport.positions, 4)
        self.assertEqual(new_rearport.type, "8p8c")

    def test_generate_diff_and_apply_create_asn_with_sites(self):
        """Test generate diff and apply create ASN with sites field (NetBox 4.5.0)."""
        site1_uuid = str(uuid4())
        site2_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "ipam.asn",
            "entity": {
                "asn": {
                    "asn": "65001",
                    "rir": {"name": f"RIR {uuid4()}"},
                    "description": "ASN with multiple sites",
                    "sites": [
                        {"name": f"Site {site1_uuid}"},
                        {"name": f"Site {site2_uuid}"}
                    ]
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        new_asn = ASN.objects.get(asn=65001)
        self.assertEqual(new_asn.sites.count(), 2)
        site_names = [site.name for site in new_asn.sites.all()]
        self.assertIn(f"Site {site1_uuid}", site_names)
        self.assertIn(f"Site {site2_uuid}", site_names)

    def test_generate_diff_and_apply_create_vlan_translation_policy(self):
        """Test generate diff and apply create VLAN translation policy (NetBox 4.5.0)."""
        policy_uuid = str(uuid4())
        owner_uuid = str(uuid4())
        group_uuid = str(uuid4())
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vlantranslationpolicy",
            "entity": {
                "vlan_translation_policy": {
                    "name": f"VLAN Translation Policy {policy_uuid}",
                    "description": "Policy for VLAN translation",
                    "owner": {
                        "name": f"Owner {owner_uuid}",
                        "group": {
                            "name": f"Owner Group {group_uuid}",
                        },
                    },
                }
            }
        }
        _, response = self.diff_and_apply(payload)
        new_policy = VLANTranslationPolicy.objects.get(name=f"VLAN Translation Policy {policy_uuid}")
        self.assertEqual(new_policy.description, "Policy for VLAN translation")
        self.assertEqual(new_policy.owner.name, f"Owner {owner_uuid}")

    def test_multiobject_cf_rediff_noop(self):
        """
        Test that re-diffing a device with multiobject custom field produces no changes.

        INT-219: multiobject custom field values are order-insensitive (sets),
        but the differ was comparing them as ordered lists. This caused phantom
        changesets on every re-diff because the "before" IDs (from queryset,
        ordered by name) didn't match the "desired" IDs (sorted numerically).
        """
        device_cf = CustomField.objects.create(
            name='int219_multi_sites',
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            required=False,
            related_object_type=ObjectType.objects.get_for_model(Site),
        )
        device_object_type = ObjectType.objects.get_for_model(Device)
        device_cf.object_types.set([device_object_type])
        device_cf.save()

        # Pre-create "Gamma" so it gets a LOWER ID than Alpha and Beta.
        # When diff+apply later creates Alpha and Beta, they get higher IDs.
        # This ensures name-alphabetical order (Alpha, Beta, Gamma) differs
        # from numeric ID order (Gamma, Alpha, Beta) — which triggers the bug.
        Site.objects.create(name="INT219-Site-Gamma", slug="int219-site-gamma")

        payload = {
            "timestamp": 1,
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "INT219-Test-Device",
                    "role": {"name": "INT219-Role"},
                    "site": {"name": "INT219-Site-Primary"},
                    "device_type": {
                        "model": "INT219-Model",
                        "manufacturer": {"name": "INT219-Manufacturer"},
                    },
                    "serial": "INT219-SERIAL-001",
                    "custom_fields": {
                        "int219_multi_sites": {
                            "multiple_objects": [
                                {"site": {"name": "INT219-Site-Alpha"}},
                                {"site": {"name": "INT219-Site-Beta"}},
                                {"site": {"name": "INT219-Site-Gamma"}},
                            ],
                        },
                    },
                },
            },
        }

        # First diff+apply: creates the device, Alpha, Beta (Gamma already exists)
        self.diff_and_apply(payload)
        device = Device.objects.get(name="INT219-Test-Device")
        self.assertIsNotNone(device)
        self.assertEqual(len(device.custom_field_data['int219_multi_sites']), 3)

        # Verify IDs are NOT in alphabetical-name order (precondition for the bug)
        alpha = Site.objects.get(name="INT219-Site-Alpha")
        beta = Site.objects.get(name="INT219-Site-Beta")
        gamma = Site.objects.get(name="INT219-Site-Gamma")
        name_order_ids = [alpha.pk, beta.pk, gamma.pk]
        numeric_order_ids = sorted(name_order_ids)
        self.assertNotEqual(
            name_order_ids, numeric_order_ids,
            "Test precondition failed: IDs happen to match name order. "
            "Pre-creating Gamma should have given it a lower ID than Alpha/Beta."
        )

        # Step 2: Re-diff with the exact same payload.
        # Before the fix, this produces a false "update" changeset because
        # cf.serialize() returns IDs in queryset name-order [alpha, beta, gamma]
        # but the transformer sorts resolved IDs numerically [gamma, alpha, beta].
        response = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])

        # The re-diff should produce NO changes — the data hasn't changed.
        self.assertEqual(
            changes, [],
            f"Expected no changes on re-diff, but got: {changes}"
        )

    def test_all_digit_name_object_cf_resolves_new_ref(self):
        """
        A CF whose name is all digits must resolve a new object ref, not 500.

        NetBox permits all-digit custom field names (validator ^[a-z0-9_]+$).
        The apply-time new_refs path for such a CF is "custom_fields.<digits>";
        the ref-resolution path helpers must not coerce that dict key to a list
        index (which would KeyError on the string-keyed custom_fields dict and,
        because the KeyError arg is an int, bypass the unresolved-ref handler
        and surface as a 500).
        """
        cf = CustomField.objects.create(
            name='12345',
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            required=False,
            related_object_type=ObjectType.objects.get_for_model(Site),
        )
        cf.object_types.set([ObjectType.objects.get_for_model(Device)])
        cf.save()

        payload = {
            "timestamp": 1,
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "DigitCF-Device",
                    "role": {"name": "DigitCF-Role"},
                    "site": {"name": "DigitCF-Site-Primary"},
                    "device_type": {
                        "model": "DigitCF-Model",
                        "manufacturer": {"name": "DigitCF-Manufacturer"},
                    },
                    # References a site that does not exist yet -> the CF ref is
                    # unresolved and must be resolved at apply via new_refs.
                    "custom_fields": {
                        "12345": {
                            "multiple_objects": [
                                {"site": {"name": "DigitCF-Site-New"}},
                            ],
                        },
                    },
                },
            },
        }

        # apply must succeed (diff_and_apply asserts 200 on both calls)
        self.diff_and_apply(payload)
        device = Device.objects.get(name="DigitCF-Device")
        new_site = Site.objects.get(name="DigitCF-Site-New")
        self.assertEqual(device.custom_field_data['12345'], [new_site.pk])

    def diff_and_apply(self, payload):
        """Diff and apply the payload."""
        response1 = self.client.post(
            self.diff_url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        diff = response1.json().get("change_set", {})
        response2 = self.client.post(
            self.apply_url, data=diff, format="json", **self.authorization_header
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        return (response1, response2)
