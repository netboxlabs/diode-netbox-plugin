"""E2E: module/module-bay ingest shapes — create adoption, and the installed_module reverse side."""
import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectChange
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers
from utilities.testing import APITestCase

from netbox_diode_plugin.api import transformer
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

    def _mismatched_entity(self, outer_bay, inner_bay):
        """A bay carrying a module whose own module_bay names a DIFFERENT bay."""
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": outer_bay,
            "label": "IM-X",
            "installed_module": self._module_ref(inner_bay),
        }}}

    def test_a_module_naming_another_bay_is_refused(self):
        """
        The two sides must name one bay, because only one of them installs it.

        Deferring installed_module buys a plannable ORDER, not a write: the
        module's own module_bay FK is what installs it, and the deferred reverse
        update only touches the related object in memory. So a bay carrying a
        module whose module_bay names a different bay describes something no
        write can produce.

        Measured before this check, bay A carrying a module naming bay B: both
        bays and the module were created, apply answered 200 with errors null,
        the module landed in B with A empty, and every later ingest of the
        identical payload re-planned the same reverse-side update to no effect.
        A success that never converges.

        Refused rather than resolved in the outer bay's favour: the two
        statements are equally explicit and nothing in the payload marks either
        as the mistake, so silently rewriting the module's own module_bay would
        be a guess.
        """
        r = self.client.post(
            self.diff_url, data=self._mismatched_entity("im-bayX", "im-bayY"),
            format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        self.assertIn("im-bayY", errors)
        self.assertIn("im-bayX", errors)
        self.assertEqual(
            ModuleBay.objects.filter(device=self.dev, name="im-bayX").count(), 0,
            "the refused payload created a bay anyway",
        )
        self.assertEqual(Module.objects.filter(device=self.dev).count(), 0)

    def test_a_module_naming_its_own_bay_is_still_accepted(self):
        """The control: the natural shape names the outer bay and still applies."""
        self._apply(self._diff(self._installed_module_entity("im-bayok")))
        bay = ModuleBay.objects.get(device=self.dev, name="im-bayok")
        self.assertEqual(bay.installed_module.module_bay_id, bay.pk)


    def _mismatched_camel_entity(self, outer_bay, inner_bay):
        """The same contradiction, in the camelCase protoJSON spelling."""
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": outer_bay,
            "installedModule": {
                "device": self._dev_ref(),
                "moduleBay": {"device": self._dev_ref(), "name": inner_bay},
                "moduleType": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
                "serial": "IM-SER-1",
            },
        }}}

    def test_a_camelcase_module_naming_another_bay_is_refused(self):
        """
        The refusal must not depend on which spelling the producer sent.

        _ensure_snake_case is shallow: it rewrites the keys of the object being
        transformed, and every nested object is normalized later by its own
        recursion. So when the check runs, a camelCase producer's nested module
        still carries `moduleBay`, and reading only the snake spelling saw
        nothing at all.

        Measured with the snake-only lookup: this payload planned
        [create bay, create bay, create module, update bay], applied 200 with
        errors null, left the outer bay empty with the module in the inner bay,
        and re-planned the same no-op update on every later ingest -- the exact
        non-converging success the check exists to refuse, reachable by sending
        the supported camelCase form of a payload the check already caught.
        """
        r = self.client.post(
            self.diff_url, data=self._mismatched_camel_entity("cc-bayX", "cc-bayY"),
            format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        self.assertIn("cc-bayX", errors)
        self.assertIn("cc-bayY", errors)
        self.assertEqual(ModuleBay.objects.filter(device=self.dev).count(), 0)
        self.assertEqual(Module.objects.filter(device=self.dev).count(), 0)

    def test_a_module_naming_a_same_named_bay_on_another_device_is_refused(self):
        """
        A bare device name is not a device, so the comparison carries its scope.

        Device is unique on (Lower(name), site, tenant), so two devices may
        share a name in different sites -- and then two bays sharing a name are
        still two different bays. Comparing bays by (device_name, bay_name)
        collapsed them into one.

        Measured with the name-only comparison: this payload applied 200 with
        errors null and installed the module in the OTHER site's bay, leaving
        this site's bay empty and re-planning the reverse update on every later
        ingest.
        """
        site2 = Site.objects.create(name="im-site2", slug="im-site2")
        Device.objects.create(
            name="im-rtr", site=site2, device_type=self.dev.device_type,
            role=self.dev.role,
        )
        other_device = {"name": "im-rtr", "site": {"name": "im-site2"}}
        entity = {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": self._dev_ref(),
            "name": "sc-bay",
            "installed_module": {
                "device": other_device,
                "module_bay": {"device": other_device, "name": "sc-bay"},
                "module_type": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
                "serial": "IM-SER-1",
            },
        }}}
        r = self.client.post(self.diff_url, data=entity, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        # both bays are named 'sc-bay' on a device named 'im-rtr', so the scope
        # is the only thing that tells the two sides apart -- an error naming
        # the same bay twice would be unactionable
        self.assertIn("in 'im-site'", errors)
        self.assertIn("in 'im-site2'", errors)
        self.assertEqual(ModuleBay.objects.filter(name="sc-bay").count(), 0)
        self.assertEqual(Module.objects.count(), 0)

    def test_a_module_naming_a_bay_on_a_device_with_another_asset_tag_is_refused(self):
        """
        A device reference can identify its device without naming it.

        Device is unique on asset_tag alone, so two references carrying
        different asset_tags are two devices even with no name on either side.
        Comparing only name/site/tenant found nothing asserted on both sides
        and read that as compatible.

        Measured with name/site/tenant only: this payload applied 200 with
        errors null, installed the module in the OTHER device's bay, and left
        this one empty to be re-planned on every later ingest.
        """
        dev_a = Device.objects.create(
            name="im-at-a", site=self.dev.site, device_type=self.dev.device_type,
            role=self.dev.role, asset_tag="AT-A",
        )
        Device.objects.create(
            name="im-at-b", site=self.dev.site, device_type=self.dev.device_type,
            role=self.dev.role, asset_tag="AT-B",
        )
        other = {"asset_tag": "AT-B"}
        entity = {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "device": {"asset_tag": "AT-A"},
            "name": "at-bay",
            "installed_module": {
                "device": other,
                "module_bay": {"device": other, "name": "at-bay"},
                "module_type": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
                "serial": "IM-SER-1",
            },
        }}}
        r = self.client.post(self.diff_url, data=entity, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        # both bays are named 'at-bay' and neither device is named, so the
        # asset tags are the only thing telling the two sides apart
        self.assertIn("AT-A", errors)
        self.assertIn("AT-B", errors)
        self.assertEqual(ModuleBay.objects.filter(device=dev_a).count(), 0)
        self.assertEqual(Module.objects.count(), 0)

    def test_a_module_naming_a_different_addressed_bay_is_refused(self):
        """
        A payload can address its row by primary key and name nothing at all.

        metadata.source_match.netbox_id is the strongest identity in the
        pipeline, and a PK-addressed reference need carry no name, so a
        comparison over names and selectors alone found nothing asserted on
        both sides and read that as compatible.

        The outer entity's metadata is popped before this check runs -- it
        would not survive _ensure_snake_case -- so the check is handed it
        explicitly; without that the outer side cannot say which row it
        addresses.

        Measured before the fix: 200 with errors null, the module installed in
        bay B, and bay A given no change at all -- the two PK-addressed bay
        nodes merged into one and arrival order decided which id survived. It
        then CONVERGED on the wrong row, which is worse than the re-planning
        shapes above: nothing further would ever mention it.
        """
        bay_a = ModuleBay.objects.create(device=self.dev, name="pk-bayA")
        bay_b = ModuleBay.objects.create(device=self.dev, name="pk-bayB")
        entity = {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "metadata": {"source_match": {"netbox_id": bay_a.pk}},
            "installed_module": {
                "device": self._dev_ref(),
                "module_bay": {"metadata": {"source_match": {"netbox_id": bay_b.pk}}},
                "module_type": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
                "serial": "IM-SER-1",
            },
        }}}

        r = self.client.post(self.diff_url, data=entity, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        # It must be THIS guard that refused, not the addressed-row merge
        # refusal downstream: that one catches the same payload (measured --
        # they are independent layers), so asserting only on the status and the
        # ids would pass with the outer metadata never reaching this check.
        self.assertIn("whose module_bay is", errors)
        # neither side names anything, so the ids are all the message has
        self.assertIn(str(bay_a.pk), errors)
        self.assertIn(str(bay_b.pk), errors)
        self.assertEqual(Module.objects.count(), 0)
        bay_a.refresh_from_db()
        bay_b.refresh_from_db()
        self.assertIsNone(getattr(bay_a, "installed_module", None))
        self.assertIsNone(getattr(bay_b, "installed_module", None))

    def _pk_addressed_entity(self, bay_pk, nested_bay_name):
        """Outer bay addressed by pk ALONE; nested module_bay named normally."""
        return {"timestamp": 1, "object_type": "dcim.modulebay", "entity": {"module_bay": {
            "metadata": {"source_match": {"netbox_id": bay_pk}},
            "installed_module": {
                "device": self._dev_ref(),
                "module_bay": {"device": self._dev_ref(), "name": nested_bay_name},
                "module_type": {"manufacturer": {"name": "im-mfr"}, "model": "im-linecard"},
                "serial": "IM-SER-1",
            },
        }}}

    def test_a_module_naming_another_bay_than_the_addressed_one_is_refused(self):
        """
        A partial reference need not repeat the name, so one side can be silent.

        With the outer bay addressed by pk alone, a comparison over asserted
        selectors finds nothing on that side to read: the two sides share no
        criterion and look compatible. Measured before the fix: 200 with errors
        null, the module installed in the NAMED bay, the addressed bay left
        empty, and the ineffective reverse update re-planned on every ingest.

        The addressed side is now resolved -- the row is the authority on what
        it is -- and the ordinary compatibility relation does the rest.
        """
        bay_a = ModuleBay.objects.create(device=self.dev, name="os-bayA")
        ModuleBay.objects.create(device=self.dev, name="os-bayB")

        r = self.client.post(
            self.diff_url, data=self._pk_addressed_entity(bay_a.pk, "os-bayB"),
            format="json", **self.auth)

        self.assertEqual(r.status_code, 400, r.content)
        errors = str(r.json().get("errors"))
        self.assertIn("whose module_bay is", errors)
        # resolving means the message can name the row, not just its id
        self.assertIn("os-bayA", errors)
        self.assertIn("os-bayB", errors)
        self.assertIn(str(bay_a.pk), errors)
        self.assertEqual(Module.objects.count(), 0)

    def test_a_module_naming_the_addressed_bay_itself_still_applies(self):
        """
        The control, and the reason the fix resolves instead of refusing.

        Naming by pk on one side and by name on the other is a legitimate way
        to describe ONE bay. Treating an incomparable pair as a conflict would
        have closed the hole above by breaking this, so it is asserted right
        beside it: it applies, installs the module in the addressed bay, and
        converges.
        """
        bay_a = ModuleBay.objects.create(device=self.dev, name="os-bayA")
        ModuleBay.objects.create(device=self.dev, name="os-bayB")

        entity = self._pk_addressed_entity(bay_a.pk, "os-bayA")
        self._apply(self._diff(entity))

        bay_a.refresh_from_db()
        self.assertEqual(bay_a.installed_module.module_bay_id, bay_a.pk)
        self.assertEqual(Module.objects.count(), 1)
        self.assertEqual(self._non_noop(self._diff(entity)), [])

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

    def test_deferred_reverse_write_records_an_update_with_no_prechange(self):
        """
        Pin the changelog shape of the deferred (ref_id) UPDATE branch.

        This shape is the cleanest probe of that branch there is: the deferred
        write persists nothing at all (installed_module is a reverse
        one-to-one, so asserting it only mutates the related object in memory),
        which makes the row's presence in the changelog depend entirely on
        whether the branch takes a prechange snapshot. NetBox's
        ObjectChange.has_changes drops an update whose prechange equals its
        postchange, so populating prechange_data here removes the 'update' row
        outright -- measured: ['create', 'update'] becomes ['create'].

        The branch re-reads its row (to keep a counter honest, see
        test_virtualchassis_ingest) and a re-read makes a snapshot cheap to
        take, which is exactly why this needs pinning in whichever direction it
        ships: a changelog behaviour change must be a decision, not a side
        effect of an unrelated fix. Shipped direction: unchanged from before
        the re-read -- both rows recorded, neither carrying a prechange.
        """
        self._apply(self._diff(self._installed_module_entity()))

        bay = ModuleBay.objects.get(device=self.dev, name="im-bay1")
        rows = list(ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="modulebay",
            changed_object_id=bay.pk,
        ).order_by("pk"))

        self.assertEqual([r.action for r in rows], ["create", "update"], rows)
        for row in rows:
            self.assertIsNone(row.prechange_data, row)
            self.assertIsNotNone(row.postchange_data, row)

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


class ReferencesConflictRuleTests(TestCase):
    """
    The rule table for `references_conflict`, without the pipeline.

    The relation answers one question -- can these two payloads denote the same
    object? -- and it is a COMPATIBILITY relation, not equality: only a field
    both sides assert, with values that disagree, proves two objects. A field
    one side leaves out cannot contradict anything, which is what keeps the
    natural shape (the nested module naming its bay less fully than the outer
    payload does) from being refused.

    Every row here predates the relation being DERIVED from the matchers. They
    are kept, unchanged, precisely for that: three hand-written comparisons
    (bay, device, scope) were replaced by one that reads its criteria from
    get_model_matchers, and this table is the evidence the replacement decides
    every case the hand-written ones did -- ModuleBay unique on
    ('name', 'device'), Device on (Lower(name), site, tenant) and on asset_tag,
    Site and Tenant on name alone and slug alone.

    It is a TestCase rather than a SimpleTestCase because the criteria now come
    from the model registry, and reference fields recurse into the referenced
    type's own identity. No row queries a row.
    """

    def _bay(self, name, device=None):
        payload = {"name": name}
        if device is not None:
            payload["device"] = device
        return payload

    def test_the_rule_table(self):
        """Each decision the relation makes, and its symmetry, in one table."""
        dev = {"name": "rtr", "site": {"name": "s1"}}
        cases = [
            # (description, mine, theirs, conflict?)
            ("identical", self._bay("b", dev), self._bay("b", dev), False),
            ("different bay name", self._bay("b1", dev), self._bay("b2", dev), True),
            ("different bay name, no device anywhere",
             self._bay("b1"), self._bay("b2"), True),
            # Lower(name) is Device identity, so casing is not a difference.
            # An exact compare here refused a bay as differing from itself.
            ("device name differs only in case",
             self._bay("b", {"name": "RTR", "site": {"name": "s1"}}),
             self._bay("b", dev), False),
            ("device name differs",
             self._bay("b", {"name": "rtr-a"}), self._bay("b", {"name": "rtr-b"}), True),
            # the finding: same names, different sites, two real devices
            ("same device name, different site",
             self._bay("b", {"name": "rtr", "site": {"name": "s2"}}),
             self._bay("b", dev), True),
            ("same device name, different site slug",
             self._bay("b", {"name": "rtr", "site": {"slug": "s2"}}),
             self._bay("b", {"name": "rtr", "site": {"slug": "s1"}}), True),
            ("same device name, different tenant",
             self._bay("b", {"name": "rtr", "tenant": {"name": "t2"}}),
             self._bay("b", {"name": "rtr", "tenant": {"name": "t1"}}), True),
            # compatibility, not equality: what one side omits cannot contradict
            ("one side omits the site", self._bay("b", {"name": "rtr"}),
             self._bay("b", dev), False),
            ("one side omits the tenant",
             self._bay("b", {"name": "rtr", "site": {"name": "s1"}, "tenant": {"name": "t"}}),
             self._bay("b", dev), False),
            ("one side omits the device", self._bay("b"), self._bay("b", dev), False),
            ("device as a bare name string", self._bay("b", "rtr"),
             self._bay("b", dev), False),
            ("bare name string that disagrees", self._bay("b", "other"),
             self._bay("b", dev), True),
            # asset_tag is unique on its own, so it settles the question
            # outright -- and settling it FIRST is what stops a differing name
            # from refusing two references to one device
            ("different asset_tag, neither side named",
             self._bay("b", {"asset_tag": "A1"}),
             self._bay("b", {"asset_tag": "A2"}), True),
            ("different asset_tag, same name",
             self._bay("b", {"asset_tag": "A1", "name": "rtr"}),
             self._bay("b", {"asset_tag": "A2", "name": "rtr"}), True),
            ("same asset_tag, different names",
             self._bay("b", {"asset_tag": "A1", "name": "rtr-a"}),
             self._bay("b", {"asset_tag": "A1", "name": "rtr-b"}), False),
            ("same asset_tag, different sites",
             self._bay("b", {"asset_tag": "A1", "site": {"name": "s1"}}),
             self._bay("b", {"asset_tag": "A1", "site": {"name": "s2"}}), False),
            ("asset_tag on one side only falls back to the name",
             self._bay("b", {"asset_tag": "A1", "name": "rtr"}),
             self._bay("b", {"name": "other"}), True),
            # an explicit primary key is the strongest identity there is, so
            # it decides ahead of every selector below it
            ("different addressed rows, nothing else asserted",
             {"metadata": {"source_match": {"netbox_id": 1}}},
             {"metadata": {"source_match": {"netbox_id": 2}}}, True),
            ("different addressed rows, same bay and device names",
             {"name": "b", "device": {"name": "rtr"},
              "metadata": {"source_match": {"netbox_id": 1}}},
             {"name": "b", "device": {"name": "rtr"},
              "metadata": {"source_match": {"netbox_id": 2}}}, True),
            ("same addressed row, different bay names",
             {"name": "b1", "metadata": {"source_match": {"netbox_id": 1}}},
             {"name": "b2", "metadata": {"source_match": {"netbox_id": 1}}}, False),
            ("addressed on one side only falls back to the names",
             {"name": "b1", "metadata": {"source_match": {"netbox_id": 1}}},
             {"name": "b2"}, True),
            ("an unusable netbox_id is ignored, as the node builder ignores it",
             {"name": "b", "metadata": {"source_match": {"netbox_id": "nope"}}},
             {"name": "b", "metadata": {"source_match": {"netbox_id": 2}}}, False),
            # the stated bound: no criterion in common, so nothing to compare
            ("disjoint selectors stay compatible",
             self._bay("b", {"asset_tag": "A1"}), self._bay("b", {"name": "rtr"}), False),
            # not comparable at all -> never a refusal
            ("no bay name on one side", {"device": dev}, self._bay("b", dev), False),
            ("empty bay name", self._bay("", dev), self._bay("b", dev), False),
            ("nested side is not a dict", None, self._bay("b", dev), False),
        ]
        for description, mine, theirs, expected in cases:
            with self.subTest(description):
                self.assertEqual(
                    transformer.references_conflict("dcim.modulebay", mine, theirs), expected)
                # the relation must not depend on which side is which
                self.assertEqual(
                    transformer.references_conflict("dcim.modulebay", theirs, mine), expected,
                    f"{description}: not symmetric")

    def test_a_camelcase_parent_key_is_read(self):
        """
        The nested payload has not been snake_cased when the check reads it.

        This is the whole of the camelCase finding in one assertion: the guard
        looks up the nested object's `module_bay`, and a camelCase producer
        spells it `moduleBay`.
        """
        nested = {"moduleBay": {"name": "b2", "device": {"name": "rtr"}}}
        self.assertEqual(
            transformer._asserted(nested, "module_bay"),
            {"name": "b2", "device": {"name": "rtr"}},
        )
        self.assertIsNone(transformer._asserted(nested, "serial"))
        # an explicit snake_case key still wins
        self.assertEqual(
            transformer._asserted({"module_bay": {"name": "snake"}}, "module_bay"),
            {"name": "snake"},
        )


class AddressedRowMergeTests(SimpleTestCase):
    """
    Two nodes addressing DIFFERENT rows must not become one node.

    _carry_addressed_row picks whichever side carries an explicit
    metadata.source_match.netbox_id, which is only unambiguous if two different
    ids never reach it. An earlier revision asserted they could not, on the
    strength of vc_identities_conflict and the _fingerprint_dedupe
    qualification -- but both are dcim.virtualchassis machinery, so for every
    other type two differently-addressed nodes with nothing else to fingerprint
    merged, and arrival order decided which row the plan then wrote.
    """

    def _node(self, netbox_id=None, **fields):
        node = {"_object_type": "dcim.modulebay", "_uuid": "u", "_refs": set(),
                "_warnings": {}, **fields}
        if netbox_id is not None:
            node["_netbox_id"] = netbox_id
        return node

    def test_two_different_addressed_rows_are_refused(self):
        """Two primary keys are two rows; that needs no interpretation."""
        with self.assertRaises(serializers.ValidationError) as caught:
            transformer._merge_nodes(self._node(1547), self._node(1548))
        message = str(caught.exception)
        self.assertIn("1547", message)
        self.assertIn("1548", message)
        self.assertIn("netbox_id", message)

    def test_the_same_addressed_row_still_merges(self):
        """The refusal is about disagreement, not about being addressed."""
        merged = transformer._merge_nodes(
            self._node(1547, name="b"), self._node(1547, label="L"))
        self.assertEqual(merged["_netbox_id"], 1547)
        self.assertEqual(merged["name"], "b")
        self.assertEqual(merged["label"], "L")

    def test_one_addressed_side_still_carries_the_id(self):
        """Either order keeps the id, which is what _carry_addressed_row is for."""
        for a, b in ((self._node(1547), self._node()),
                     (self._node(), self._node(1547))):
            self.assertEqual(transformer._merge_nodes(a, b)["_netbox_id"], 1547)


class ComparableBaySidesTests(TestCase):
    """
    Which side of the reverse-side comparison gets resolved, and which does not.

    The lookup exists for one shape -- a side addressed by primary key that
    names nothing -- because there the missing criterion made a non-converging
    success reachable. Every other shape must stay a pure payload comparison,
    so the query is not paid on ordinary ingest.
    """

    @classmethod
    def setUpTestData(cls):
        """One device and one bay, to resolve against."""
        site = Site.objects.create(name="cb-site", slug="cb-site")
        mfr = Manufacturer.objects.create(name="cb-mfr", slug="cb-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="cb-dt", slug="cb-dt")
        role = DeviceRole.objects.create(name="cb-role", slug="cb-role")
        cls.dev = Device.objects.create(
            name="cb-rtr", site=site, device_type=dt, role=role, asset_tag="CB-1")
        cls.bay = ModuleBay.objects.create(device=cls.dev, name="cb-bay")

    def _sides(self, mine, theirs):
        return transformer._comparable_bay_sides(mine, theirs, "dcim.modulebay")

    def test_an_addressed_nameless_side_is_resolved(self):
        """It gains the row's real name and device, including the asset tag."""
        addressed = {"metadata": {"source_match": {"netbox_id": self.bay.pk}}}
        named = {"name": "other", "device": {"name": "cb-rtr"}}

        mine, theirs = self._sides(addressed, named)

        self.assertEqual(mine["name"], "cb-bay")
        self.assertEqual(mine["device"]["name"], "cb-rtr")
        self.assertEqual(mine["device"]["site"], {"name": "cb-site"})
        self.assertEqual(mine["device"]["asset_tag"], "CB-1")
        # the id survives, so the message can name what the producer addressed
        self.assertEqual(transformer._addressed_row_of(mine), self.bay.pk)
        self.assertIs(theirs, named, "the named side was resolved too")
        # and the resolved pair is now comparable, which is the whole point
        self.assertTrue(transformer.references_conflict("dcim.modulebay", mine, theirs))

    def test_the_nested_side_is_resolved_the_same_way(self):
        """Either side may be the addressed one."""
        named = {"name": "other", "device": {"name": "cb-rtr"}}
        addressed = {"metadata": {"source_match": {"netbox_id": self.bay.pk}}}

        mine, theirs = self._sides(named, addressed)

        self.assertIs(mine, named)
        self.assertEqual(theirs["name"], "cb-bay")

    def test_an_addressed_side_that_names_itself_is_left_alone(self):
        """Already comparable, so the lookup is not paid."""
        addressed = {"name": "given", "device": {"name": "cb-rtr"},
                     "metadata": {"source_match": {"netbox_id": self.bay.pk}}}
        named = {"name": "other"}

        with self.assertNumQueries(0):
            mine, theirs = self._sides(addressed, named)

        self.assertIs(mine, addressed)
        self.assertIs(theirs, named)

    def test_two_addressed_sides_are_left_alone(self):
        """The ids decide each other; neither needs resolving."""
        a = {"metadata": {"source_match": {"netbox_id": self.bay.pk}}}
        b = {"metadata": {"source_match": {"netbox_id": self.bay.pk + 1}}}

        with self.assertNumQueries(0):
            mine, theirs = self._sides(a, b)

        self.assertIs(mine, a)
        self.assertIs(theirs, b)

    def test_an_unknown_row_is_left_for_the_resolver_to_report(self):
        """
        A bad id is not this check's error to raise.

        _resolve_by_netbox_id already fails with a message about the id, and
        refusing here first would replace it with one about bay names.
        """
        missing = {"metadata": {"source_match": {"netbox_id": self.bay.pk + 10_000}}}
        named = {"name": "other"}

        mine, theirs = self._sides(missing, named)

        self.assertIs(mine, missing)
        self.assertFalse(transformer.references_conflict("dcim.modulebay", mine, theirs))


class DerivedIdentityCriteriaTests(TestCase):
    """
    The relation reads its criteria from the matchers, for any type.

    The table above proves the derivation reproduces what three hand-written
    comparisons decided for one shape. These assert the part that makes the
    replacement worth having: the same rules applied to types nobody wrote a
    comparison for, and the two asymmetric rules stated once.
    """

    def _conflict(self, object_type, a, b):
        first = transformer.references_conflict(object_type, a, b)
        self.assertEqual(
            first, transformer.references_conflict(object_type, b, a),
            "the relation is not symmetric")
        return first

    def test_a_type_nobody_wrote_a_comparison_for(self):
        """dcim.site was only ever compared by a hardcoded (name, slug) pair."""
        self.assertTrue(self._conflict("dcim.site", {"name": "s1"}, {"name": "s2"}))
        self.assertTrue(self._conflict("dcim.site", {"slug": "s1"}, {"slug": "s2"}))
        # unique on name alone, so equal names are one site
        self.assertFalse(self._conflict("dcim.site", {"name": "s"}, {"name": "s"}))
        # disjoint selectors: no criterion in common, so nothing is proved
        self.assertFalse(self._conflict("dcim.site", {"name": "s"}, {"slug": "s"}))

    def test_sameness_outranks_difference(self):
        """
        A satisfied unique constraint is the stronger statement.

        Site is unique on name alone AND on slug alone, so two payloads
        agreeing on the name are one site even where the slugs disagree -- that
        disagreement is a field conflict on that row for _merge_nodes to
        report, not evidence of a second row.
        """
        self.assertFalse(self._conflict(
            "dcim.site", {"name": "s", "slug": "x"}, {"name": "s", "slug": "y"}))

    def test_difference_needs_only_one_field(self):
        """
        Because a field is single-valued, not because it is unique.

        A device's name identifies nothing on its own -- Device is unique on
        (Lower(name), site, tenant) -- but a row still has one name, so two
        payloads disagreeing about it are two rows.
        """
        self.assertTrue(self._conflict(
            "dcim.device", {"name": "rtr-a"}, {"name": "rtr-b"}))

    def test_case_insensitivity_comes_from_the_constraint(self):
        """
        Lower(F(name)) in the constraint is why the name compares insensitively.

        Asserted with tenant on both sides so the (Lower(name), site, tenant)
        matcher is complete: it then reports SAME, which it can only do if the
        casing was folded. Without that, the differing name would be DIFFERENT
        and this pair would conflict.
        """
        scope = {"site": {"name": "s"}, "tenant": {"name": "t"}}
        self.assertFalse(self._conflict(
            "dcim.device", {"name": "RTR", **scope}, {"name": "rtr", **scope}))
        # and the same shape with a genuinely different name still conflicts
        self.assertTrue(self._conflict(
            "dcim.device", {"name": "rtr-a", **scope}, {"name": "rtr-b", **scope}))

    def test_a_conditional_constraint_does_not_prove_sameness(self):
        """
        dcim_device_unique_name_site applies only where tenant IS NULL.

        A payload silent about tenant has not said that, so its equality on
        (name, site) is not proof of one row -- two devices with that name and
        site can exist under different tenants. Left UNKNOWN, which refuses
        nothing, rather than read as SAME.
        """
        pair = {"name": "rtr", "site": {"name": "s"}}
        # equal on (name, site) and NOT declared the same object...
        self.assertFalse(self._conflict("dcim.device", dict(pair), dict(pair)))
        # ...which is visible here: a tenant disagreement still conflicts,
        # where a SAME verdict on (name, site) would have outranked it
        self.assertTrue(self._conflict(
            "dcim.device",
            {**pair, "tenant": {"name": "t1"}}, {**pair, "tenant": {"name": "t2"}}))

    def test_references_recurse_into_their_own_identity(self):
        """A device's site is compared as a site, not as text."""
        # same site named two ways -> no criterion in common -> not a conflict
        self.assertFalse(self._conflict(
            "dcim.device",
            {"name": "rtr", "site": {"name": "s"}},
            {"name": "rtr", "site": {"slug": "s"}}))
        # a site that is definitely another site -> conflict, from depth 2
        self.assertTrue(self._conflict(
            "dcim.device",
            {"name": "rtr", "site": {"name": "s1"}},
            {"name": "rtr", "site": {"name": "s2"}}))

    def test_identity_the_model_does_not_have_is_not_invented(self):
        """
        dcim.virtualchassis has no unique name, so two names prove nothing.

        This is the property that makes deriving safer than restating: the
        relation cannot claim identity the constraints do not give it, which is
        the whole reason VirtualChassis needed its own partition in the first
        place.
        """
        self.assertFalse(self._conflict(
            "dcim.virtualchassis", {"name": "stack-a"}, {"name": "stack-b"}))

    def test_an_unknown_type_refuses_nothing(self):
        """No criteria to read means no evidence, not an error."""
        self.assertFalse(self._conflict(
            "nosuch.type", {"name": "a"}, {"name": "b"}))

    def test_a_bare_int_is_not_read_as_a_primary_key(self):
        """Guessing that would be exactly the interpretation this must not do."""
        self.assertFalse(self._conflict("dcim.site", 1, 2))
