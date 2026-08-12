"""E2E: duplicate module creates adopt the existing module at apply time."""
import uuid
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class ModuleAdoptionE2ETests(APITestCase):
    """Plan-ahead duplicate module CREATEs must adopt, not discard or crash."""

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.auth = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        p = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user
        )
        p.start()
        self.addCleanup(p.stop)

        self.site = Site.objects.create(name="ma-site", slug="ma-site")
        mfr = Manufacturer.objects.create(name="ma-mfr", slug="ma-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="ma-dt", slug="ma-dt")
        self.role = DeviceRole.objects.create(name="ma-role", slug="ma-role")
        self.mt = ModuleType.objects.create(manufacturer=mfr, model="ma-linecard")
        self.dev = Device.objects.create(
            name="ma-rtr", site=self.site, device_type=self.dt, role=self.role
        )
        self.bay = ModuleBay.objects.create(device=self.dev, name="ma-bay1")

    def _dev_ref(self):
        return {"name": "ma-rtr", "site": {"name": "ma-site"}}

    def _module_entity(self, extra=None):
        entity = {
            "device": self._dev_ref(),
            "module_bay": {"name": "ma-bay1", "device": self._dev_ref()},
            "module_type": {"manufacturer": {"name": "ma-mfr"}, "model": "ma-linecard"},
            "status": "active",
        }
        entity.update(extra or {})
        return {"timestamp": 1, "object_type": "dcim.module", "entity": {"module": entity}}

    def _subbay_entity(self):
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": "ma-sub1",
            "module": {
                "device": self._dev_ref(),
                "module_bay": {"name": "ma-bay1", "device": self._dev_ref()},
                "module_type": {"manufacturer": {"name": "ma-mfr"}, "model": "ma-linecard"},
            },
        }}}

    def _diff(self, payload):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("change_set", r.json(), r.content)
        return r.json().get("change_set", {})

    def _apply(self, cs, expect=200):
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def test_plan_ahead_duplicate_adopts_and_applies_payload(self):
        """Both changesets planned before either apply: adopt, don't discard."""
        cs_a = self._diff(self._module_entity())
        # round B diverges on serial — the load-bearing lossiness probe
        cs_b = self._diff(self._subbay_entity())
        cs_a2 = self._diff(self._module_entity({"serial": "SER-B99"}))

        self._apply(cs_a)
        self._apply(cs_b)
        self._apply(cs_a2)

        modules = Module.objects.filter(module_bay=self.bay)
        self.assertEqual(modules.count(), 1)
        module = modules.first()
        self.assertEqual(module.serial, "SER-B99")  # applied, not discarded
        sub = ModuleBay.objects.get(name="ma-sub1")
        self.assertEqual(sub.module_id, module.pk)  # nesting restored

        # converged: re-plan of both entities is empty
        for payload in (self._module_entity({"serial": "SER-B99"}), self._subbay_entity()):
            cs = self._diff(payload)
            non_noop = [c for c in cs.get("changes", []) if c["change_type"] != "noop"]
            self.assertEqual(non_noop, [], non_noop)

    def test_null_asset_tag_create_does_not_relocate(self):
        """The hazard pin: asset_tag null must not adopt an unrelated module."""
        other_dev = Device.objects.create(
            name="ma-other", site=self.site, device_type=self.dt, role=self.role
        )
        other_bay = ModuleBay.objects.create(device=other_dev, name="ma-obay")
        other_module = Module.objects.create(
            device=other_dev, module_bay=other_bay, module_type=self.mt
        )
        cs = self._diff(self._module_entity({"asset_tag": None}))
        self._apply(cs)
        other_module.refresh_from_db()
        self.assertEqual(other_module.device_id, other_dev.pk)  # untouched
        self.assertEqual(Module.objects.filter(module_bay=self.bay).count(), 1)

    def test_occupied_bay_update_last_writer_wins(self):
        """A planned UPDATE on an occupied bay applies last-writer-wins module_type."""
        Module.objects.create(device=self.dev, module_bay=self.bay, module_type=self.mt)
        mt2 = ModuleType.objects.create(
            manufacturer=Manufacturer.objects.get(name="ma-mfr"), model="ma-linecard-v2"
        )
        payload = self._module_entity()
        payload["entity"]["module"]["module_type"]["model"] = "ma-linecard-v2"
        cs = self._diff(payload)
        self._apply(cs)
        modules = Module.objects.filter(module_bay=self.bay)
        self.assertEqual(modules.count(), 1)
        self.assertEqual(modules.first().module_type_id, mt2.pk)  # last writer wins

    def test_invalid_adoption_payload_aborts_changeset_atomically(self):
        """
        A 400 against the adopted instance rolls back sibling changes.

        Hand-crafted changeset (deterministic): a device/bay-mismatched module
        CREATE plus an innocent sibling site CREATE. Today the mismatch is
        swallowed (ValidationError fallback returns the existing module) and
        the changeset succeeds with the sibling applied; with adoption the
        mismatch surfaces as 400 and the whole changeset rolls back.
        """
        Module.objects.create(device=self.dev, module_bay=self.bay, module_type=self.mt)
        wrong_dev = Device.objects.create(
            name="ma-wrong", site=self.site, device_type=self.dt, role=self.role
        )
        cs = {
            "id": str(uuid.uuid4()),
            "changes": [
                {"id": str(uuid.uuid4()), "change_type": "create", "object_type": "dcim.site",
                 "ref_id": "s1", "data": {"name": "ma-sibling-site", "slug": "ma-sibling-site"}},
                {"id": str(uuid.uuid4()), "change_type": "create", "object_type": "dcim.module",
                 "ref_id": "m1", "data": {"device": wrong_dev.pk, "module_bay": self.bay.pk,
                                          "module_type": self.mt.pk, "status": "active"}},
            ],
        }
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Site.objects.filter(name="ma-sibling-site").exists())  # atomic abort

    def test_miss_then_create_still_works(self):
        """Find-first miss on an empty bay falls through to a normal create."""
        cs = self._diff(self._module_entity())
        self._apply(cs)
        self.assertEqual(Module.objects.filter(module_bay=self.bay).count(), 1)
