#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Diode NetBox Plugin - unique custom field identity guard.

A unique custom field names one row. The payload can still contradict that
row on an optional unique identifier such as an asset tag. The lookup then
raises ConflictingObjectMatch at plan time. It does not plan an UPDATE that
rewrites the row, and it does not plan a CREATE that NetBox rejects at apply.
"""

from types import SimpleNamespace
from unittest import mock

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.core.cache import cache as django_cache
from django.test import TestCase
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomField
from ipam.models import VRF
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import ChangeSetException
from netbox_diode_plugin.api.matcher import (
    ConflictingObjectMatch,
    _find_obj_cache_key,
    _optional_unique_value_fields,
    enter_request_obj_cache,
    exit_request_obj_cache,
    find_existing_object,
)
from netbox_diode_plugin.plugin_config import get_diode_user


def _unique_field(name, *models):
    cf = CustomField.objects.create(name=name, type=CustomFieldTypeChoices.TYPE_TEXT, required=False, unique=True)
    cf.object_types.set([ObjectType.objects.get_for_model(m) for m in models])
    return cf


def _device_fixtures():
    site = Site.objects.create(name="guard-site", slug="guard-site")
    mfr = Manufacturer.objects.create(name="guard-mfr", slug="guard-mfr")
    dt = DeviceType.objects.create(manufacturer=mfr, model="guard-dt", slug="guard-dt")
    role = DeviceRole.objects.create(name="guard-role", slug="guard-role")
    return site, dt, role


class OptionalUniqueValueFieldsTests(TestCase):
    """The guard reads its field list from the model, not from a hard-coded name."""

    def test_asset_tag_and_rd_qualify(self):
        """Optional, unique, single-field values identify the real thing behind a row."""
        self.assertIn("asset_tag", _optional_unique_value_fields(Device))
        self.assertIn("rd", _optional_unique_value_fields(VRF))

    def test_required_labels_and_relations_do_not(self):
        """A rename or a primary IP change must not look like a different object."""
        self.assertEqual(_optional_unique_value_fields(Site), ())
        self.assertNotIn("name", _optional_unique_value_fields(Device))
        self.assertNotIn("primary_ip4", _optional_unique_value_fields(Device))


class DeviceGuardTests(TestCase):
    """Device member-a holds the value STACK-001 and the asset tag TAG-A."""

    @classmethod
    def setUpTestData(cls):
        """One device with a value and a tag, one with a value and no tag."""
        cls.site, dt, role = _device_fixtures()
        _unique_field("stack_id", Device)
        cls.tagged = Device.objects.create(
            name="member-a", site=cls.site, device_type=dt, role=role, asset_tag="TAG-A", custom_field_data={"stack_id": "STACK-001"}
        )
        cls.tagless = Device.objects.create(
            name="member-c", site=cls.site, device_type=dt, role=role, custom_field_data={"stack_id": "STACK-002"}
        )

    def setUp(self):
        """Keep lookup answers from an earlier test out of this one."""
        django_cache.clear()

    def _payload(self, stack_id, **fields):
        return {"site": self.site.pk, "custom_fields": {"stack_id": stack_id}, **fields}

    def test_a_different_asset_tag_is_refused(self):
        """The motivating case: the same value, a different tag and a different name."""
        payload = self._payload("STACK-001", name="member-b", asset_tag="TAG-B")
        with self.assertRaises(ConflictingObjectMatch) as caught:
            find_existing_object(payload, "dcim.device")
        message = caught.exception.message
        self.assertIn("stack_id 'STACK-001'", message)
        self.assertIn(f"member-a (id {self.tagged.pk})", message)
        self.assertIn("asset_tag 'TAG-A'", message)
        self.assertIn("asset_tag 'TAG-B'", message)
        self.assertEqual(caught.exception.errors, {"dcim.device": {"asset_tag": [message]}})

    def test_an_empty_asset_tag_is_refused_too(self):
        """A payload that asserts no tag contradicts a row that has one."""
        for empty in ("", None):
            payload = self._payload("STACK-001", name="member-b", asset_tag=empty)
            with self.subTest(asset_tag=empty), self.assertRaises(ConflictingObjectMatch) as caught:
                find_existing_object(payload, "dcim.device")
            self.assertIn("carries no asset_tag", str(caught.exception))

    def test_a_rename_without_a_tag_binds(self):
        """A key-absent field asserts nothing, so identity by external id survives a rename."""
        payload = self._payload("STACK-001", name="member-b")
        self.assertEqual(find_existing_object(payload, "dcim.device"), self.tagged)

    def test_the_same_tag_binds(self):
        """No contradiction when the tags agree."""
        payload = self._payload("STACK-001", name="member-b", asset_tag="TAG-A")
        self.assertEqual(find_existing_object(payload, "dcim.device"), self.tagged)

    def test_a_tagless_row_accepts_a_new_tag(self):
        """The payload adds the identifier and rewrites nothing."""
        payload = self._payload("STACK-002", name="member-c", asset_tag="TAG-C")
        self.assertEqual(find_existing_object(payload, "dcim.device"), self.tagless)

    def test_the_error_is_a_changeset_exception_and_not_a_value_error(self):
        """Three call sites swallow ValueError and TypeError into "no match"."""
        self.assertTrue(issubclass(ConflictingObjectMatch, ChangeSetException))
        self.assertFalse(issubclass(ConflictingObjectMatch, ValueError))
        self.assertFalse(issubclass(ConflictingObjectMatch, TypeError))


class VRFGuardTests(TestCase):
    """The rule is generic: rd on VRF is an optional unique identifier too."""

    @classmethod
    def setUpTestData(cls):
        """One VRF with a value and a route distinguisher."""
        _unique_field("ext_id", VRF)
        cls.vrf = VRF.objects.create(name="blue", rd="65000:1", custom_field_data={"ext_id": "VRF-1"})

    def setUp(self):
        """Keep lookup answers from an earlier test out of this one."""
        django_cache.clear()

    def test_a_different_rd_is_refused(self):
        """A route distinguisher mismatch between two sources points to a different VRF."""
        payload = {"name": "blue-renamed", "rd": "65000:2", "custom_fields": {"ext_id": "VRF-1"}}
        with self.assertRaises(ConflictingObjectMatch) as caught:
            find_existing_object(payload, "ipam.vrf")
        self.assertEqual(caught.exception.field, "rd")
        self.assertIn("rd '65000:1'", str(caught.exception))

    def test_a_rename_binds(self):
        """The name is a label; the external id still names the row."""
        payload = {"name": "blue-renamed", "custom_fields": {"ext_id": "VRF-1"}}
        self.assertEqual(find_existing_object(payload, "ipam.vrf"), self.vrf)


class RequiredLabelTests(TestCase):
    """A required unique label such as a site name does not identify the thing."""

    @classmethod
    def setUpTestData(cls):
        """One site with a value."""
        _unique_field("ext_id", Site)
        cls.site = Site.objects.create(name="Site A", slug="site-a", custom_field_data={"ext_id": "S-1"})

    def setUp(self):
        """Keep lookup answers from an earlier test out of this one."""
        django_cache.clear()

    def test_a_rename_by_external_id_binds(self):
        """The guard must not refuse what the identity exists for."""
        payload = {"name": "Site B", "slug": "site-b", "custom_fields": {"ext_id": "S-1"}}
        self.assertEqual(find_existing_object(payload, "dcim.site"), self.site)


class BulkPlanGuardTests(APITestCase):
    """The error reaches the deviation review: no change set, one message on asset_tag."""

    def setUp(self):
        """Authenticate as the Diode user and seed member-a with the value and a tag."""
        self.url = "/netbox/api/plugins/diode/bulk-plan/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user)
        self.introspect_patcher.start()
        django_cache.clear()
        site, dt, role = _device_fixtures()
        _unique_field("stack_id", Device)
        self.device = Device.objects.create(
            name="member-a", site=site, device_type=dt, role=role, asset_tag="TAG-A", custom_field_data={"stack_id": "STACK-001"}
        )

    def tearDown(self):
        """Stop the auth patch and empty the shared cache."""
        self.introspect_patcher.stop()
        django_cache.clear()
        super().tearDown()

    def _plan_member_b(self, **fields):
        entity = {
            "name": "member-b",
            "site": {"name": "guard-site"},
            "role": {"name": "guard-role"},
            "device_type": {"model": "guard-dt", "manufacturer": {"name": "guard-mfr"}},
            "custom_fields": {"stack_id": {"text": "STACK-001"}},
            **fields,
        }
        payload = {"entities": [{"id": "b", "object_type": "dcim.device", "entity": {"device": entity}}]}
        response = self.client.post(self.url, data=payload, format="json", **self.authorization_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()["results"][0]

    def test_the_second_member_is_refused_with_the_reason(self):
        """The plan carries the message and no change set."""
        result = self._plan_member_b(asset_tag="TAG-B")
        self.assertIsNone(result.get("change_set"))
        [message] = result["errors"]["dcim.device"]["asset_tag"]
        self.assertIn("asset_tag 'TAG-A'", message)
        self.assertIn("asset_tag 'TAG-B'", message)
        self.assertIn("do not mark stack_id as unique", message)

    def test_a_rename_still_plans_an_update(self):
        """Without an asset tag in the payload the row binds, as before."""
        result = self._plan_member_b()
        self.assertIsNone(result.get("errors"))
        changes = result["change_set"]["changes"]
        [change] = [c for c in changes if c["object_type"] == "dcim.device"]
        self.assertEqual(change["change_type"], "update")
        self.assertEqual(change["object_id"], self.device.pk)
        self.assertFalse([c for c in changes if c["change_type"] == "create"])


class CacheKeyTests(TestCase):
    """A cached answer never stands in for a lookup that differs in a unique custom field."""

    @classmethod
    def setUpTestData(cls):
        """Two devices keyed by stack_id: one without a tag, one with TAG-2."""
        cls.site, dt, role = _device_fixtures()
        _unique_field("stack_id", Device)
        cls.untagged = Device.objects.create(
            name="member-x", site=cls.site, device_type=dt, role=role, custom_field_data={"stack_id": "STACK-X"}
        )
        cls.tagged = Device.objects.create(
            name="member-y", site=cls.site, device_type=dt, role=role, asset_tag="TAG-2", custom_field_data={"stack_id": "STACK-Y"}
        )

    def setUp(self):
        """Keep lookup answers from an earlier test out of this one."""
        django_cache.clear()

    def test_custom_field_values_are_part_of_the_key(self):
        """Same scalars with different values give different keys; equal values give one key."""
        base = {"name": "n", "site": self.site.pk, "asset_tag": "TAG-P"}
        with_x = _find_obj_cache_key({**base, "custom_fields": {"stack_id": "STACK-X"}}, "dcim.device")
        with_y = _find_obj_cache_key({**base, "custom_fields": {"stack_id": "STACK-Y"}}, "dcim.device")
        again = _find_obj_cache_key({**base, "custom_fields": {"stack_id": "STACK-X"}}, "dcim.device")
        self.assertNotEqual(with_x, with_y)
        self.assertEqual(with_x, again)
        self.assertNotEqual(with_x, _find_obj_cache_key(base, "dcim.device"))

    def test_non_scalar_custom_field_values_hash_the_same_way(self):
        """JSON and list values take part, and key order inside them does not matter."""
        base = {"name": "n", "site": self.site.pk}
        one = _find_obj_cache_key({**base, "custom_fields": {"meta": {"b": 1, "a": [1, 2]}}}, "dcim.device")
        two = _find_obj_cache_key({**base, "custom_fields": {"meta": {"a": [1, 2], "b": 1}}}, "dcim.device")
        self.assertEqual(one, two)

    def test_a_cached_answer_does_not_bypass_the_guard(self):
        """The first lookup binds and is cached. The second differs only in stack_id and must raise."""
        scalars = {"name": "member-n", "site": self.site.pk, "asset_tag": "TAG-P"}
        token = enter_request_obj_cache()
        try:
            first = find_existing_object({**scalars, "custom_fields": {"stack_id": "STACK-X"}}, "dcim.device")
            self.assertEqual(first, self.untagged)
            with self.assertRaises(ConflictingObjectMatch):
                find_existing_object({**scalars, "custom_fields": {"stack_id": "STACK-Y"}}, "dcim.device")
        finally:
            exit_request_obj_cache(token)
