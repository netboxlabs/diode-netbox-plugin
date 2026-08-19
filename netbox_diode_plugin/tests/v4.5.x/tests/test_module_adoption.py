"""E2E: module/module-bay ingest shapes — create adoption, and the installed_module reverse side."""
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


class ModuleBayInstalledModuleE2ETests(APITestCase):
    """
    The reverse side of the module/bay relation: dcim.modulebay.installed_module.

    A producer walking a device's bays naturally nests the module inside the
    bay it occupies, and the nested module has to name its own module_bay
    (Module.module_bay is a required OneToOneField). That names the outer bay
    back, so the pair only plans if the reverse-side write is deferred.
    """

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

        site = Site.objects.create(name="im-site", slug="im-site")
        mfr = Manufacturer.objects.create(name="im-mfr", slug="im-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="im-dt", slug="im-dt")
        role = DeviceRole.objects.create(name="im-role", slug="im-role")
        self.mt = ModuleType.objects.create(manufacturer=mfr, model="im-linecard")
        self.dev = Device.objects.create(
            name="im-rtr", site=site, device_type=dt, role=role
        )

    def _dev_ref(self):
        return {"name": "im-rtr", "site": {"name": "im-site"}}

    def _module_ref(self, bay_name):
        return {
            "device": self._dev_ref(),
            "module_bay": {"device": self._dev_ref(), "name": bay_name},
            "module_type": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
            "serial": "IM-SER-1",
        }

    def _installed_module_entity(self, bay_name="im-bay1"):
        """A bay carrying the module installed in it — the natural shape."""
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": bay_name,
            "label": "IM-1",
            "installed_module": self._module_ref(bay_name),
        }}}

    def _parent_module_entity(self, bay_name="im-bay2"):
        """The same bay claiming the module as its PARENT — contradictory data."""
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": bay_name,
            "module": self._module_ref(bay_name),
        }}}

    def _diff(self, payload, expect=200):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json().get("change_set", {}) or {}

    def _apply(self, cs, expect=200):
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def _non_noop(self, cs):
        return [c for c in cs.get("changes", []) if c["change_type"] != "noop"]

    def test_installed_module_natural_shape_installs_the_module(self):
        """The bay-carries-its-module shape plans, applies, and lands in the DB."""
        self._apply(self._diff(self._installed_module_entity()))

        bay = ModuleBay.objects.get(device=self.dev, name="im-bay1")
        self.assertEqual(bay.label, "IM-1")
        module = Module.objects.get(module_bay=bay)
        self.assertEqual(module.device_id, self.dev.pk)
        self.assertEqual(module.module_type_id, self.mt.pk)
        self.assertEqual(module.serial, "IM-SER-1")
        # the reverse accessor the field is named after now resolves
        self.assertEqual(bay.installed_module.pk, module.pk)
        self.assertEqual(Module.objects.filter(device=self.dev).count(), 1)

        # and it converges: a second plan of the same payload is a no-op
        self.assertEqual(self._non_noop(self._diff(self._installed_module_entity())), [])

    def test_reingest_of_an_already_installed_module_is_a_noop(self):
        """
        Rows already correct in the DB must survive a second pass.

        This is the convergence case: _topo_sort runs before
        _resolve_existing_references, so an undeclared cycle rejects the
        payload at plan time even when the database already agrees with it.
        """
        bay = ModuleBay.objects.create(device=self.dev, name="im-bay1", label="IM-1")
        module = Module.objects.create(
            device=self.dev, module_bay=bay, module_type=self.mt, serial="IM-SER-1"
        )

        self.assertEqual(self._non_noop(self._diff(self._installed_module_entity())), [])

        module.refresh_from_db()
        self.assertEqual(module.module_bay_id, bay.pk)
        self.assertEqual(Module.objects.filter(device=self.dev).count(), 1)

    def test_parent_module_field_still_reports_the_recursion_error(self):
        """
        dcim.modulebay.module stays a better-error path, NOT a working shape.

        Its table entry exists to reach NetBox's own recursion message instead
        of the opaque plan-time cycle error; a bay cannot be a sub-bay of the
        module installed in it. Declaring installed_module must not turn this
        contradiction into a success.
        """
        cs = self._diff(self._parent_module_entity())
        r = self._apply(cs, expect=400)
        self.assertIn(
            "A module bay cannot belong to a module installed within it.",
            r.content.decode(),
        )
        # the failed bay update aborts the whole change set
        self.assertFalse(Module.objects.filter(device=self.dev).exists())
