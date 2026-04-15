#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""

import logging
from collections import defaultdict
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, RackType, Site
from extras.models import CustomField
from extras.models.customfields import CustomFieldTypeChoices
from ipam.models import IPAddress
from tenancy.models import Tenant
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)

def _get_error(response, object_name, field):
    return response.json().get("errors", {}).get(object_name, {}).get(field, [])


class GenerateDiffTestCase(APITestCase):
    """GenerateDiff test cases."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

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
        self.introspect_patcher.stop()
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

    def test_merge_states_failed(self):
        """Test merge states failed."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vrf",
            "entity": {
                "vrf": {
                    "name": "Customer-A-VRF",
                    "rd": "65000:100",
                    "tenant": {"name": "Tenant 1"},
                    "enforce_unique": True,
                    "description": "Isolated routing domain for Customer A",
                    "comments": "Used for customer's private network services",
                    "tags": [
                    {
                        "name": "Tag 1"
                    },
                    {
                        "name": "Tag 2"
                    }
                    ],
                    "import_targets": [
                        {
                            "name": "65000:100",
                            "description": "Primary import route target"
                        },
                        {
                            "name": "65000:101",
                            "description": "Backup import route target"
                        }
                    ],
                    "export_targets": [
                        {
                            "name": "65000:100",
                            "description": "Primary export route target"
                        }
                    ]
                }
            }
        }

        response = self.send_request(payload, status.HTTP_400_BAD_REQUEST)
        logger.error(response.json())
        errs = _get_error(response, "ipam.vrf", "__all__")
        self.assertEqual(len(errs), 1)
        err = errs[0]
        self.assertTrue(err.startswith("Conflicting values for 'description' merging duplicate ipam.routetarget"))

    def test_vlangroup_error(self):
        """Test vlangroup error."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vlangroup",
            "entity": {
                "vlan_group": {
                    "name": "Data Center Core",
                    "slug": "dc-core",
                    "scope_site": {
                        "name": "Data Center West",
                        "slug": "dc-west",
                        "status": "active"
                    },
                    "description": "Core network VLANs for data center infrastructure",
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
        _ = self.send_request(payload)

    def test_generate_diff_dedupe_different_object_types(self):
        """Test generate diff dedupe different object types with same values."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "Cat8000V",
                    "role": {"name": "undefined"},
                    "site": {"name": "undefined"},
                    "serial": "9OBXJHNNU5V",
                    "status": "active",
                    "platform": {"name": "ios", "manufacturer": {"name": "undefined"}},
                    "device_type": {"model": "C8000V", "manufacturer": {"name": "undefined"}}
                },
            },
        }
        response = self.send_request(payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cs = response.json().get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 6)
        by_object_type = defaultdict(int)
        for change in changes:
            by_object_type[change.get("object_type")] += 1

        self.assertEqual(by_object_type["dcim.device"], 1)
        self.assertEqual(by_object_type["dcim.manufacturer"], 1)
        self.assertEqual(by_object_type["dcim.platform"], 1)
        self.assertEqual(by_object_type["dcim.devicetype"], 1)
        self.assertEqual(by_object_type["dcim.site"], 1)
        self.assertEqual(by_object_type["dcim.devicerole"], 1)

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response


class ExplicitFieldClearingTestCase(APITestCase):
    """Test that explicitly-provided empty values in updates produce changesets that clear fields."""

    def setUp(self):
        """Set up test fixtures with a site that has populated optional fields."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=self.diode_user,
        )
        self.introspect_patcher.start()

        self.site = Site.objects.create(
            name="Clear Fields Site",
            slug="clear-fields-site",
            facility="Bravo",
            description="A description to be cleared",
            physical_address="456 Real Ave",
            shipping_address="789 Ship Blvd",
            comments="Some comments",
        )

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response

    def test_explicit_empty_string_clears_description(self):
        """Setting description to '' on an existing site should produce an update with description=''."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "description": "",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)

        data = change.get("data", {})
        # description must be present and empty — this is the explicit clear
        self.assertIn("description", data)
        self.assertEqual(data["description"], "")

    def test_omitted_field_leaves_existing_value_untouched(self):
        """Not sending description at all should NOT include it in the changeset data."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "comments": "Updated comments",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "update")

        data = change.get("data", {})
        self.assertEqual(data.get("comments"), "Updated comments")
        # description was not in the payload, so it should not be in the changeset data —
        # only fields the user actually sent should appear
        self.assertNotIn("description", data)

    def test_noop_when_existing_and_incoming_both_empty(self):
        """If a field is already empty and the user sends empty, no change should be detected."""
        # First clear the description via DB
        self.site.description = ""
        self.site.save()

        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "description": "",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        # shallow_compare_dict sees no difference, so either noop or no changes
        if len(changes) > 0:
            site_changes = [c for c in changes if c["object_type"] == "dcim.site"]
            for change in site_changes:
                self.assertEqual(change.get("change_type"), "noop")

    def test_clear_multiple_fields_at_once(self):
        """Setting multiple optional fields to '' should produce an update with all of them cleared."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "description": "",
                    "comments": "",
                    "facility": "",
                    "physical_address": "",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)

        data = change.get("data", {})
        self.assertIn("description", data)
        self.assertEqual(data["description"], "")
        self.assertIn("comments", data)
        self.assertEqual(data["comments"], "")
        self.assertIn("facility", data)
        self.assertEqual(data["facility"], "")
        self.assertIn("physical_address", data)
        self.assertEqual(data["physical_address"], "")

    def test_set_nonempty_value_still_works(self):
        """Setting a field to a non-empty value still produces the correct update."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "description": "New description",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "update")

        data = change.get("data", {})
        self.assertEqual(data["description"], "New description")

    def test_clear_field_and_update_field_simultaneously(self):
        """Clearing one field while updating another should both appear in the changeset."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Clear Fields Site",
                    "slug": "clear-fields-site",
                    "description": "",
                    "comments": "Brand new comment",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "update")

        data = change.get("data", {})
        self.assertIn("description", data)
        self.assertEqual(data["description"], "")
        self.assertEqual(data["comments"], "Brand new comment")

    def test_create_with_empty_string_does_not_include_empty_fields(self):
        """On create, empty string fields should still be excluded (no noise)."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "Brand New Site",
                    "slug": "brand-new-site",
                    "description": "",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.get("change_type"), "create")

        data = change.get("data", {})
        self.assertEqual(data.get("name"), "Brand New Site")
        self.assertEqual(data.get("slug"), "brand-new-site")
        # For creates, empty string fields should be stripped (current behavior preserved)
        self.assertNotIn("description", data)


class NullNestedEntityClearingTestCase(APITestCase):
    """Test clearing nullable FK / generic FK fields by setting nested entities to null."""

    def setUp(self):
        """Set up test fixtures with objects that have nullable FK fields populated."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=self.diode_user,
        )
        self.introspect_patcher.start()

        # Create tenant + site with tenant assigned
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")
        self.site = Site.objects.create(
            name="FK Clear Site",
            slug="fk-clear-site",
            tenant=self.tenant,
            description="Site with tenant",
        )

        # Create device + interface + IP address with assigned_object
        self.manufacturer = Manufacturer.objects.create(name="FK Test Mfg", slug="fk-test-mfg")
        self.device_type = DeviceType.objects.create(
            manufacturer=self.manufacturer, model="FK Test Type", slug="fk-test-type"
        )
        self.role = DeviceRole.objects.create(name="FK Test Role", slug="fk-test-role", color="ff0000")
        self.device = Device.objects.create(
            name="FK Test Device",
            device_type=self.device_type,
            role=self.role,
            site=self.site,
        )
        self.interface = Interface.objects.create(
            device=self.device, name="eth0", type="virtual"
        )
        self.ip_address = IPAddress.objects.create(
            address="10.0.0.1/24",
            assigned_object=self.interface,
        )

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response

    def test_null_tenant_clears_fk_on_site(self):
        """Setting tenant to null on a site that has a tenant should produce an update clearing it."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "FK Clear Site",
                    "slug": "fk-clear-site",
                    "tenant": None,
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        site_changes = [c for c in changes if c["object_type"] == "dcim.site"]
        self.assertEqual(len(site_changes), 1)
        change = site_changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)

        data = change.get("data", {})
        self.assertIn("tenant", data)
        self.assertIsNone(data["tenant"])

    def test_omitted_tenant_leaves_fk_unchanged(self):
        """Not providing tenant at all should not include it in the changeset."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "FK Clear Site",
                    "slug": "fk-clear-site",
                    "description": "Updated description",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        site_changes = [c for c in changes if c["object_type"] == "dcim.site"]
        self.assertEqual(len(site_changes), 1)
        change = site_changes[0]
        self.assertEqual(change.get("change_type"), "update")

        data = change.get("data", {})
        # tenant was not in the payload — must not appear in changeset
        self.assertNotIn("tenant", data)

    def test_null_assigned_object_interface_clears_generic_fk(self):
        """Setting assigned_object_interface to null on an IP with an interface should clear it."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "10.0.0.1/24",
                    "assigned_object_interface": None,
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        ip_changes = [c for c in changes if c["object_type"] == "ipam.ipaddress"]
        self.assertEqual(len(ip_changes), 1)
        change = ip_changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.ip_address.id)

        data = change.get("data", {})
        self.assertIn("assigned_object_id", data)
        self.assertIsNone(data["assigned_object_id"])
        self.assertIn("assigned_object_type", data)
        self.assertIsNone(data["assigned_object_type"])

    def test_omitted_assigned_object_leaves_generic_fk_unchanged(self):
        """Not providing assigned_object at all should not include it in the changeset."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "10.0.0.1/24",
                    "description": "Updated description",
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        ip_changes = [c for c in changes if c["object_type"] == "ipam.ipaddress"]
        self.assertEqual(len(ip_changes), 1)
        change = ip_changes[0]

        data = change.get("data", {})
        # assigned_object was not in payload — must not appear in changeset
        self.assertNotIn("assigned_object_id", data)
        self.assertNotIn("assigned_object_type", data)

    def test_empty_dict_tenant_clears_fk_on_site(self):
        """Setting tenant to {} (protojson for empty message) should clear it, same as null."""
        payload = {
            "timestamp": 1,
            "object_type": "dcim.site",
            "entity": {
                "site": {
                    "name": "FK Clear Site",
                    "slug": "fk-clear-site",
                    "tenant": {},
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        site_changes = [c for c in changes if c["object_type"] == "dcim.site"]
        self.assertEqual(len(site_changes), 1)
        change = site_changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.site.id)

        data = change.get("data", {})
        self.assertIn("tenant", data)
        self.assertIsNone(data["tenant"])

    def test_empty_dict_assigned_object_clears_generic_fk(self):
        """Setting assigned_object_interface to {} should clear the GFK, same as null."""
        payload = {
            "timestamp": 1,
            "object_type": "ipam.ipaddress",
            "entity": {
                "ip_address": {
                    "address": "10.0.0.1/24",
                    "assigned_object_interface": {},
                },
            },
        }

        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        ip_changes = [c for c in changes if c["object_type"] == "ipam.ipaddress"]
        self.assertEqual(len(ip_changes), 1)
        change = ip_changes[0]
        self.assertEqual(change.get("change_type"), "update")
        self.assertEqual(change.get("object_id"), self.ip_address.id)

        data = change.get("data", {})
        self.assertIn("assigned_object_id", data)
        self.assertIsNone(data["assigned_object_id"])
        self.assertIn("assigned_object_type", data)
        self.assertIsNone(data["assigned_object_type"])


class PKBasedMatchingTestCase(APITestCase):
    """Test PK-based matching via metadata.source_match.netbox_id."""

    def setUp(self):
        """Set up test fixtures."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=self.diode_user,
        )
        self.introspect_patcher.start()

        self.site = Site.objects.create(name="PK Test Site", slug="pk-test-site")
        manufacturer = Manufacturer.objects.create(name="PK Test Manufacturer", slug="pk-test-manufacturer")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="PK Test Type", slug="pk-test-type"
        )
        self.role = DeviceRole.objects.create(name="PK Test Role", slug="pk-test-role", color="ff0000")
        self.device = Device.objects.create(
            name="PK Test Device",
            device_type=device_type,
            role=self.role,
            site=self.site,
            serial="ORIG-SERIAL",
        )
        self.interface = Interface.objects.create(
            device=self.device,
            name="eth0",
            type="virtual",
        )

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response

    def test_pk_match_noop(self):
        """PK match with identical data produces no changes."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "PK Test Device",
                    "serial": "ORIG-SERIAL",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                    "metadata": {
                        "source_match": {"netbox_id": self.device.pk},
                    },
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        # all changes should be noop since data matches
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertTrue(len(device_changes) <= 1)
        if device_changes:
            self.assertEqual(device_changes[0]["change_type"], "noop")

    def test_pk_match_update(self):
        """PK match with changed attribute produces update changeset."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "PK Test Device",
                    "serial": "NEW-SERIAL",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                    "metadata": {
                        "source_match": {"netbox_id": self.device.pk},
                    },
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertEqual(len(device_changes), 1)
        change = device_changes[0]
        self.assertEqual(change["change_type"], "update")
        self.assertEqual(change["object_id"], self.device.pk)
        self.assertEqual(change["data"]["serial"], "NEW-SERIAL")

    def test_pk_match_ignores_name(self):
        """PK match finds the device even when the name is different — produces update, not create."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "Completely Different Name",
                    "serial": "ORIG-SERIAL",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                    "metadata": {
                        "source_match": {"netbox_id": self.device.pk},
                    },
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertEqual(len(device_changes), 1)
        change = device_changes[0]
        self.assertEqual(change["change_type"], "update")
        self.assertEqual(change["object_id"], self.device.pk)
        # name change should be in the diff
        self.assertEqual(change["data"]["name"], "Completely Different Name")

    def test_pk_not_found_raises_error(self):
        """PK that doesn't exist returns an error, not a create."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "Ghost Device",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                    "metadata": {
                        "source_match": {"netbox_id": 999999},
                    },
                },
            },
        }
        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)
        errors = response.json().get("errors", {})
        self.assertTrue(len(errors) > 0)

    def test_no_metadata_uses_normal_matching(self):
        """Without metadata, normal constraint-based matching applies."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "PK Test Device",
                    "serial": "CHANGED-SERIAL",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertEqual(len(device_changes), 1)
        change = device_changes[0]
        # should still match by name+site and produce an update
        self.assertEqual(change["change_type"], "update")
        self.assertEqual(change["object_id"], self.device.pk)

    def test_pk_match_device_via_nested_interface(self):
        """Interface ingested with parent device carrying netbox_id — device matched by PK, interface by name."""
        payload = {
            "object_type": "dcim.interface",
            "entity": {
                "interface": {
                    "name": "eth0",
                    "type": "virtual",
                    "mtu": 9000,
                    "device": {
                        "name": "Irrelevant Name",
                        "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                        "role": {"name": "PK Test Role"},
                        "site": {"name": "PK Test Site"},
                        "metadata": {
                            "source_match": {"netbox_id": self.device.pk},
                        },
                    },
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        interface_changes = [c for c in changes if c["object_type"] == "dcim.interface"]
        # device should be matched by PK (name differs so it's an update)
        self.assertEqual(len(device_changes), 1)
        self.assertEqual(device_changes[0]["change_type"], "update")
        self.assertEqual(device_changes[0]["object_id"], self.device.pk)
        # interface should be matched by name within the PK-resolved device
        self.assertEqual(len(interface_changes), 1)
        self.assertEqual(interface_changes[0]["change_type"], "update")
        self.assertEqual(interface_changes[0]["object_id"], self.interface.pk)

    def test_invalid_netbox_id_falls_through(self):
        """Invalid (non-numeric) netbox_id is ignored with a warning, falls through to normal matching."""
        payload = {
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": "PK Test Device",
                    "serial": "CHANGED-FOR-INVALID-PK-TEST",
                    "device_type": {"model": "PK Test Type", "manufacturer": {"name": "PK Test Manufacturer"}},
                    "role": {"name": "PK Test Role"},
                    "site": {"name": "PK Test Site"},
                    "metadata": {
                        "source_match": {"netbox_id": "not-a-number"},
                    },
                },
            },
        }
        response = self.send_request(payload)
        cs = response.json().get("change_set", {})
        changes = cs.get("changes", [])
        device_changes = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertEqual(len(device_changes), 1)
        # should fall through to normal matching and find the device by name
        self.assertEqual(device_changes[0]["change_type"], "update")
        self.assertEqual(device_changes[0]["object_id"], self.device.pk)
        # warning should be present
        warnings = cs.get("warnings", {})
        self.assertIn("metadata", warnings.get("dcim.device", {}))
