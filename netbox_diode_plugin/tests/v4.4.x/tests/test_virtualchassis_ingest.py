"""E2E: VirtualChassis natural-shape ingest, convergence, and regressions."""
import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectChange
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import SimpleTestCase
from utilities.testing import APITestCase

from netbox_diode_plugin.api.applier import _coerce_pk
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

    def _diff_apply(self, payload, allow_empty=False):
        cs = self._diff(payload)
        if allow_empty and not cs.get("changes"):
            # Already fully converged: generate-diff legitimately returns an
            # empty changes list (this codebase's established idempotency
            # signal, see test_updates.py's post-create re-diff assertion),
            # and apply-change-set intentionally 400s on an empty changeset
            # (applier._validate_change_set: "Changes are required"). A real
            # reconciler client would not call apply here either -- skip it.
            return cs
        self.assertTrue(cs.get("changes"), cs)
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
        if members:
            self.assertEqual(vc.members.count(), len(members), members)
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
        cs = self._diff_apply(payload, allow_empty=True)
        self.assertEqual(cs.get("changes"), [], cs)
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

    def test_natural_shape_leaves_member_count_equal_to_actual_members(self):
        """
        The counter must not be double-incremented by the deferred device update.

        This is the PR's headline shape and it plans three changes: create the
        device (ref R), create the chassis mastered by R, then update R with its
        position. Between changes two and three NetBox's own
        dcim.signals.assign_virtualchassis_master has already written
        virtual_chassis onto the device ROW -- through a different Python object
        -- and utilities.counters has already counted that membership.

        Applying change three to the instance the CREATE returned therefore
        replays the assignment from a stale baseline:
        utilities.counters.post_save_receiver reads TrackingModelMixin's
        tracker, sees virtual_chassis_id go None -> chassis, and increments
        VirtualChassis.member_count a second time for the one membership that
        exists. member_count is not a field ingest diffs, so the re-diff below
        is empty and nothing ever repairs it -- the stored count stays one
        above the truth for the life of the row.

        The two assertions are deliberately paired: member_count on its own
        would also be satisfied by a chassis that ended up with two members.
        """
        payload = self._device_payload("vce-sw1", {
            "vc_position": 3, "vc_priority": 128,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        cs = self._diff_apply(payload)

        # Pin the shape: the third change is an UPDATE addressed by ref_id, so
        # it is the branch that reuses the CREATE's in-memory instance.
        deferred = [c for c in cs["changes"]
                    if c["object_type"] == "dcim.device" and c["change_type"] == "update"]
        self.assertEqual(len(deferred), 1, cs)
        self.assertIsNone(deferred[0]["object_id"], deferred[0])
        self.assertIsNotNone(deferred[0]["ref_id"], deferred[0])

        vc = self._assert_single_vc("vce-stack", master_name="vce-sw1",
                                   members={"vce-sw1": 3})
        self.assertEqual(vc.members.count(), 1)
        self.assertEqual(vc.member_count, 1, "stored member_count diverged from reality")

        # ...and it never self-heals, so the divergence would be permanent.
        self._assert_noop_rediff(payload)
        self.assertEqual(VirtualChassis.objects.get(pk=vc.pk).member_count, 1)

        # Re-reading must not cost the deferred update its changelog prechange
        # snapshot, and the snapshot must be of the ROW: the chassis assignment
        # NetBox's signal already made is what separates a fresh read from the
        # stale instance, which still had virtual_chassis unset.
        dev = Device.objects.get(name="vce-sw1")
        dev_change = ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="device",
            changed_object_id=dev.pk,
        ).order_by("pk").last()
        self.assertIsNotNone(dev_change, "the deferred update recorded no change at all")
        self.assertEqual(dev_change.action, "update", dev_change)
        self.assertIsNotNone(dev_change.prechange_data, dev_change)
        self.assertEqual(
            dev_change.prechange_data.get("virtual_chassis"), vc.pk,
            dev_change.prechange_data,
        )

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

    def test_chassis_move_into_position_taken_in_the_old_chassis(self):
        """
        A legal cross-chassis move whose target position is taken in the OLD chassis.

        vce-sw2 sits at position 2 of vce-stack-a, where position 3 belongs to
        vce-sw3; the payload promotes it to master of vce-stack-b at position
        3, which is free there. The position must travel WITH the chassis:
        asserting vc_position=3 on the device while it is still a member of
        vce-stack-a trips NetBox's (virtual_chassis, vc_position) uniqueness
        constraint -- "Device with this Virtual chassis and VC position
        already exists" -- and rejects a valid move.

        Naming the moving device as the new chassis's master is what makes the
        reproduction deterministic: it forces the VirtualChassis change to
        sort after the device change, so _handle_post_creates cannot merge the
        deferred step back into the device change (which would reunite chassis
        and position and mask the bug).
        """
        vc_a, _ = self._seed_stack(vc_name="vce-stack-a", master="vce-sw1")
        for name, position in (("vce-sw2", 2), ("vce-sw3", 3)):
            d = Device.objects.create(
                name=name, site=self.site, device_type=self.dt, role=self.role
            )
            Device.objects.filter(pk=d.pk).update(virtual_chassis=vc_a, vc_position=position)

        payload = self._device_payload("vce-sw2", {
            "vc_position": 3,
            "virtual_chassis": {
                "name": "vce-stack-b",
                "master": {"name": "vce-sw2", "site": {"name": "vce-site"}},
            },
        })
        self._diff_apply(payload)

        self._assert_single_vc("vce-stack-b", master_name="vce-sw2", members={"vce-sw2": 3})
        # the old chassis keeps its own occupant of that position
        stayed = Device.objects.get(name="vce-sw3")
        self.assertEqual(stayed.virtual_chassis_id, vc_a.pk)
        self.assertEqual(stayed.vc_position, 3)
        self._assert_noop_rediff(payload)

    def test_inline_master_position_rides_only_on_the_deferred_update(self):
        """
        Pin the split: the main device change carries no position, the deferred one does.

        NetBox's assign_virtualchassis_master signal forces the inline
        master's vc_position to 1 when the VirtualChassis row is created, so
        the submitted position can only survive on the deferred update that
        runs after it. A second copy on the main change buys nothing here (the
        signal overwrites it) and is exactly what rejects a cross-chassis
        move, so the position must appear on the deferred change ONLY.
        """
        payload = self._device_payload("vce-sw1", {
            "vc_position": 3, "vc_priority": 128,
            "virtual_chassis": {
                "name": "vce-stack",
                "master": {"name": "vce-sw1", "site": {"name": "vce-site"}},
            },
        })
        cs = self._diff(payload)
        device_changes = [c for c in cs.get("changes", []) if c["object_type"] == "dcim.device"]
        creates = [c for c in device_changes if c["change_type"] == "create"]
        deferred = [c for c in device_changes if c["change_type"] == "update"]
        self.assertEqual(len(creates), 1, cs)
        self.assertEqual(len(deferred), 1, cs)
        self.assertNotIn("vc_position", creates[0]["data"], creates[0])
        self.assertNotIn("vc_priority", creates[0]["data"], creates[0])
        self.assertIn("virtual_chassis", deferred[0]["data"], deferred[0])
        self.assertEqual(deferred[0]["data"]["vc_position"], 3, deferred[0])
        self.assertEqual(deferred[0]["data"]["vc_priority"], 128, deferred[0])

        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        dev = Device.objects.get(name="vce-sw1")
        self.assertEqual(dev.vc_position, 3)   # NOT the signal's forced 1
        self.assertEqual(dev.vc_priority, 128)
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

        Empirically probed: generate-diff happily
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
        Empirically confirmed at APPLY.
        """
        self._seed_stack()
        cs = self._diff(self._device_payload("vce-sw3", {"virtual_chassis": {"name": "vce-stack"}}))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        body = str(r.json())
        self.assertIn("position", body.lower(), body)
        self.assertFalse(Device.objects.filter(name="vce-sw3").exists())

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
            # Both orders are fully converged by now, so this third pass
            # legitimately plans empty: member-first no longer defers VC.master
            # for an ingest, because adoption attaches the chassis-less master
            # itself. It stays here as a re-ingest safety net.
            self._diff_apply(master_payload, allow_empty=True)
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

        # Pin the bulk request's own outcome: the member device lands on
        # that VC before any follow-up re-ingest happens.
        self.assertEqual(
            Device.objects.get(name=s2).virtual_chassis_id,
            VirtualChassis.objects.get(name=vc_name).pk,
        )

        # VC.master converges within the bulk request itself: adoption
        # attaches the chassis-less master to the row it adopts, so the
        # re-ingest below has nothing left to plan.
        self.assertEqual(VirtualChassis.objects.get(name=vc_name).master.name, m)
        self._diff_apply(master_payload, allow_empty=True)
        self._assert_single_vc(vc_name, master_name=m, members={m: 1, s2: 2})
        self._assert_noop_rediff(master_payload)
        self._assert_noop_rediff(member_payload)

    def test_standalone_vc_payload_adopts_masterless_row_and_attaches_master(self):
        """
        FIRST ingest must converge: adoption has to establish membership itself.

        A member-first ingest leaves a masterless VC; the standalone VC payload
        that names the master IS the whole payload, so there is no "later
        ingest" of it that would ever attach the master device. Dropping master
        here leaves the row masterless forever and re-plans the same CREATE on
        every pass.

        The master lands at position 1 -- the position NetBox itself assigns
        when it attaches a new chassis's master (dcim.signals.
        assign_virtualchassis_master) -- because the payload carries none.
        """
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        vc = VirtualChassis.objects.create(name="vce-stack")
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc, vc_position=2)
        Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        count_before = VirtualChassis.objects.get(pk=vc.pk).member_count

        payload = self._vc_payload("vce-stack", "vce-sw1")
        self._diff_apply(payload)

        adopted = self._assert_single_vc("vce-stack", master_name="vce-sw1",
                                        members={"vce-sw1": 1, "vce-sw2": 2})
        self.assertEqual(adopted.pk, vc.pk)  # adopted, not duplicated
        # The attach bumps member_count through a direct UPDATE the adopted
        # instance cannot see, so the adoption save must not write its stale
        # in-memory counter back over it. Asserted as a delta because the
        # queryset .update() seeding above never fires the counter signal.
        self.assertEqual(adopted.member_count, count_before + 1)
        # ...and the re-read must not cost the changelog its prechange snapshot.
        # member_count is what pins the ORDERING: the attach bumps it, so a
        # snapshot taken AFTER the attach would record count_before + 1. master
        # alone proves nothing here, because the attach never touches it -- that
        # assertion holds whichever side of the attach the snapshot is taken on.
        vc_change = ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="virtualchassis",
            changed_object_id=adopted.pk,
        ).latest("time")
        self.assertIsNotNone(vc_change.prechange_data, vc_change)
        self.assertIsNone(vc_change.prechange_data.get("master"), vc_change.prechange_data)
        self.assertEqual(
            vc_change.prechange_data.get("member_count"), count_before, vc_change.prechange_data
        )
        self.assertEqual(vc_change.postchange_data.get("master"), adopted.master_id)
        self._assert_noop_rediff(payload)

    def test_standalone_vc_payload_reingest_is_a_noop(self):
        """
        The property the drop-master path violated: identical re-ingest settles.

        Before adoption attached the master, every pass re-planned the same
        CREATE against a row that stayed masterless -- an ingest pipeline that
        never converges, which for a reconciler is the sharp end of it.
        """
        vc = VirtualChassis.objects.create(name="vce-stack")
        Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        payload = self._vc_payload("vce-stack", "vce-sw1")

        self._diff_apply(payload)
        first = self._assert_single_vc("vce-stack", master_name="vce-sw1",
                                      members={"vce-sw1": 1})
        self.assertEqual(first.pk, vc.pk)

        self._assert_noop_rediff(payload)
        self._diff_apply(payload, allow_empty=True)
        again = self._assert_single_vc("vce-stack", master_name="vce-sw1",
                                      members={"vce-sw1": 1})
        self.assertEqual(again.pk, vc.pk)

    def test_standalone_vc_adoption_takes_the_lowest_free_position(self):
        """
        The provisional position steps past positions the adopted row already uses.

        Positions 1 and 3 are taken, so the master gets 2 -- lowest free, not
        highest+1. It is provisional either way: the device's own payload
        asserts the real position, and Device.clean simply refuses a member
        without one, so adoption has to pick something.
        """
        vc = VirtualChassis.objects.create(name="vce-stack")
        for name, position in (("vce-sw2", 1), ("vce-sw3", 3)):
            d = Device.objects.create(
                name=name, site=self.site, device_type=self.dt, role=self.role
            )
            Device.objects.filter(pk=d.pk).update(virtual_chassis=vc, vc_position=position)
        Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )

        self._diff_apply(self._vc_payload("vce-stack", "vce-sw1"))
        self._assert_single_vc("vce-stack", master_name="vce-sw1",
                              members={"vce-sw1": 2, "vce-sw2": 1, "vce-sw3": 3})

    def test_standalone_vc_payload_will_not_move_master_out_of_another_chassis(self):
        """
        Adoption attaches a chassis-less device; it does not relocate one.

        Membership is asserted by the DEVICE payload (Device.virtual_chassis).
        A VC payload naming a master is not authority to pull that device out
        of a chassis it already belongs to -- and when the device is the other
        chassis's master NetBox refuses the move outright. So master stays
        unset and the deviation stays visible (the CREATE keeps re-planning)
        rather than being "converged" by a silent relocation.
        """
        other, _ = self._seed_stack(vc_name="vce-other", master="vce-sw9")
        member = Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=other, vc_position=2)
        vc = VirtualChassis.objects.create(name="vce-stack")

        self._diff_apply(self._vc_payload("vce-stack", "vce-sw1"))

        member.refresh_from_db()
        self.assertEqual(member.virtual_chassis_id, other.pk)  # not relocated
        self.assertEqual(member.vc_position, 2)
        vc.refresh_from_db()
        self.assertIsNone(vc.master_id)
        self.assertEqual(vc.members.count(), 0)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)

    def _plan_vc_create_then_delete_master(self, vc_name, master_name):
        """Plan a master-bearing VC CREATE against an adoptable row, then delete the master."""
        vc = VirtualChassis.objects.create(name=vc_name)
        master = Device.objects.create(
            name=master_name, site=self.site, device_type=self.dt, role=self.role
        )
        cs = self._diff(self._vc_payload(vc_name, master_name))
        master.delete()
        return vc, cs

    def test_master_deleted_between_plan_and_apply_is_rejected(self):
        """
        A planned master pk with no device behind it must fail the apply.

        Plan-then-apply is two round trips, so nothing stops the master
        resolved at plan time from being deleted before the apply lands. The
        payload then references an object that is not there, and adoption must
        not "converge" that by dropping master: the CREATE would be reported as
        successfully applied, the chassis would be saved masterless, and the
        dangling reference -- a hard serializer error on every other applied
        change -- would leave no trace at all.
        """
        vc, cs = self._plan_vc_create_then_delete_master("vce-stack", "vce-sw1")

        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, (cs, r.content))
        errors = r.json()["errors"]
        # NetBox's own dangling-reference error, on the field that carried it
        self.assertIn("dcim.virtualchassis", errors, errors)
        self.assertIn("master", errors["dcim.virtualchassis"], errors)

        # The rejected apply leaves the adoptable row exactly as it was.
        #
        # These three alone would ALSO have held under the silent success this
        # test exists to catch: the adoptable row was already masterless with no
        # members, and the payload carried nothing but name and master, so
        # "unchanged" and "wrongly saved without its master" look identical in
        # those fields. The status assertion above was carrying the whole test.
        # last_updated is what separates them -- a save would have moved it --
        # and the absence of an ObjectChange proves the row was never written.
        before = vc.last_updated
        vc.refresh_from_db()
        self.assertIsNone(vc.master_id)
        self.assertEqual(vc.members.count(), 0)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)
        self.assertEqual(vc.last_updated, before, "the rejected apply saved the chassis anyway")
        self.assertFalse(
            ObjectChange.objects.filter(
                changed_object_type__app_label="dcim",
                changed_object_type__model="virtualchassis",
                changed_object_id=vc.pk,
            ).exists(),
            "a rolled-back apply must leave no changelog entry for the chassis",
        )

    def test_missing_master_and_deferred_master_are_not_the_same_outcome(self):
        """
        Adoption's two "cannot attach" cases must not collapse into one branch.

        Both halves run here together because the defect they guard is exactly
        the two sharing an outcome:

        - the named master belongs to a DIFFERENT chassis: a deliberate defer.
          The apply succeeds with master dropped, because a VC payload is not
          authority to relocate a device, and the device's own payload will
          converge it.
        - the named master DOES NOT EXIST: a dangling reference. The apply must
          fail, because nothing later can converge a pk that resolves to
          nothing.

        Handling them alike sacrifices one property or the other: a shared drop
        turns a dangling reference into a silent success, a shared raise turns a
        legitimate defer into a failed ingest.
        """
        # deferred: the master is real, but it lives in another chassis
        other, _ = self._seed_stack(vc_name="vce-other", master="vce-sw9")
        held = Device.objects.create(
            name="vce-held", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=held.pk).update(virtual_chassis=other, vc_position=2)
        deferred_vc = VirtualChassis.objects.create(name="vce-deferred")

        cs = self._diff(self._vc_payload("vce-deferred", "vce-held"))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        deferred_vc.refresh_from_db()
        self.assertIsNone(deferred_vc.master_id)
        held.refresh_from_db()
        self.assertEqual(held.virtual_chassis_id, other.pk)

        # missing: same inability to attach, opposite outcome
        missing_vc, cs = self._plan_vc_create_then_delete_master("vce-missing", "vce-ghost")
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, (cs, r.content))
        missing_vc.refresh_from_db()
        self.assertIsNone(missing_vc.master_id)

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
        self.assertEqual(r.status_code, 400, r.content)
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

    # ---- direct apply-change-set: master arrives exactly as posted ---------

    def _vc_create_changeset(self, name, master):
        """A minimal apply-change-set body carrying master exactly as given."""
        return {
            "id": str(uuid.uuid4()),
            "changes": [{
                "change_id": str(uuid.uuid4()),
                "change_type": "create",
                "object_version": None,
                "object_type": "dcim.virtualchassis",
                "object_id": None,
                "ref_id": "1",
                "data": {"name": name, "master": master},
            }],
        }

    def _seed_adoptable(self, vc_name="vce-stack", master_name="vce-sw1"):
        """A masterless VC to adopt, plus a chassis-less device to become its master."""
        vc = VirtualChassis.objects.create(name=vc_name)
        master = Device.objects.create(
            name=master_name, site=self.site, device_type=self.dt, role=self.role
        )
        return vc, master

    def test_direct_apply_integer_master_adopts(self):
        """The well-formed direct-POST shape: an int pk adopts the masterless row."""
        vc, master = self._seed_adoptable()

        r = self.client.post(self.apply_url, data=self._vc_create_changeset("vce-stack", master.pk),
                             format="json", **self.auth)

        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        adopted = self._assert_single_vc("vce-stack", master_name="vce-sw1", members={"vce-sw1": 1})
        self.assertEqual(adopted.pk, vc.pk)

    def test_direct_apply_numeric_string_master_adopts(self):
        """
        A numeric-string pk is a legal wire form and must keep adopting.

        The malformed-value guard has to COERCE this, not decline it: declining
        falls through to the create path, which would leave a second chassis of
        the same name beside the masterless row this is supposed to bind.
        """
        vc, master = self._seed_adoptable()

        r = self.client.post(self.apply_url,
                             data=self._vc_create_changeset("vce-stack", str(master.pk)),
                             format="json", **self.auth)

        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        adopted = self._assert_single_vc("vce-stack", master_name="vce-sw1", members={"vce-sw1": 1})
        self.assertEqual(adopted.pk, vc.pk)

    def test_direct_apply_malformed_master_is_a_structured_400(self):
        """
        A non-numeric master must be reported as an error, not crash the apply.

        The plan path always resolves master to a pk, so this shape reaches the
        applier only through a direct POST to apply-change-set (or bulk-apply)
        -- a first-class entry point, and nothing between the wire and the
        applier coerces the field: ChangeSet.validate pops relation fields
        before instantiating the model. Carried into an ORM filter it raises
        ValueError, which apply_changeset's handler chain does not catch
        (ValidationError, ObjectDoesNotExist, TypeError, IntegrityError,
        KeyError), so it escapes as a 500 -- burying the structured 400 the VC
        serializer already produces for this exact value.

        TWO lookups on this path carried it into the ORM: the adoption pass,
        and the match lookup _create_or_find_instance falls back to once the
        serializer has rejected the payload. Either one alone still 500s, so
        this test goes green only when both decline a malformed reference.
        """
        vc, _ = self._seed_adoptable()
        before = vc.last_updated

        r = self.client.post(self.apply_url, data=self._vc_create_changeset("vce-stack", "abc"),
                             format="json", **self.auth)

        self.assertEqual(r.status_code, 400, r.content)
        errors = r.json()["errors"]
        self.assertIn("dcim.virtualchassis", errors, errors)
        self.assertIn("master", errors["dcim.virtualchassis"], errors)

        # Declining the adoption must not half-apply it either. On a row that
        # was already masterless and memberless, "untouched" and "saved without
        # its master" look identical in those two fields -- last_updated is
        # what separates them, so it carries the no-write assertion.
        vc.refresh_from_db()
        self.assertIsNone(vc.master_id)
        self.assertEqual(vc.members.count(), 0)
        self.assertEqual(vc.last_updated, before, "the rejected apply saved the chassis anyway")
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)


    def test_direct_apply_master_bearing_create_never_rewrites_another_chassis(self):
        """
        A CREATE must not be able to rename or otherwise write an existing chassis.

        The pre-save match (matcher._REQUIRES_PRE_SAVE_MATCH) is an UPDATE
        path: it applies the CREATE's payload to whatever find_existing_object
        returns. With master present the matcher that answers is the
        auto-derived unique_master one, so routing a master-bearing CREATE
        through it means "create the chassis named NEW, mastered by D" is
        applied to whichever chassis D already masters -- an unrelated,
        already-converged row, renamed by a create. Scoping the routing to
        MASTERLESS payloads is what makes that unreachable, and it also puts
        master beyond the reach of an ORM pk filter, where the malformed forms
        below coerce silently rather than declining (True -> pk 1, 7.5 -> pk 7,
        7.0 -> pk 7).

        The invariant is asserted over every pre-existing row rather than over
        the outcome of the apply, because the outcome legitimately varies with
        the value: what must never vary is that no row that existed before the
        request comes out of it changed.
        """
        vc, master = self._seed_stack(vc_name="vce-owned", master="vce-sw1")

        for label, master_value in (
            ("well_formed_pk", master.pk),
            ("numeric_string", str(master.pk)),
            ("bool_true", True),
            ("bool_false", False),
            ("non_integral_float", 7.5),
            ("integral_float", 7.0),
        ):
            with self.subTest(master=label):
                before = {
                    row.pk: (row.name, row.description, row.master_id, row.last_updated)
                    for row in VirtualChassis.objects.all()
                }
                r = self.client.post(
                    self.apply_url,
                    data=self._vc_create_changeset("vce-renamed", master_value),
                    format="json", **self.auth,
                )
                self.assertIn(r.status_code, (200, 400), r.content)
                after = {
                    row.pk: (row.name, row.description, row.master_id, row.last_updated)
                    for row in VirtualChassis.objects.filter(pk__in=before)
                }
                self.assertEqual(after, before, f"{label}: a pre-existing chassis was written")
                self.assertEqual(
                    VirtualChassis.objects.get(pk=vc.pk).name, "vce-owned",
                    f"{label}: the chassis this master already owns was renamed",
                )


class CoercePkTests(SimpleTestCase):
    """
    What may reach an ORM pk filter, for every shape a payload FK can carry.

    Declining is never merely defensive: an unusable value passed through
    raises ValueError/TypeError out of the ORM, and apply_changeset does not
    catch either, so it lands as a 500 instead of the serializer's 400.
    """

    def test_usable_pk_forms_are_coerced(self):
        """A resolved instance, an int, and a numeric string all yield the pk."""
        self.assertEqual(_coerce_pk(SimpleNamespace(pk=7)), 7)
        self.assertEqual(_coerce_pk(7), 7)
        self.assertEqual(_coerce_pk("7"), 7)

    def test_unusable_pk_forms_are_declined(self):
        """Nothing the ORM would choke on gets through -- including an unsaved instance."""
        for value in (None, "", "abc", "7.5", 7.5, [7], (7,), {"pk": 7}, SimpleNamespace(pk=None)):
            with self.subTest(value=value):
                self.assertIsNone(_coerce_pk(value))

    def test_bool_is_not_a_pk(self):
        """A bool is an int subclass, so True would otherwise silently mean pk 1."""
        self.assertIsNone(_coerce_pk(True))
        self.assertIsNone(_coerce_pk(False))
