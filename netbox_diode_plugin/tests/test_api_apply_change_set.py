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

        self.roles = (
            DeviceRole(name="Device Role 1", slug="device-role-1", color="ff0000"),
            DeviceRole(name="Device Role 2", slug="device-role-2", color="00ff00"),
        )
        DeviceRole.objects.bulk_create(self.roles)

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
        # self.assertEqual(
        #     response.json().get("errors")[0].get("change_type"),
        #     "This field may not be null.",
        # )
        # self.assertEqual(
        #     response.json().get("errors")[0].get("object_type"),
        #     "This field may not be blank.",
        # )

        # # Second item of change_set
        # self.assertEqual(
        #     response.json().get("errors")[1].get("change_id"),
        #     self.get_change_id(payload, 1),
        # )
        # self.assertEqual(
        #     response.json().get("errors")[1].get("change_type"),
        #     "This field may not be blank.",
        # )

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

    # def test_create_ip_address_return_400(self):
    #     """Test create ip_address with missing interface name."""
    #     payload = {
    #         "id": str(uuid.uuid4()),
    #         "change_set": [
    #             {
    #                 "change_id": str(uuid.uuid4()),
    #                 "change_type": "create",
    #                 "object_version": None,
    #                 "object_type": "ipam.ipaddress",
    #                 "object_id": None,
    #                 "data": {
    #                     "address": "192.161.3.1/24",
    #                     "assigned_object": {
    #                         "interface": {
    #                             # Forcing to miss the name of the interface
    #                             "device": {
    #                                 "name": self.devices[0].name,
    #                                 "site": {"name": self.sites[0].name},
    #                             },
    #                         },
    #                     },
    #                 },
    #             },
    #         ],
    #     }
    #     response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

    #     self.assertIn(
    #         "not sufficient to retrieve interface",
    #         response.json().get("errors")[0].get("assigned_object"),
    #     )

    # def test_create_ip_address_not_exist_interface_return_400(self):
    #     """Test create ip_address with not valid interface."""
    #     payload = {
    #         "id": str(uuid.uuid4()),
    #         "changes": [
    #             {
    #                 "change_id": str(uuid.uuid4()),
    #                 "change_type": "create",
    #                 "object_version": None,
    #                 "object_type": "ipam.ipaddress",
    #                 "object_id": None,
    #                 "data": {
    #                     "address": "192.161.3.1/24",
    #                     "assigned_object": {
    #                         "interface": {
    #                             "name": "not_exist",
    #                             "device": {
    #                                 "name": self.devices[0].name,
    #                                 "site": {"name": self.sites[0].name},
    #                             },
    #                         },
    #                     },
    #                 },
    #             },
    #         ],
    #     }
    #     response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

    #     self.assertIn(
    #         "does not exist",
    #         response.json().get("errors")[0].get("assigned_object"),
    #     )

    # def test_create_ip_address_missing_device_interface_return_400(self):
    #     """Test create ip_address with missing device interface name."""
    #     payload = {
    #         "id": str(uuid.uuid4()),
    #         "changes": [
    #             {
    #                 "change_id": str(uuid.uuid4()),
    #                 "change_type": "create",
    #                 "object_version": None,
    #                 "object_type": "ipam.ipaddress",
    #                 "object_id": None,
    #                 "ref_id": "1",
    #                 "data": {
    #                     "address": "192.161.3.1/24",
    #                     "assigned_object": {
    #                         "interface": {
    #                             "name": "not_exist",
    #                             "device": {
    #                                 "site": {"name": self.sites[0].name},
    #                             },
    #                         },
    #                     },
    #                 },
    #             },
    #         ],
    #     }
    #     response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

    #     self.assertIn(
    #         "Interface device needs to have either id or name provided",
    #         response.json().get("errors", {}) # .get("assigned_object"),
    #     )

    # def test_create_ip_address_missing_interface_device_site_return_400(self):
    #     """Test create ip_address with missing interface device site name."""
    #     payload = {
    #         "id": str(uuid.uuid4()),
    #         "changes": [
    #             {
    #                 "change_id": str(uuid.uuid4()),
    #                 "change_type": "create",
    #                 "object_version": None,
    #                 "object_type": "ipam.ipaddress",
    #                 "object_id": None,
    #                 "ref_id": "1",
    #                 "data": {
    #                     "address": "192.161.3.1/24",
    #                     "assigned_object": {
    #                         "interface": {
    #                             "name": "not_exist",
    #                             "device": {
    #                                 "name": self.devices[0].name,
    #                                 "site": {"facility": "Betha"},
    #                             },
    #                         },
    #                     },
    #                 },
    #             },
    #         ],
    #     }
    #     response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

    #     self.assertIn(
    #         "Interface device site needs to have either id or name provided",
    #         response.json().get("errors")[0].get("assigned_object"),
    #     )

    # def test_primary_ip_address_not_found_return_400(self):
    #     """Test update primary ip address with site name."""
    #     payload = {
    #         "id": str(uuid.uuid4()),
    #         "changes": [
    #             {
    #                 "change_id": str(uuid.uuid4()),
    #                 "change_type": "update",
    #                 "object_version": None,
    #                 "object_type": "dcim.device",
    #                 "data": {
    #                     "name": self.devices[0].name,
    #                     "site": {"name": self.sites[0].name},
    #                     "primary_ip6": {
    #                         "address": "2001:DB8:0000:0000:244:17FF:FEB6:D37D/64",
    #                     },
    #                 },
    #             },
    #         ],
    #     }
    #     response = self.send_request(payload, status_code=status.HTTP_400_BAD_REQUEST)

    #     self.assertEqual(response.json()[0], "primary IP not found")

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
        self.assertIn(
            'Please select a site.',
            _get_error(response, "ipam.prefix", "scope"),
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
