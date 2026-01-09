#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""

import uuid
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Rack, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from ipam.models import ASN, RIR, IPAddress, Prefix
from netaddr import IPNetwork
from rest_framework import status
from utilities.testing import APITestCase
from virtualization.models import Cluster, ClusterType, VirtualMachine

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

User = get_user_model()

def _get_error(response, object_name, field):
    return response.json().get("errors", {}).get(object_name, {}).get(field, [])

class BaseApplyChangeSet(APITestCase):
    """Base ApplyChangeSet test case."""

    def setUp(self):
        """Set up test."""
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

        rir = RIR.objects.create(name="RFC 6996", is_private=True)
        self.asns = [ASN(asn=65000 + i, rir=rir) for i in range(8)]
        ASN.objects.bulk_create(self.asns)

        self.sites = (
            Site(
                id=10,
                name="Site 1",
                slug="site-1",
                facility="Alpha",
                description="First test site",
                physical_address="123 Fake St Lincoln NE 68588",
                shipping_address="123 Fake St Lincoln NE 68588",
                comments="Lorem ipsum etcetera",
            ),
            Site(
                id=20,
                name="Site 2",
                slug="site-2",
                facility="Bravo",
                description="Second test site",
                physical_address="725 Cyrus Valleys Suite 761 Douglasfort NE 57761",
                shipping_address="725 Cyrus Valleys Suite 761 Douglasfort NE 57761",
                comments="Lorem ipsum etcetera",
            ),
        )
        Site.objects.bulk_create(self.sites)

        self.racks = (
            Rack(name="Rack 1", site=self.sites[0]),
            Rack(name="Rack 2", site=self.sites[1]),
        )
        Rack.objects.bulk_create(self.racks)

        manufacturer = Manufacturer.objects.create(
            name="Manufacturer 1", slug="manufacturer-1"
        )

        self.device_types = (
            DeviceType(
                manufacturer=manufacturer, model="Device Type 1", slug="device-type-1"
            ),
            DeviceType(
                manufacturer=manufacturer,
                model="Device Type 2",
                slug="device-type-2",
                u_height=2,
            ),
        )
        DeviceType.objects.bulk_create(self.device_types)

        # bulk create is wierd due to mptt
        self.roles = [
            DeviceRole.objects.create(name="Device Role 1", slug="device-role-1", color="ff0000"),
            DeviceRole.objects.create(name="Device Role 2", slug="device-role-2", color="00ff00"),
        ]

        cluster_type = ClusterType.objects.create(
            name="Cluster Type 1", slug="cluster-type-1"
        )

        self.cluster_types = (cluster_type,)

        site_content_type = ContentType.objects.get_for_model(Site)

        self.clusters = (
            Cluster(name="Cluster 1", type=cluster_type, scope_type=site_content_type, scope_id=self.sites[0].id),
            Cluster(name="Cluster 2", type=cluster_type, scope_type=site_content_type, scope_id=self.sites[0].id),
        )
        Cluster.objects.bulk_create(self.clusters)

        self.devices = (
            Device(
                id=10,
                device_type=self.device_types[0],
                role=self.roles[0],
                name="Device 1",
                site=self.sites[0],
                rack=self.racks[0],
                cluster=self.clusters[0],
                local_context_data={"A": 1},
            ),
            Device(
                id=20,
                device_type=self.device_types[0],
                role=self.roles[0],
                name="Device 2",
                site=self.sites[0],
                rack=self.racks[0],
                cluster=self.clusters[0],
                local_context_data={"B": 2},
            ),
        )
        Device.objects.bulk_create(self.devices)

        self.interfaces = (
            Interface(name="Interface 1", device=self.devices[0], type="1000baset"),
            Interface(name="Interface 2", device=self.devices[0], type="1000baset"),
            Interface(name="Interface 3", device=self.devices[0], type="1000baset"),
            Interface(name="Interface 4", device=self.devices[0], type="1000baset"),
            Interface(name="Interface 5", device=self.devices[0], type="1000baset"),
        )
        Interface.objects.bulk_create(self.interfaces)

        self.ip_addresses = (
            IPAddress(
                address=IPNetwork("10.0.0.1/24"), assigned_object=self.interfaces[0]
            ),
            IPAddress(
                address=IPNetwork("192.0.2.1/24"), assigned_object=self.interfaces[1]
            ),
        )
        IPAddress.objects.bulk_create(self.ip_addresses)

        self.virtual_machines = (
            VirtualMachine(name="Virtual Machine 1"),
            VirtualMachine(name="Virtual Machine 2"),
        )
        VirtualMachine.objects.bulk_create(self.virtual_machines)

        self.url = "/netbox/api/plugins/diode/apply-change-set/"

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def send_request(self, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            self.url,
            data=payload,
            format="json",
            **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response


class ApplyChangeSetTestCase(BaseApplyChangeSet):
    """ApplyChangeSet test cases."""

    @staticmethod
    def get_change_id(payload, index):
        """Get change_id from payload."""
        return payload.get("changes")[index].get("change_id")

    def test_change_type_create_return_200(self):
        """Test create change_type with successful."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.interface",
                    "object_id": None,
                    "ref_id": "2",
                    "data": {
                        "name": "Interface 1",
                        "device": self.devices[1].pk,
                        "type": "other",
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "ipam.ipaddress",
                    "object_id": None,
                    "ref_id": "3",
                    "data": {
                        "address": "192.163.2.1/24",
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": self.interfaces[2].pk
                    },
                },
            ],
        }

        _ = self.send_request(payload)

    def test_change_type_update_return_200(self):
        """Test update change_type with successful."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 20,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
            ],
        }

        _ = self.client.post(
            self.url, payload, format="json", **self.authorization_header
        )

        site_updated = Site.objects.get(id=20)

        self.assertEqual(site_updated.name, "Site A")

    def test_change_type_create_with_error_return_400(self):
        """Test create change_type with wrong payload."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": 1,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)
        site_created = Site.objects.filter(name="Site A")

        self.assertIn(
            'Expected a list of items but got type "int".',
            _get_error(response, "dcim.site", "asns"),
        )
        self.assertFalse(site_created.exists())

    def test_change_type_update_with_error_return_400(self):
        """Test update change_type with wrong payload."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 20,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": 1,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        site_updated = Site.objects.get(id=20)
        self.assertIn(
            'Expected a list of items but got type "int".',
            _get_error(response, "dcim.site", "asns")
        )
        self.assertEqual(site_updated.name, "Site 2")

    def test_change_type_create_with_multiples_objects_return_200(self):
        """Test create change type with two objects."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site Z",
                        "slug": "site-z",
                        "facility": "Omega",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": None,
                    "ref_id": "2",
                    "data": {
                        "device_type": self.device_types[1].pk,
                        "role": self.roles[1].pk,
                        "name": "Test Device 500",
                        "site": self.sites[1].pk,
                        "rack": self.racks[1].pk,
                        "cluster": self.clusters[1].pk,
                    },
                },
            ],
        }

        _ = self.send_request(payload)

    def test_change_type_update_with_multiples_objects_return_200(self):
        """Test update change type with two objects."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 20,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": 10,
                    "data": {
                        "device_type": self.device_types[1].pk,
                        "role": self.roles[1].pk,
                        "name": "Test Device 3",
                        "site": self.sites[1].pk,
                        "rack": self.racks[1].pk,
                        "cluster": self.clusters[1].pk,
                    },
                },
            ],
        }

        _ = self.send_request(payload)

        site_updated = Site.objects.get(id=20)
        device_updated = Device.objects.get(id=10)

        self.assertEqual(site_updated.name, "Site A")
        self.assertEqual(device_updated.name, "Test Device 3")

    def test_change_type_create_and_update_with_error_in_one_object_return_400(self):
        """Test create and update change type with one object with error."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site Z",
                        "slug": "site-z",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": 10,
                    "data": {
                        "device_type": 3,
                        "role": self.roles[1].pk,
                        "name": "Test Device 4",
                        "site": self.sites[1].pk,
                        "rack": self.racks[1].pk,
                        "cluster": self.clusters[1].pk,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        site_created = Site.objects.filter(name="Site Z")
        device_created = Device.objects.filter(name="Test Device 4")

        self.assertIn(
            "Related object not found using the provided numeric ID: 3",
            _get_error(response, "dcim.device", "device_type"),
        )
        self.assertFalse(site_created.exists())
        self.assertFalse(device_created.exists())

    def test_multiples_create_type_error_in_two_objects_return_400(self):
        """Test create with error in two objects."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site Z",
                        "slug": "site-z",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": [self.asns[0].pk, self.asns[1].pk],
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": None,
                    "ref_id": "2",
                    "data": {
                        "device_type": 3,
                        "role": self.roles[1].pk,
                        "name": "Test Device 4",
                        "site": self.sites[1].pk,
                        "rack": self.racks[1].pk,
                        "cluster": self.clusters[1].pk,
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": None,
                    "ref_id": "3",
                    "data": {
                        "device_type": 100,
                        "role": 10,
                        "name": "Test Device 40",
                        "site": self.sites[1].pk,
                        "rack": self.racks[1].pk,
                        "cluster": self.clusters[1].pk,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        site_created = Site.objects.filter(name="Site Z")
        device_created = Device.objects.filter(name="Test Device 4")

        self.assertIn(
            "Related object not found using the provided numeric ID: 3",
            _get_error(response, "dcim.device", "device_type"),
        )

        self.assertFalse(site_created.exists())
        self.assertFalse(device_created.exists())

    def test_change_type_update_with_object_id_not_exist_return_400(self):
        """Test update object with nonexistent object_id."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 30,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": 1,
                    },
                },
            ],
        }

        response = self.client.post(
            self.url, payload, format="json", **self.authorization_header
        )

        site_updated = Site.objects.get(id=20)

        self.assertIn(
            "dcim.site with id 30 does not exist",
            _get_error(response, "dcim.site", "object_id"),
        )
        self.assertEqual(site_updated.name, "Site 2")

    def test_change_set_id_field_not_provided_return_400(self):
        """Test update object with change_set_id incorrect."""
        payload = {
            "id": None,
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 20,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": 1,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        self.assertIsNone(response.json().get("errors", {}).get("change_id", None))
        self.assertIn(
            "Change set ID is required",
            _get_error(response, "changeset", "id"),
        )

    def test_change_type_field_not_provided_return_400(
        self,
    ):
        """Test update object with change_type incorrect."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": 20,
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                        "asns": 1,
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        self.assertIn(
            "Unsupported change type ''",
            _get_error(response, "dcim.site", "change_type"),
        )

    def test_change_set_id_field_and_change_set_not_provided_return_400(self):
        """Test update object with change_set_id and change_set incorrect."""
        payload = {
            "id": "",
            "changes": [],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        self.assertIn(
            "Change set ID is required",
            _get_error(response, "changeset", "id"),
        )

    def test_change_type_and_object_type_provided_return_400(
        self,
    ):
        """Test change_type and object_type incorrect."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": None,
                    "object_version": None,
                    "object_type": "",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Site A",
                        "slug": "site-a",
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "2",
                    "data": {
                        "name": "Site Z",
                        "slug": "site-z",
                        "facility": "Betha",
                        "description": "",
                        "physical_address": "123 Fake St Lincoln NE 68588",
                        "shipping_address": "123 Fake St Lincoln NE 68588",
                        "comments": "Lorem ipsum etcetera",
                    },
                },
            ],
        }

        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

        self.assertIn(
            "Unsupported change type 'None'",
            _get_error(response, "__all__", "change_type"),
        )

    def test_create_ip_address_return_200(self):
        """Test create ip_address with successful."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "ipam.ipaddress",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "address": "192.161.3.1/24",
                        "assigned_object_id": self.interfaces[3].pk,
                        "assigned_object_type": "dcim.interface",
                    },
                },
            ],
        }
        _ = self.send_request(payload)

    def test_add_primary_ip_address_to_device(self):
        """Add primary ip address to device."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": self.devices[0].pk,
                    "data": {
                        "name": self.devices[0].name,
                        "site": {"name": self.sites[0].name},
                        "primary_ip4": self.ip_addresses[0].pk
                    },
                },
            ],
        }

        _ = self.send_request(payload)
        device_updated = Device.objects.get(id=10)

        self.assertEqual(device_updated.name, self.devices[0].name)
        self.assertEqual(device_updated.primary_ip4, self.ip_addresses[0])

    def test_create_prefix_with_site_stored_as_scope(self):
        """Test create prefix with site stored as scope."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "ipam.prefix",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "prefix": "192.168.0.0/24",
                        "scope_id": self.sites[0].pk,
                        "scope_type": "dcim.site",
                    },
                },
            ],
        }
        _ = self.send_request(payload)
        self.assertEqual(Prefix.objects.get(prefix="192.168.0.0/24").scope, self.sites[0])

    def test_create_prefix_with_unknown_site_fails(self):
        """Test create prefix with unknown site fails."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "ipam.prefix",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "prefix": "192.168.0.0/24",
                        "scope_id": 99,
                        "scope_type": "dcim.site",
                    },
                },
            ],
        }
        response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)
        print(response.json())
        self.assertIn(
            'Related object not found using the provided value: 99.',
            _get_error(response, "ipam.prefix", "scope_id"),
        )
        self.assertFalse(Prefix.objects.filter(prefix="192.168.0.0/24").exists())

    def test_create_virtualization_cluster_with_site_stored_as_scope(self):
        """Test create cluster with site stored as scope."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "virtualization.cluster",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "Cluster 3",
                        "type": {
                            "name": self.cluster_types[0].name,
                        },
                        "scope_id": self.sites[0].pk,
                        "scope_type": "dcim.site",
                    },
                },
            ],
        }
        _ = self.send_request(payload)
        self.assertEqual(Cluster.objects.get(name="Cluster 3").scope, self.sites[0])

    def test_create_virtualmachine_with_cluster_site_stored_as_scope(self):
        """Test create virtualmachine with cluster site stored as scope."""
        payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "update",
                    "object_version": None,
                    "object_type": "virtualization.cluster",
                    "object_id": self.clusters[0].pk,
                    "data": {
                        "scope_id": self.sites[0].pk,
                        "scope_type": "dcim.site",
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "virtualization.virtualmachine",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": "VM foobar",
                        "site": self.sites[0].pk,
                        "cluster": self.clusters[0].pk
                    },
                },
            ],
        }
        _ = self.send_request(payload)
        self.assertEqual(VirtualMachine.objects.get(name="VM foobar", site_id=self.sites[0].id).cluster.scope, self.sites[0])

    def test_apply_two_changes_that_create_the_same_object_return_200(self):
        """Test apply two changes that create the same object return 200."""
        site_name = uuid.uuid4()
        payload1 = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": f"Site {site_name}",
                        "slug": f"site-{site_name}",
                        "comments": "comment 1",
                    },
                },
            ],
        }
        _ = self.send_request(payload1)

        payload2 = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": f"Site {site_name}",
                        "slug": f"site-{site_name}",
                        "comments": "comment 1",
                    },
                },
            ],
        }
        _ = self.send_request(payload2)

    def test_module_bay_from_template_no_duplicate(self):
        """Test that module bays created from templates are reused and updated, not duplicated."""
        from dcim.models import Module, ModuleBay, ModuleBayTemplate, ModuleType

        # Create a device type with a module bay template
        device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.first(),
            model="Device with Module Bay Template",
            slug="device-with-module-bay-template",
        )

        # Create module bay template
        ModuleBayTemplate.objects.create(
            device_type=device_type,
            name="Tray",
        )

        # Create module type
        module_type = ModuleType.objects.create(
            manufacturer=Manufacturer.objects.first(),
            model="Test Module Type",
        )

        # Step 1: Create a device - this will auto-create module bay "Tray" from template
        device_payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": None,
                    "ref_id": "device-1",
                    "data": {
                        "name": "Test Device with Module Bay",
                        "device_type": device_type.id,
                        "role": self.roles[0].id,
                        "site": self.sites[0].id,
                    },
                },
            ],
        }
        self.send_request(device_payload)

        # Verify device was created
        device = Device.objects.get(name="Test Device with Module Bay")

        # Verify module bay was auto-created from template
        module_bays_before = ModuleBay.objects.filter(device=device, name="Tray")
        self.assertEqual(module_bays_before.count(), 1)
        module_bay = module_bays_before.first()
        self.assertIsNone(module_bay.module)  # No module installed yet
        self.assertEqual(module_bay.description, "")  # Template has no description

        # Step 2: Create a module with the module bay - should reuse existing bay and update it
        module_payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.modulebay",
                    "object_id": None,
                    "ref_id": "modulebay-1",
                    "data": {
                        "name": "Tray",
                        "device": device.id,
                        "description": "Ingested module bay",
                    },
                },
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.module",
                    "object_id": None,
                    "ref_id": "module-1",
                    "new_refs": ["module_bay"],
                    "data": {
                        "device": device.id,
                        "module_bay": "modulebay-1",
                        "module_type": module_type.id,
                        "description": "Ingested module",
                    },
                },
            ],
        }
        self.send_request(module_payload)

        # Verify NO duplicate module bays were created
        module_bays_after = ModuleBay.objects.filter(device=device, name="Tray")
        self.assertEqual(
            module_bays_after.count(),
            1,
            "Module bay should be reused, not duplicated"
        )

        # Verify the module bay was updated with the description
        module_bay.refresh_from_db()
        self.assertEqual(
            module_bay.description,
            "Ingested module bay",
            "Module bay should be updated with ingested data"
        )

        # Verify module was created successfully
        modules = Module.objects.filter(device=device, module_bay=module_bay)
        self.assertEqual(modules.count(), 1)
        module = modules.first()
        self.assertEqual(module.module_type, module_type)
        self.assertEqual(module.description, "Ingested module")

    def test_interface_from_template_no_duplicate(self):
        """Test that interfaces created from templates are reused and updated, not duplicated."""
        from dcim.models import InterfaceTemplate

        # Create a device type with an interface template
        device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.first(),
            model="Device with Interface Template",
            slug="device-with-interface-template",
        )

        # Create interface template
        InterfaceTemplate.objects.create(
            device_type=device_type,
            name="eth0",
            type="1000base-t",
        )

        # Step 1: Create a device - this will auto-create interface "eth0" from template
        device_payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.device",
                    "object_id": None,
                    "ref_id": "device-1",
                    "data": {
                        "name": "Test Device with Interface",
                        "device_type": device_type.id,
                        "role": self.roles[0].id,
                        "site": self.sites[0].id,
                    },
                },
            ],
        }
        self.send_request(device_payload)

        # Verify device was created
        device = Device.objects.get(name="Test Device with Interface")

        # Verify interface was auto-created from template
        interfaces_before = Interface.objects.filter(device=device, name="eth0")
        self.assertEqual(interfaces_before.count(), 1)
        interface = interfaces_before.first()
        self.assertEqual(interface.description, "")  # Template has no description

        # Step 2: Try to create the same interface with additional data - should reuse and update
        interface_payload = {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.interface",
                    "object_id": None,
                    "ref_id": "interface-1",
                    "data": {
                        "name": "eth0",
                        "device": device.id,
                        "type": "1000base-t",
                        "description": "Ingested interface",
                        "enabled": True,
                    },
                },
            ],
        }
        self.send_request(interface_payload)

        # Verify NO duplicate interfaces were created
        interfaces_after = Interface.objects.filter(device=device, name="eth0")
        self.assertEqual(
            interfaces_after.count(),
            1,
            "Interface should be reused, not duplicated"
        )

        # Verify the interface was updated with the description
        interface.refresh_from_db()
        self.assertEqual(
            interface.description,
            "Ingested interface",
            "Interface should be updated with ingested data"
        )
        self.assertTrue(interface.enabled)
