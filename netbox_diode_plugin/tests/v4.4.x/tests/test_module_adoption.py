"""E2E: module/module-bay ingest shapes — create adoption, and the installed_module reverse side."""
import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectChange
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site
from django.test import SimpleTestCase
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


class ReverseSideConflictRuleTests(SimpleTestCase):
    """
    The rule table for `_reverse_side_conflict`, without the pipeline.

    The relation answers one question -- can these two payloads name the same
    module bay? -- and it is a COMPATIBILITY relation, not equality: only a
    field both sides assert, with values that disagree, proves two bays. A
    field one side leaves out cannot contradict anything, which is what keeps
    the natural shape (the nested module naming its bay less fully than the
    outer payload does) from being refused.

    The identity being compared is taken from the model constraints rather than
    guessed: ModuleBay is unique on ('name', 'device'), Device on
    (Lower(name), site, tenant), and Site and Tenant are each unique on name
    alone and on slug alone, so the walk stops at their names.
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
                    transformer._reverse_side_conflict(mine, theirs), expected)
                # the relation must not depend on which side is which
                self.assertEqual(
                    transformer._reverse_side_conflict(theirs, mine), expected,
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
