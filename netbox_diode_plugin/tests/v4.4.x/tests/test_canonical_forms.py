#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Producer values are rewritten into the form NetBox stores before matching and diffing."""

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Interface, MACAddress, Manufacturer, Site
from django.test import TestCase
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.canonical import canonicalize_entity
from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.supported_models import extract_supported_models
from netbox_diode_plugin.api.transformer import transform_proto_json
from netbox_diode_plugin.plugin_config import get_diode_user


class CanonicalizeEntityTests(TestCase):
    """canonicalize_entity mirrors NetBox's write-time rewrites, nothing more."""

    def _canon(self, object_type, **fields):
        canonicalize_entity(fields, object_type)
        return fields

    # --- EUI fields: NetBox stores the uppercase colon form -------------------

    def test_mac_address_variants_collapse_to_netbox_form(self):
        """Every input form NetBox accepts lands on the one form it stores."""
        for sent in (
            "9c:1d:36:fc:96:2f",
            "9C:1D:36:FC:96:2F",
            "9c-1d-36-fc-96-2f",
            "9c1d.36fc.962f",
            "9C1D36FC962F",
            " 9c:1d:36:fc:96:2f",
        ):
            with self.subTest(sent=sent):
                out = self._canon("dcim.macaddress", mac_address=sent)
                self.assertEqual(out["mac_address"], "9C:1D:36:FC:96:2F")

    def test_wwn_variants_collapse_to_netbox_form(self):
        """WWN goes through the same EUI-64 path NetBox uses on write."""
        for sent in ("50:01:43:80:00:00:00:00", "50:01:43:80:00:00:00:00".lower(), "5001438000000000"):
            with self.subTest(sent=sent):
                out = self._canon("dcim.interface", wwn=sent)
                self.assertEqual(out["wwn"], "50:01:43:80:00:00:00:00")

    def test_invalid_mac_is_left_for_the_serializer_to_reject(self):
        """A value NetBox cannot parse is passed through untouched so apply reports NetBox's own error."""
        out = self._canon("dcim.macaddress", mac_address="not-a-mac")
        self.assertEqual(out["mac_address"], "not-a-mac")

    # --- whitespace: DRF CharField trims on write ----------------------------

    def test_text_fields_are_stripped_like_drf_does(self):
        """Leading/trailing whitespace never reaches the DB, so it must not reach the diff either."""
        out = self._canon("dcim.device", name="  sw1 ", serial=" ABC123\n", description="\tcore ")
        self.assertEqual(out["name"], "sw1")
        self.assertEqual(out["serial"], "ABC123")
        self.assertEqual(out["description"], "core")

    def test_choice_fields_are_not_stripped(self):
        """DRF maps a model CharField with choices to ChoiceField, which does not trim; mirror that."""
        out = self._canon("dcim.device", status=" active ")
        self.assertEqual(out["status"], " active ")

    def test_save_time_rewrites_are_not_mirrored(self):
        """IPAddress.save() lowercases dns_name, but that is NetBox policy, not a field rule; only strip applies."""
        out = self._canon("ipam.ipaddress", dns_name=" Host.Example.COM ")
        self.assertEqual(out["dns_name"], "Host.Example.COM")

    # --- guards ----------------------------------------------------------------

    def test_non_string_values_and_references_are_untouched(self):
        """Only scalar strings are candidates; ints, None, refs, dicts and lists pass through."""
        ref = UnresolvedReference(object_type="dcim.device", uuid=str(uuid4()))
        out = self._canon(
            "dcim.interface",
            wwn=None,
            mtu=1500,
            device=ref,
            tagged_vlans=[1, 2],
            primary_mac_address={"mac_address": "9c:1d:36:fc:96:2f"},
        )
        self.assertIsNone(out["wwn"])
        self.assertEqual(out["mtu"], 1500)
        self.assertIs(out["device"], ref)
        self.assertEqual(out["tagged_vlans"], [1, 2])
        # nested payloads are canonicalized when their own node is transformed
        self.assertEqual(out["primary_mac_address"], {"mac_address": "9c:1d:36:fc:96:2f"})

    def test_unknown_object_type_is_a_noop(self):
        """A type with no model behind it must not raise."""
        out = self._canon("diode.no_such_type", mac_address="9c:1d:36:fc:96:2f")
        self.assertEqual(out["mac_address"], "9c:1d:36:fc:96:2f")


class CanonicalFormsInTransformerTests(TestCase):
    """The rewrite happens at transform time, so nested nodes and fingerprints see canonical values."""

    def test_nested_primary_mac_address_node_is_canonical(self):
        """The flat and nested MAC forms both end up as one canonical dcim.macaddress node."""
        supported = extract_supported_models()
        payload = {
            "name": "GigabitEthernet1",
            "type": "1000base-t",
            "wwn": "5001438000000000",
            "device": {"name": " sw1 ", "site": {"name": "s1"}},
            "primary_mac_address": {"mac_address": "9c1d.36fc.962f"},
        }
        entities = transform_proto_json(payload, "dcim.interface", supported)
        # primary_mac_address is circular (the MAC points back at the interface), so
        # the interface is split into its main node and a deferred one that carries
        # only the MAC link; the main node is the one that carries the name.
        def main(object_type):
            return next(e for e in entities if e["_object_type"] == object_type and "name" in e)

        (mac,) = [e for e in entities if e["_object_type"] == "dcim.macaddress"]
        self.assertEqual(mac["mac_address"], "9C:1D:36:FC:96:2F")
        self.assertEqual(main("dcim.interface")["wwn"], "50:01:43:80:00:00:00:00")
        self.assertEqual(main("dcim.device")["name"], "sw1")


class CanonicalFormsDiffTests(APITestCase):
    """Re-sending a converged object in a non-canonical spelling plans nothing (diode#373)."""

    def setUp(self):
        """Authenticate as the diode user and seed a converged device graph."""
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user
        )
        self.introspect_patcher.start()
        self.addCleanup(self.introspect_patcher.stop)

        suffix = str(uuid4())
        self.site = Site.objects.create(name=f"Site {suffix}", slug=f"site-{suffix}")
        manufacturer = Manufacturer.objects.create(name=f"Manufacturer {suffix}", slug=f"manufacturer-{suffix}")
        self.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model=f"Device Type {suffix}", slug=f"device-type-{suffix}"
        )
        self.role = DeviceRole.objects.create(name=f"Role {suffix}", slug=f"role-{suffix}", color="ff0000")
        self.device = Device.objects.create(
            name=f"Device {suffix}", device_type=self.device_type, role=self.role, site=self.site,
            serial="ABC123",
        )
        self.interface = Interface.objects.create(
            device=self.device, name="GigabitEthernet1", type="1000base-t", wwn="50:01:43:80:00:00:00:00"
        )
        self.mac = MACAddress.objects.create(mac_address="9C:1D:36:FC:96:2F", assigned_object=self.interface)
        self.interface.primary_mac_address = self.mac
        self.interface.save()

    def _changes(self, object_type, entity_key, entity):
        response = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": object_type, "entity": {entity_key: entity}},
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response.json().get("change_set", {}).get("changes", [])

    def _device_ref(self):
        return {
            "name": self.device.name,
            "site": {"name": self.site.name},
            "role": {"name": self.role.name},
            "device_type": {
                "manufacturer": {"name": self.device_type.manufacturer.name},
                "model": self.device_type.model,
            },
        }

    def test_lowercase_primary_mac_and_wwn_replan_nothing(self):
        """The exact diode#373 scenario: same MAC in lowercase must not plan an UPDATE."""
        changes = self._changes("dcim.interface", "interface", {
            "name": self.interface.name,
            "type": self.interface.type,
            "device": self._device_ref(),
            "wwn": "50:01:43:80:00:00:00:00".lower(),
            "primary_mac_address": {"mac_address": "9c:1d:36:fc:96:2f"},
        })
        self.assertEqual(changes, [])

    def test_dotted_mac_replans_nothing(self):
        """Cisco dotted spelling of the same MAC is the same object."""
        changes = self._changes("dcim.interface", "interface", {
            "name": self.interface.name,
            "type": self.interface.type,
            "device": self._device_ref(),
            "primary_mac_address": {"mac_address": "9c1d.36fc.962f"},
        })
        self.assertEqual(changes, [])

    def test_padded_device_name_matches_the_stored_device(self):
        """Whitespace DRF would strip on write must not turn a re-ingest into a CREATE."""
        ref = self._device_ref()
        ref["name"] = f"  {self.device.name} "
        ref["serial"] = " ABC123 "
        changes = self._changes("dcim.device", "device", ref)
        self.assertEqual(changes, [])

    def test_a_real_mac_change_is_still_planned(self):
        """Canonicalization must not mask an actual change."""
        changes = self._changes("dcim.interface", "interface", {
            "name": self.interface.name,
            "type": self.interface.type,
            "device": self._device_ref(),
            "primary_mac_address": {"mac_address": "9c:1d:36:fc:96:30"},
        })
        # a new MAC row plus the interface re-pointing primary_mac_address at it
        planned = {c["object_type"]: c for c in changes if c["change_type"] != "noop"}
        self.assertEqual(set(planned), {"dcim.macaddress", "dcim.interface"})
        self.assertEqual(planned["dcim.macaddress"]["change_type"], "create")
        self.assertEqual(planned["dcim.macaddress"]["data"]["mac_address"], "9C:1D:36:FC:96:30")

    def test_invalid_mac_reports_netbox_error(self):
        """An unparseable MAC still fails with NetBox's own message, not a transformer error."""
        response = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.macaddress", "entity": {"mac_address": {
                "mac_address": "not-a-mac",
            }}},
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("mac_address", str(response.content))
