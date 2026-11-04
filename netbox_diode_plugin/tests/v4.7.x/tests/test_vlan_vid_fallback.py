"""Regression tests for VLAN VID matching when group is omitted."""

from types import SimpleNamespace
from unittest import mock

from dcim.models import Site
from django.conf import settings
from django.test import TestCase
from ipam.models import VLAN, VLANGroup
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.matcher import AmbiguousObjectMatch, find_existing_object
from netbox_diode_plugin.plugin_config import get_diode_user


class VLANVidFallbackMatcherTests(TestCase):
    """Direct matching against the test database."""

    def test_omitted_group_matches_existing_grouped_vlan(self):
        group = VLANGroup.objects.create(name="Fallback Test Group", slug="fallback-test")
        existing = VLAN.objects.create(
            vid=123, name="Original VLAN Name", group=group
        )

        matched = find_existing_object({"vid": 123}, "ipam.vlan")

        self.assertIsNotNone(matched)
        self.assertEqual(matched.pk, existing.pk)

    def test_omitted_group_rejects_ambiguous_vid(self):
        first_group = VLANGroup.objects.create(
            name="Fallback Group A", slug="fallback-group-a"
        )
        second_group = VLANGroup.objects.create(
            name="Fallback Group B", slug="fallback-group-b"
        )
        VLAN.objects.create(vid=123, name="First VLAN", group=first_group)
        VLAN.objects.create(vid=123, name="Second VLAN", group=second_group)

        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object({"vid": 123}, "ipam.vlan")

    def test_omitted_group_rejects_grouped_and_ungrouped_ambiguity(self):
        group = VLANGroup.objects.create(
            name="Mixed Ambiguity Group", slug="mixed-ambiguity-group"
        )
        VLAN.objects.create(
            vid=123, name="Grouped VLAN", group=group
        )
        VLAN.objects.create(
            vid=123, name="Ungrouped VLAN"
        )

        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object({"vid": 123}, "ipam.vlan")


    def test_explicit_null_group_does_not_adopt_grouped_vlan(self):
        group = VLANGroup.objects.create(
            name="Explicit Null Group", slug="explicit-null-group"
        )
        grouped = VLAN.objects.create(
            vid=123, name="Grouped VLAN", group=group
        )

        matched = find_existing_object(
            {"vid": 123, "group": None}, "ipam.vlan"
        )

        self.assertTrue(matched is None or matched.pk != grouped.pk)

    def test_explicit_group_matches_only_requested_group(self):
        first_group = VLANGroup.objects.create(
            name="Explicit Group A", slug="explicit-group-a"
        )
        second_group = VLANGroup.objects.create(
            name="Explicit Group B", slug="explicit-group-b"
        )
        expected = VLAN.objects.create(
            vid=123, name="Expected VLAN", group=first_group
        )
        VLAN.objects.create(
            vid=123, name="Other VLAN", group=second_group
        )

        matched = find_existing_object(
            {"vid": 123, "group": first_group.pk}, "ipam.vlan"
        )

        self.assertIsNotNone(matched)
        self.assertEqual(matched.pk, expected.pk)

    def test_omitted_site_does_not_match_site_specific_vlan(self):
        site = Site.objects.create(
            name="Fallback Site A", slug="fallback-site-a"
        )
        VLAN.objects.create(
            vid=123, name="Site-specific VLAN", site=site
        )

        matched = find_existing_object({"vid": 123}, "ipam.vlan")

        self.assertIsNone(matched)

    def test_specified_site_does_not_match_different_site(self):
        site_a = Site.objects.create(
            name="Fallback Site A", slug="fallback-site-a"
        )
        site_b = Site.objects.create(
            name="Fallback Site B", slug="fallback-site-b"
        )
        VLAN.objects.create(
            vid=123, name="Other Site VLAN", site=site_b
        )

        matched = find_existing_object(
            {"vid": 123, "site": site_a.pk}, "ipam.vlan"
        )

        self.assertIsNone(matched)

    def test_omitted_group_does_not_adopt_qinq_service_vlan(self):
        group = VLANGroup.objects.create(
            name="QinQ Test Group", slug="qinq-test-group"
        )
        VLAN.objects.create(
            vid=123,
            name="QinQ Service VLAN",
            group=group,
            qinq_role="svlan",
        )

        matched = find_existing_object({"vid": 123}, "ipam.vlan")

        self.assertIsNone(matched)

    def test_omitted_group_does_not_adopt_qinq_customer_vlan(self):
        group = VLANGroup.objects.create(
            name="QinQ Customer Group", slug="qinq-customer-group"
        )
        service_vlan = VLAN.objects.create(
            vid=608,
            name="QinQ Parent Service VLAN",
            group=group,
            qinq_role="svlan",
        )
        VLAN.objects.create(
            vid=123,
            name="QinQ Customer VLAN",
            group=group,
            qinq_role="cvlan",
            qinq_svlan=service_vlan,
        )

        matched = find_existing_object({"vid": 123}, "ipam.vlan")

        self.assertIsNone(matched)

    def test_incoming_qinq_service_vlan_does_not_adopt_ordinary_vlan(self):
        group = VLANGroup.objects.create(
            name="Incoming QinQ Group", slug="incoming-qinq-group"
        )
        existing = VLAN.objects.create(
            vid=123,
            name="Ordinary Grouped VLAN",
            group=group,
        )

        matched = find_existing_object(
            {"vid": 123, "qinq_role": "svlan"}, "ipam.vlan"
        )

        self.assertTrue(matched is None or matched.pk != existing.pk)

    def test_incoming_qinq_customer_vlan_does_not_adopt_ordinary_vlan(self):
        group = VLANGroup.objects.create(
            name="Incoming CVLAN Group", slug="incoming-cvlan-group"
        )
        existing = VLAN.objects.create(
            vid=123,
            name="Ordinary Grouped VLAN",
            group=group,
        )

        matched = find_existing_object(
            {"vid": 123, "qinq_role": "cvlan"}, "ipam.vlan"
        )

        self.assertTrue(matched is None or matched.pk != existing.pk)


class VLANVidFallbackAPITests(APITestCase):
    """Exercise VLAN adoption through planning and application."""

    def setUp(self):
        super().setUp()
        api_base = f"/{settings.BASE_PATH.strip('/')}/api/plugins/diode/"
        api_base = api_base.replace("//", "/")
        self.diff_url = f"{api_base}generate-diff/"
        self.apply_url = f"{api_base}apply-change-set/"
        self.auth = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}

        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=diode_user,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _plan(self, vid, name):
        payload = {
            "timestamp": 1,
            "object_type": "ipam.vlan",
            "entity": {"vlan": {"vid": vid, "name": name}},
        }
        response = self.client.post(
            self.diff_url, data=payload, format="json", **self.auth
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json().get("change_set", {})

    def _apply(self, change_set):
        response = self.client.post(
            self.apply_url, data=change_set, format="json", **self.auth
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json().get("errors"), response.content)

    def test_omitted_group_updates_existing_grouped_vlan(self):
        group = VLANGroup.objects.create(name="Fallback API Group", slug="fallback-api")
        existing = VLAN.objects.create(
            vid=123, name="Original VLAN Name", group=group
        )

        change_set = self._plan(123, "Updated VLAN Name")
        self._apply(change_set)

        existing.refresh_from_db()
        self.assertEqual(existing.name, "Updated VLAN Name")
        self.assertEqual(existing.group_id, group.pk)
        self.assertEqual(VLAN.objects.filter(vid=123).count(), 1)

        self.assertEqual(self._plan(123, "Updated VLAN Name").get("changes", []), [])
