"""E2E: VirtualChassis natural-shape ingest, convergence, and regressions."""
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class VirtualChassisIngestE2ETests(APITestCase):
    """Natural VC shapes must converge to one VC with master and members."""

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.bulk_plan_apply_url = "/netbox/api/plugins/diode/bulk-plan-apply/"
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

        self.site = Site.objects.create(name="vce-site", slug="vce-site")
        mfr = Manufacturer.objects.create(name="vce-mfr", slug="vce-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="vce-dt", slug="vce-dt")
        self.role = DeviceRole.objects.create(name="vce-role", slug="vce-role")

    # ---- payload builders -------------------------------------------------

    def _device_payload(self, name, extra=None, asset_tag=None):
        entity = {
            "name": name,
            "site": {"name": "vce-site"},
            "role": {"name": "vce-role"},
            "device_type": {"manufacturer": {"name": "vce-mfr"}, "model": "vce-dt"},
        }
        if asset_tag:
            entity["asset_tag"] = asset_tag
        entity.update(extra or {})
        return {"timestamp": 1, "object_type": "dcim.device", "entity": {"device": entity}}

    def _vc_payload(self, name, master_name):
        return {"timestamp": 1, "object_type": "dcim.virtualchassis", "entity": {"virtual_chassis": {
            "name": name,
            "master": {"name": master_name, "site": {"name": "vce-site"}},
        }}}

    def _diff(self, payload):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json().get("change_set", {})

    def _diff_apply(self, payload):
        cs = self._diff(payload)
        if not cs.get("changes"):
            # Already fully converged: generate-diff legitimately returns an
            # empty changes list (this codebase's established idempotency
            # signal, see test_updates.py's post-create re-diff assertion),
            # and apply-change-set intentionally 400s on an empty changeset
            # (applier._validate_change_set: "Changes are required"). A real
            # reconciler client would not call apply here either -- skip it.
            return cs
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        return cs

    def _assert_noop_rediff(self, payload):
        cs = self._diff(payload)
        non_noop = [c for c in cs.get("changes", []) if c["change_type"] != "noop"]
        self.assertEqual(non_noop, [], non_noop)

    def _assert_single_vc(self, name, master_name=None, members=None):
        vcs = VirtualChassis.objects.filter(name=name)
        self.assertEqual(vcs.count(), 1)
        vc = vcs.first()
        if master_name is not None:
            self.assertIsNotNone(vc.master, "master not set")
            self.assertEqual(vc.master.name, master_name)
        for member_name, position in (members or {}).items():
            d = Device.objects.get(name=member_name)
            self.assertEqual(d.virtual_chassis_id, vc.pk, member_name)
            self.assertEqual(d.vc_position, position, member_name)
        return vc

    # ---- scenarios --------------------------------------------------------

    def _seed_stack(self, vc_name="vce-stack", master="vce-sw1", position=1):
        """ORM-seed a converged one-member stack (master at position)."""
        dev = Device.objects.create(
            name=master, site=self.site, device_type=self.dt, role=self.role
        )
        vc = VirtualChassis.objects.create(name=vc_name, master=dev)
        Device.objects.filter(pk=dev.pk).update(virtual_chassis=vc, vc_position=position)
        return vc, dev

    def test_member_reingest_name_only_binds_existing_vc(self):
        """The silent-duplicate generator: name-only VC ref must not duplicate."""
        vc, _ = self._seed_stack()
        payload = self._device_payload("vce-sw2", {
            "vc_position": 2, "virtual_chassis": {"name": "vce-stack"},
        })
        self._diff_apply(payload)
        self._assert_single_vc("vce-stack", master_name="vce-sw1",
                               members={"vce-sw2": 2, "vce-sw1": 1})
        self._assert_noop_rediff(payload)

    def test_master_reingest_natural_shape_idempotent(self):
        """A master carrying its own VC ref: no cycle, no detach error."""
        self._seed_stack()
        payload = self._device_payload("vce-sw1", {
            "vc_position": 1,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        self._diff_apply(payload)
        self._assert_single_vc("vce-stack", master_name="vce-sw1", members={"vce-sw1": 1})
        self._assert_noop_rediff(payload)

    def test_fresh_stack_single_batch_master_position_and_priority(self):
        """First ingest of a natural master with position != 1 and priority."""
        payload = self._device_payload("vce-sw1", {
            "vc_position": 3, "vc_priority": 128,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        self._diff_apply(payload)
        vc = self._assert_single_vc("vce-stack", master_name="vce-sw1", members={"vce-sw1": 3})
        self.assertEqual(vc.master.vc_priority, 128)
        self._assert_noop_rediff(payload)

    def test_custom_position_on_preexisting_master_device(self):
        """The signal-clobber case: pre-existing device, position must survive ingest 1."""
        Device.objects.create(name="vce-sw1", site=self.site, device_type=self.dt, role=self.role)
        payload = self._device_payload("vce-sw1", {
            "vc_position": 2, "vc_priority": 255,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        self._diff_apply(payload)
        dev = Device.objects.get(name="vce-sw1")
        self.assertEqual(dev.vc_position, 2)   # NOT the signal's forced 1
        self.assertEqual(dev.vc_priority, 255)
        self._assert_noop_rediff(payload)

    def test_master_rename_via_asset_tag_natural_shape(self):
        """Master renamed while matched by asset_tag; natural shape must converge."""
        vc, dev = self._seed_stack()
        Device.objects.filter(pk=dev.pk).update(asset_tag="vce-at-1")
        payload = self._device_payload("vce-sw1-renamed", {
            "vc_position": 1,
            "virtual_chassis": {"name": "vce-stack"},
        }, asset_tag="vce-at-1")
        self._diff_apply(payload)
        self._assert_single_vc("vce-stack", master_name="vce-sw1-renamed",
                               members={"vce-sw1-renamed": 1})
        self._assert_noop_rediff(payload)

    def test_duplicate_field_state_member_migrates_back(self):
        """Bug aftermath: member sits in a NEWER empty duplicate; must migrate back."""
        vc_old, master = self._seed_stack()
        vc_dup = VirtualChassis.objects.create(name="vce-stack")
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc_dup, vc_position=2)

        payload = self._device_payload("vce-sw2", {
            "vc_position": 2, "virtual_chassis": {"name": "vce-stack"},
        })
        self._diff_apply(payload)
        member.refresh_from_db()
        self.assertEqual(member.virtual_chassis_id, vc_old.pk)  # oldest wins
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 2)
        self._assert_noop_rediff(payload)

    def test_workaround_shape_still_resolves(self):
        """Orb's shipped shape: plain master, top-level VC, member with inline VC."""
        self._diff_apply(self._device_payload("vce-sw1"))
        self._diff_apply(self._vc_payload("vce-stack", "vce-sw1"))
        payload = self._device_payload("vce-sw2", {
            "vc_position": 2,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        self._diff_apply(payload)
        self._assert_single_vc("vce-stack", master_name="vce-sw1",
                               members={"vce-sw1": 1, "vce-sw2": 2})
        self._assert_noop_rediff(payload)

    def test_vc_detach_member_clears_and_master_deviates(self):
        """
        virtual_chassis: {} detaches a member; a master detach is a deviation.

        Empirically probed (see task-4-report.md): generate-diff happily
        plans the master's virtual_chassis: null UPDATE -- NetBox's
        Device.clean() master-detach guard only runs at save() time, so
        the rejection surfaces as a per-entity APPLY error, not at plan.
        """
        vc, master = self._seed_stack()
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc, vc_position=2)

        cs = self._diff(self._device_payload("vce-sw2", {"virtual_chassis": {}}))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        member.refresh_from_db()
        self.assertIsNone(member.virtual_chassis_id)

        # Plan-time: NetBox validation for master-detach only runs at save(),
        # so generate-diff succeeds and plans a plain UPDATE.
        cs2 = self._diff(self._device_payload("vce-sw1", {"virtual_chassis": {}}))
        device_changes = [c for c in cs2.get("changes", []) if c["object_type"] == "dcim.device"]
        self.assertTrue(device_changes, cs2)
        self.assertEqual(device_changes[0]["change_type"], "update")

        # Apply-time: NetBox's Device.clean() rejects the master detach as a
        # per-entity error.
        r2 = self.client.post(self.apply_url, data=cs2, format="json", **self.auth)
        self.assertEqual(r2.status_code, 400, r2.content)
        self.assertIn("virtual chassis", str(r2.json()).lower())
        master.refresh_from_db()
        self.assertEqual(master.virtual_chassis_id, vc.pk)  # rejected apply leaves DB untouched

    def test_positionless_member_yields_position_deviation(self):
        """
        A VC ref without vc_position surfaces a position error at APPLY, not a 500.

        Plan-time validate runs clean_fields only; Device.clean's
        position-required rule fires in the serializer during apply.
        Empirically confirmed at APPLY, see task-4-report.md.
        """
        self._seed_stack()
        cs = self._diff(self._device_payload("vce-sw3", {"virtual_chassis": {"name": "vce-stack"}}))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        body = str(r.json())
        self.assertIn("position", body.lower(), body)

    def test_fresh_stack_two_requests_both_orders_converge(self):
        """
        Master-first and member-first request orders both end at ONE VC.

        Member-first exercises the adoption fallback end-to-end: the member's
        name-only ref creates a masterless VC, and the master's later
        master-bearing node must adopt it instead of duplicating.
        """
        for order, (vc_name, m, s2) in {
            "master_first": ("vce-ord1", "vce-m1", "vce-n1"),
            "member_first": ("vce-ord2", "vce-m2", "vce-n2"),
        }.items():
            master_payload = self._device_payload(m, {
                "vc_position": 1,
                "virtual_chassis": {
                    "name": vc_name,
                    "master": {"name": m, "site": {"name": "vce-site"}},
                },
            })
            member_payload = self._device_payload(s2, {
                "vc_position": 2, "virtual_chassis": {"name": vc_name},
            })
            first, second = (
                (master_payload, member_payload) if order == "master_first"
                else (member_payload, master_payload)
            )
            self._diff_apply(first)
            self._diff_apply(second)
            # member-first defers VC.master one ingest (adoption drops it until
            # the master device is a member); a converging re-ingest sets it
            self._diff_apply(master_payload)
            self._assert_single_vc(vc_name, master_name=m, members={m: 1, s2: 2})
            self._assert_noop_rediff(master_payload)
            self._assert_noop_rediff(member_payload)

    def test_bulk_plan_apply_member_first_ordering_converges(self):
        """
        Member-first ordering within ONE bulk-plan-apply request.

        /bulk-plan-apply/ plans-then-applies each entity in the submitted
        order, sharing the request-scoped obj/prechange caches across
        entities (see BulkPlanApplyView docstring in api/views.py). This
        makes it a real single-request analogue of the two-request
        member-first case above: entity 1 (member, name-only VC ref) applies
        before entity 2 (master, master-bearing VC ref) is even planned.
        """
        vc_name, m, s2 = "vce-bulk1", "vce-bm1", "vce-bn1"
        member_payload = self._device_payload(s2, {
            "vc_position": 2, "virtual_chassis": {"name": vc_name},
        })
        master_payload = self._device_payload(m, {
            "vc_position": 1,
            "virtual_chassis": {
                "name": vc_name,
                "master": {"name": m, "site": {"name": "vce-site"}},
            },
        })
        bulk_payload = {
            "entities": [
                {"id": "member", "object_type": "dcim.device", "entity": member_payload["entity"]},
                {"id": "master", "object_type": "dcim.device", "entity": master_payload["entity"]},
            ]
        }
        r = self.client.post(self.bulk_plan_apply_url, data=bulk_payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        results = {res["id"]: res for res in r.json()["results"]}
        self.assertIsNone(results["member"].get("errors"), results["member"])
        self.assertIsNone(results["master"].get("errors"), results["master"])

        # Adoption fallback must not duplicate the masterless VC the member's
        # plan created; exactly one VC row exists after the bulk request.
        self.assertEqual(VirtualChassis.objects.filter(name=vc_name).count(), 1)

        # As in the two-request case, VC.master converges on a later,
        # identical re-ingest of the master payload (deferred-master
        # adoption contract), not necessarily within the bulk request itself.
        self._diff_apply(master_payload)
        self._assert_single_vc(vc_name, master_name=m, members={m: 1, s2: 2})
        self._assert_noop_rediff(master_payload)
        self._assert_noop_rediff(member_payload)

    def test_name_only_master_stub_yields_required_fields_deviation(self):
        """A VC whose inline master stub lacks matcher keys fails loudly, not silently."""
        r = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.virtualchassis",
                  "entity": {"virtual_chassis": {
                      "name": "vce-badstub",
                      "master": {"name": "vce-ghost"},
                  }}},
            format="json", **self.auth,
        )
        # the standalone device CREATE lacks device_type/role/site
        self.assertIn("required", str(r.json()).lower())

    def test_explicit_master_null_plans_master_clearing_update(self):
        """Pin the clear-FK contract: name-matched VC with master:{} plans master=None."""
        self._seed_stack()
        cs = self._diff({
            "timestamp": 1, "object_type": "dcim.virtualchassis",
            "entity": {"virtual_chassis": {"name": "vce-stack", "master": {}}},
        })
        updates = [c for c in cs.get("changes", [])
                   if c["object_type"] == "dcim.virtualchassis"
                   and "master" in (c.get("data") or {})]
        self.assertTrue(updates, cs)
        self.assertIsNone(updates[0]["data"]["master"])
