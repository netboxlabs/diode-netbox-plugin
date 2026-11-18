#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - apply endpoints when NetBox refuses a save (AbortRequest)."""

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from extras.models import Tag
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

BULK_PLAN_URL = "/netbox/api/plugins/diode/bulk-plan/"
BULK_PLAN_APPLY_URL = "/netbox/api/plugins/diode/bulk-plan-apply/"
BULK_APPLY_URL = "/netbox/api/plugins/diode/bulk-apply/"
APPLY_CHANGE_SET_URL = "/netbox/api/plugins/diode/apply-change-set/"

TAG_REFUSAL = "Tag sites only cannot be assigned to manufacturer objects."


class AbortRequestApiTestCase(APITestCase):
    """Shared auth and helpers for the apply endpoints."""

    def setUp(self):
        """Mock token introspection and pick a unique name suffix."""
        patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=SimpleNamespace(
                user=get_diode_user(),
                token_scopes=["netbox:read", "netbox:write"],
                token_data={"scope": "netbox:read netbox:write"},
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.s = uuid4().hex[:8]

    def post(self, url, payload):
        """Post a JSON payload with the mocked bearer token."""
        return self.client.post(url, data=payload, format="json", HTTP_AUTHORIZATION="Bearer mocked_oauth_token")

    @staticmethod
    def restricted_tag():
        """Create a tag that NetBox only allows on sites."""
        tag = Tag.objects.create(name="sites only", slug="sites-only")
        tag.object_types.set([ObjectType.objects.get_for_model(Site)])
        return tag

    def plan(self, entry):
        """Plan one entity through /bulk-plan/ and return its change set."""
        response = self.post(BULK_PLAN_URL, {"entities": [entry]})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]["change_set"]

    def site_entry(self, entity_id, name):
        """Build a site entity."""
        return {"id": entity_id, "object_type": "dcim.site", "entity": {"site": {"name": name}}}

    def refused_manufacturer_entry(self, entity_id, name):
        """Build a manufacturer entity carrying the site-only tag."""
        return {
            "id": entity_id,
            "object_type": "dcim.manufacturer",
            "entity": {"manufacturer": {"name": name, "tags": [{"name": "sites only"}]}},
        }

    def results_by_id(self, response):
        """Index a bulk response's results by entity id."""
        return {result["id"]: result for result in response.json()["results"]}


class BulkPlanApplyAbortRequestTestCase(AbortRequestApiTestCase):
    """bulk-plan-apply fails only the entity NetBox refuses."""

    def test_refused_entity_fails_alone_with_netbox_message(self):
        """The refused entity carries NetBox's message; its neighbours apply."""
        s = self.s
        self.restricted_tag()
        response = self.post(BULK_PLAN_APPLY_URL, {"entities": [
            self.site_entry("a", f"a-{s}"),
            self.refused_manufacturer_entry("b", f"m-{s}"),
            self.site_entry("c", f"c-{s}"),
        ]})

        self.assertEqual(response.status_code, 207, response.content)
        results = self.results_by_id(response)
        self.assertIsNone(results["a"]["errors"])
        self.assertIsNone(results["c"]["errors"])
        self.assertEqual(results["b"]["errors"]["apply"], {"dcim.manufacturer": {"__all__": [TAG_REFUSAL]}})
        self.assertIsNotNone(results["b"]["change_set"])
        self.assertTrue(Site.objects.filter(name=f"a-{s}").exists())
        self.assertTrue(Site.objects.filter(name=f"c-{s}").exists())
        self.assertFalse(Manufacturer.objects.filter(name=f"m-{s}").exists())

    def test_two_refusals_fail_alone_and_neighbours_apply(self):
        """Each refused entity fails on its own; every other entity applies."""
        s = self.s
        self.restricted_tag()
        response = self.post(BULK_PLAN_APPLY_URL, {"entities": [
            self.site_entry("a", f"a-{s}"),
            self.refused_manufacturer_entry("m1", f"m1-{s}"),
            self.site_entry("c", f"c-{s}"),
            self.refused_manufacturer_entry("m2", f"m2-{s}"),
            self.site_entry("e", f"e-{s}"),
        ]})

        self.assertEqual(response.status_code, 207, response.content)
        results = self.results_by_id(response)
        for refused in ("m1", "m2"):
            self.assertEqual(results[refused]["errors"]["apply"], {"dcim.manufacturer": {"__all__": [TAG_REFUSAL]}})
        for applied in ("a", "c", "e"):
            self.assertIsNone(results[applied]["errors"])
            self.assertTrue(Site.objects.filter(name=f"{applied}-{s}").exists())
        self.assertFalse(Manufacturer.objects.filter(name__in=[f"m1-{s}", f"m2-{s}"]).exists())

    def test_refused_entity_rolls_back_parents_and_later_entities_recreate_them(self):
        """A refused device's new site and platform roll back; a later device creates them again."""
        s = self.s
        self.restricted_tag()
        manufacturer = Manufacturer.objects.create(name=f"mfr-{s}", slug=f"mfr-{s}")
        DeviceType.objects.create(manufacturer=manufacturer, model=f"dt-{s}", slug=f"dt-{s}")
        DeviceRole.objects.create(name=f"role-{s}", slug=f"role-{s}")

        def device_entry(name, **extra):
            return {"id": name, "object_type": "dcim.device", "entity": {"device": {
                "name": name,
                "site": {"name": f"new-site-{s}"},
                "platform": {"name": f"plat-{s}"},
                "role": {"name": f"role-{s}"},
                "device_type": {"manufacturer": {"name": f"mfr-{s}"}, "model": f"dt-{s}"},
                **extra,
            }}}

        response = self.post(BULK_PLAN_APPLY_URL, {"entities": [
            device_entry(f"sw1-{s}", tags=[{"name": "sites only"}]),
            device_entry(f"sw2-{s}"),
        ]})

        self.assertEqual(response.status_code, 207, response.content)
        results = self.results_by_id(response)
        self.assertEqual(
            results[f"sw1-{s}"]["errors"]["apply"],
            {"dcim.device": {"__all__": ["Tag sites only cannot be assigned to device objects."]}},
        )
        self.assertIsNone(results[f"sw2-{s}"]["errors"])
        self.assertEqual(Site.objects.filter(name=f"new-site-{s}").count(), 1)
        self.assertEqual(Platform.objects.filter(name=f"plat-{s}").count(), 1)
        self.assertFalse(Device.objects.filter(name=f"sw1-{s}").exists())
        device = Device.objects.get(name=f"sw2-{s}")
        self.assertEqual(device.site.name, f"new-site-{s}")
        self.assertEqual(device.platform.name, f"plat-{s}")


class ApplyEndpointsAbortRequestTestCase(AbortRequestApiTestCase):
    """apply-change-set and bulk-apply report the refusal on the change set."""

    def test_apply_change_set_returns_400_with_netbox_message(self):
        """A refused change set gets a 400 carrying NetBox's message."""
        s = self.s
        self.restricted_tag()
        change_set = self.plan(self.refused_manufacturer_entry("b", f"m-{s}"))

        response = self.post(APPLY_CHANGE_SET_URL, change_set)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["errors"], {"dcim.manufacturer": {"__all__": [TAG_REFUSAL]}})
        self.assertFalse(Manufacturer.objects.filter(name=f"m-{s}").exists())

    def test_bulk_apply_reports_refusal_on_its_change_set(self):
        """bulk-apply applies the good change set and reports NetBox's message on the refused one."""
        s = self.s
        self.restricted_tag()
        good = self.plan(self.site_entry("a", f"a-{s}"))
        refused = self.plan(self.refused_manufacturer_entry("b", f"m-{s}"))

        response = self.post(BULK_APPLY_URL, {"change_sets": [good, refused]})

        self.assertEqual(response.status_code, 207, response.content)
        results = response.json()["results"]
        self.assertFalse(results[0].get("errors"))
        self.assertTrue(Site.objects.filter(name=f"a-{s}").exists())
        self.assertEqual(results[1]["errors"], {"dcim.manufacturer": {"__all__": [TAG_REFUSAL]}})
        self.assertFalse(Manufacturer.objects.filter(name=f"m-{s}").exists())
