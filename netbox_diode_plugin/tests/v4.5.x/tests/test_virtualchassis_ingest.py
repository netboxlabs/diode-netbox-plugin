"""E2E: VirtualChassis natural-shape ingest, convergence, and regressions."""
import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectChange
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import SimpleTestCase
from utilities.testing import APITestCase

from netbox_diode_plugin.api import matcher
from netbox_diode_plugin.api.applier import _coerce_pk
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.matcher import _PRE_SAVE_MATCH_BIND_ONLY
from netbox_diode_plugin.api.transformer import _IS_CIRCULAR_REFERENCE
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

    def _vc_payload(self, name, master_name, domain=None):
        vc = {
            "name": name,
            "master": {"name": master_name, "site": {"name": "vce-site"}},
        }
        if domain is not None:
            vc["domain"] = domain
        return {"timestamp": 1, "object_type": "dcim.virtualchassis",
                "entity": {"virtual_chassis": vc}}

    def _seed_named_stack(self, vc_name, domain, members):
        """
        ORM-seed a converged stack: ``members`` is {device_name: position}, first is master.

        Two of these with the SAME name and different domains are the review's
        scenario: two legitimately distinct stacks that a name cannot tell apart.
        """
        devices = {}
        vc = VirtualChassis.objects.create(name=vc_name, domain=domain)
        for name, position in members.items():
            d = Device.objects.create(
                name=name, site=self.site, device_type=self.dt, role=self.role
            )
            Device.objects.filter(pk=d.pk).update(virtual_chassis=vc, vc_position=position)
            devices[name] = d
        vc.refresh_from_db()
        vc.master = devices[next(iter(members))]
        vc.save()
        return vc, devices

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

    def _assert_split_vc(self, name, master_name, mastered_members, orphan_members):
        """
        Two same-named rows: the one this payload made, and the one it declined.

        A populated row matched on the name alone is not adopted, so a
        member-first pass leaves the member's masterless row where it is and the
        master-bearing pass gets its own. Both halves are asserted, because the
        property that makes the split shippable is that NOTHING MOVED: the row
        the payload did not identify has to be byte-identical afterwards.
        """
        rows = VirtualChassis.objects.filter(name=name)
        self.assertEqual(rows.count(), 2, list(rows.values_list("pk", "master_id")))
        mastered = rows.exclude(master__isnull=True)
        self.assertEqual(mastered.count(), 1, "no row ended up mastered")
        mastered = mastered.first()
        self.assertEqual(mastered.master.name, master_name)
        self.assertEqual(
            dict(mastered.members.values_list("name", "vc_position")), mastered_members)
        orphan = rows.filter(master__isnull=True).first()
        self.assertIsNone(orphan.master_id, "the declined row acquired a master")
        self.assertEqual(
            dict(orphan.members.values_list("name", "vc_position")), orphan_members,
            "the declined row's membership changed")
        return mastered, orphan

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

        # Re-reading the row must not change what the deferred update records.
        # A fresh read makes a prechange snapshot cheap to take, and taking one
        # would alter the changelog in two ways at once -- adding
        # prechange_data, and (through NetBox's ObjectChange.has_changes gate)
        # deciding whether the row is recorded at all. Neither belongs in a
        # counter fix, so the shipped behaviour is the one from before the
        # re-read: an 'update' row, with no prechange.
        # test_module_adoption pins the same branch on the shape where that
        # gate actually drops the row.
        dev = Device.objects.get(name="vce-sw1")
        dev_change = ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="device",
            changed_object_id=dev.pk,
        ).order_by("pk").last()
        self.assertIsNotNone(dev_change, "the deferred update recorded no change at all")
        self.assertEqual(dev_change.action, "update", dev_change)
        self.assertIsNone(dev_change.prechange_data, dev_change)
        self.assertEqual(
            dev_change.postchange_data.get("virtual_chassis"), vc.pk,
            dev_change.postchange_data,
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

    def test_duplicate_field_state_member_keeps_the_chassis_it_is_in(self):
        """
        Bug aftermath: the member sits in a duplicate, and ingest must NOT move it.

        THE OLD EXPECTATION HERE WAS UNSAFE and this test used to assert it:
        "vce-sw2 migrates back to the oldest same-named chassis". It passed
        because the name matcher resolved a name-only reference with
        order_by('pk').first(), so a name that matches two rows always chose the
        older one -- and since this reference is a MEMBER DEVICE's
        virtual_chassis, choosing meant RELOCATING that device. Determinism is
        not identity: run the same shape with two legitimately distinct stacks
        sharing a name (see the ambiguity tests below) and the identical rule
        moves a device into another building's stack.

        The rule now is "prefer the chassis this device already belongs to",
        which is the one preference the database itself answers, so nothing
        moves and the second pass plans nothing at all. Recovering from
        duplicates is still possible where the evidence is real -- see
        test_empty_duplicate_loses_to_the_populated_chassis, where the member is
        NOT already placed -- but a name is not evidence enough to move a device
        that is.
        """
        vc_old, master = self._seed_stack()
        vc_dup = VirtualChassis.objects.create(name="vce-stack")
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc_dup, vc_position=2)

        payload = self._device_payload("vce-sw2", {
            "vc_position": 2, "virtual_chassis": {"name": "vce-stack"},
        })
        # Fully converged already: the device is where the payload says it is.
        self.assertEqual(self._diff(payload).get("changes"), [])
        member.refresh_from_db()
        self.assertEqual(member.virtual_chassis_id, vc_dup.pk, "the member was relocated")
        self.assertEqual(member.vc_position, 2)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 2)
        self._assert_noop_rediff(payload)

    def test_empty_duplicate_loses_to_the_populated_chassis(self):
        """
        Recovery that IS safe: an empty same-named row is not a stack anyone owns.

        A bug-created duplicate is empty and masterless; the real chassis has a
        master and members. A new member's name-only reference resolves to the
        real one -- not because it is older (it is, and that is irrelevant: the
        assertions below hold for the POPULATED row whichever order the rows
        were created in) but because exactly one candidate is a stack at all.
        No device is moved: vce-sw2 is not in either row to begin with.
        """
        vc_dup = VirtualChassis.objects.create(name="vce-stack")  # created FIRST, empty
        vc_real, master = self._seed_stack()                      # newer, populated
        self.assertLess(vc_dup.pk, vc_real.pk, "the empty duplicate must be the older row")

        payload = self._device_payload("vce-sw2", {
            "vc_position": 2, "virtual_chassis": {"name": "vce-stack"},
        })
        self._diff_apply(payload)
        member = Device.objects.get(name="vce-sw2")
        self.assertEqual(member.virtual_chassis_id, vc_real.pk)
        self.assertEqual(vc_dup.members.count(), 0)
        self._assert_noop_rediff(payload)

    def test_two_same_named_stacks_make_a_member_reference_ambiguous(self):
        """
        The review's scenario, and the failure mode this whole policy exists for.

        Two legitimately distinct stacks are both called "vce-shared": one in
        building-a with vce-a1/vce-a2, one in building-b with vce-b1/vce-b2.
        Ingesting a new member vce-b3 whose virtual_chassis reference carries
        the name and nothing else USED to bind the older row and place vce-b3
        into the building-A stack -- silently, with no later diff mentioning it.

        It must now fail, at PLAN time, with an error that names both rows and
        says what would resolve them. Failing at plan is what makes it a
        deviation the producer sees rather than a write it has to detect
        afterwards.
        """
        vc_a, _ = self._seed_named_stack(
            "vce-shared", "building-a", {"vce-a1": 1, "vce-a2": 2})
        vc_b, _ = self._seed_named_stack(
            "vce-shared", "building-b", {"vce-b1": 1, "vce-b2": 2})
        self.assertLess(vc_a.pk, vc_b.pk)

        r = self.client.post(
            self.diff_url,
            data=self._device_payload("vce-b3", {
                "vc_position": 3, "virtual_chassis": {"name": "vce-shared"},
            }),
            format="json", **self.auth,
        )
        self.assertEqual(r.status_code, 400, r.content)
        error = r.json()["errors"]["dcim.virtualchassis"]["name"][0]
        self.assertIn("vce-shared", error)
        self.assertIn(f"id {vc_a.pk}", error)
        self.assertIn(f"id {vc_b.pk}", error)
        # The message names domain, but only as what it is for a payload that
        # asserts none: half of a two-part fix (the producer sends it, the row
        # carries it). These two rows already carry different domains, so
        # labelling alone cannot resolve this -- see
        # test_the_remedy_the_refusal_names_actually_resolves_it.
        self.assertIn("domain", error)
        self.assertIn("labelling these rows cannot settle it on its own", error)

        # nothing planned means nothing applied: no device, no membership change
        self.assertFalse(Device.objects.filter(name="vce-b3").exists())
        self.assertEqual(vc_a.members.count(), 2)
        self.assertEqual(vc_b.members.count(), 2)

    def test_domain_resolves_a_reference_two_stacks_would_otherwise_share(self):
        """
        The discriminator does the work the name cannot: vce-b3 lands in building-b.

        Same two stacks as above. The only difference is that the member's
        virtual_chassis reference carries domain, which is a claim about WHICH
        row rather than a guess between rows -- so it resolves, and it resolves
        to the row the producer named, not the older one.
        """
        vc_a, _ = self._seed_named_stack(
            "vce-shared", "building-a", {"vce-a1": 1, "vce-a2": 2})
        vc_b, _ = self._seed_named_stack(
            "vce-shared", "building-b", {"vce-b1": 1, "vce-b2": 2})

        payload = self._device_payload("vce-b3", {
            "vc_position": 3,
            "virtual_chassis": {"name": "vce-shared", "domain": "building-b"},
        })
        self._diff_apply(payload)

        member = Device.objects.get(name="vce-b3")
        self.assertEqual(member.virtual_chassis_id, vc_b.pk)
        self.assertEqual(member.vc_position, 3)
        self.assertEqual(vc_a.members.count(), 2, "the building-A stack was touched")
        vc_a.refresh_from_db()
        self.assertEqual(vc_a.domain, "building-a", "the building-A domain was overwritten")
        self._assert_noop_rediff(payload)

    def test_an_existing_member_keeps_its_own_stack_when_the_name_is_shared(self):
        """
        The member's own membership outranks everything, including the older row.

        vce-b3 is already in the building-B stack, which is the NEWER of the two
        same-named rows. A name-only reference must resolve to the chassis it is
        in -- the previous policy resolved to the older row and MOVED it, which
        is the same defect as the ambiguity above wearing a converged disguise:
        it looks idempotent and is a relocation.
        """
        vc_a, _ = self._seed_named_stack(
            "vce-shared", "building-a", {"vce-a1": 1, "vce-a2": 2})
        vc_b, _ = self._seed_named_stack(
            "vce-shared", "building-b", {"vce-b1": 1, "vce-b2": 2})
        member = Device.objects.create(
            name="vce-b3", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc_b, vc_position=3)

        payload = self._device_payload("vce-b3", {
            "vc_position": 3, "virtual_chassis": {"name": "vce-shared"},
        })
        self.assertEqual(self._diff(payload).get("changes"), [])
        member.refresh_from_db()
        self.assertEqual(member.virtual_chassis_id, vc_b.pk)
        self.assertEqual(vc_a.members.count(), 2)

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

    def test_fresh_stack_two_requests_both_orders_are_stable(self):
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
            if order == "master_first":
                # The chassis exists before the member names it, so the
                # member's name-only reference resolves to the one row there.
                self._assert_single_vc(
                    vc_name, master_name=m, members={m: 1, s2: 2})
            else:
                # Member-first builds the row before any master exists, so
                # the master-bearing pass declines it and takes its own.
                self._assert_split_vc(vc_name, master_name=m,
                                      mastered_members={m: 1},
                                      orphan_members={s2: 2})
            self._assert_noop_rediff(master_payload)
            self._assert_noop_rediff(member_payload)

    def test_bulk_plan_apply_member_first_ordering_is_stable(self):
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

        # The member entity creates a masterless row and the master-bearing
        # entity declines to adopt it, so the request ends with two rows. The
        # decline does not care that both entities arrived together: sharing the
        # request-scoped caches does not make a populated row identifiable.
        rows = VirtualChassis.objects.filter(name=vc_name)
        self.assertEqual(rows.count(), 2, list(rows.values_list("pk", "master_id")))

        # Pin the bulk request's own outcome: each device sits in the row its
        # OWN entity produced, before any follow-up re-ingest happens.
        orphan = rows.filter(master__isnull=True).get()
        mastered = rows.exclude(master__isnull=True).get()
        self.assertEqual(Device.objects.get(name=s2).virtual_chassis_id, orphan.pk)
        self.assertEqual(mastered.master.name, m)
        self._diff_apply(master_payload, allow_empty=True)
        self._assert_split_vc(vc_name, master_name=m,
                              mastered_members={m: 1}, orphan_members={s2: 2})
        self._assert_noop_rediff(master_payload)
        self._assert_noop_rediff(member_payload)

    def _second_producer_payload(self, domain=None):
        """A site-B device claiming the same chassis NAME, with its own master."""
        vc = {
            "name": "vce-shared",
            "master": {"name": "vce-b3", "site": {"name": "vce-site-b"}},
        }
        if domain is not None:
            vc["domain"] = domain
        return self._device_payload("vce-b3", {
            "site": {"name": "vce-site-b"},
            "vc_position": 3,
            "virtual_chassis": vc,
        })

    def _seed_a_producers_member_first_stack(self):
        """
        Producer A ingests two members, name-only VC ref: the shape of this PR.

        Leaves exactly the state the member-first convergence tests rely on -- a
        masterless row nobody has mastered yet -- and it is the same state a
        SECOND producer's identically named stack presents to adoption. That is
        the point: the row cannot tell the two apart, so the guard has to.
        """
        Site.objects.create(name="vce-site-b", slug="vce-site-b")
        for name, position in (("vce-a1", 1), ("vce-a2", 2)):
            self._diff_apply(self._device_payload(name, {
                "vc_position": position,
                "virtual_chassis": {"name": "vce-shared"},
            }))
        row = VirtualChassis.objects.get(name="vce-shared")
        self.assertIsNone(row.master_id)
        self.assertEqual(row.members.count(), 2)
        return row

    def _assert_producer_a_row_untouched(self, row, before, extra_rows=0):
        """
        Producer A's row is byte-identical, and the decline left no mess.

        ``extra_rows`` is how many same-named rows the declining payload created
        for itself: 0 where the payload was not applied at all, 1 where it
        declined the adoption and created its own. Asserted rather than left
        open, because "declined" and "silently did nothing" look alike from
        producer A's side and only one of them converges.
        """
        row.refresh_from_db()
        self.assertIsNone(row.master_id, "the declined adoption mastered the row anyway")
        self.assertEqual(row.members.count(), 2, "a device was moved into the row")
        self.assertEqual(row.last_updated, before, "the declined adoption saved the row")
        self.assertEqual(
            VirtualChassis.objects.filter(name=row.name).count(), 1 + extra_rows)

    # ---- the real producer, transcribed ------------------------------------

    # The chassis-relevant entities of a real orb-agent snmp-discovery capture
    # of a three-member Cisco 2960X stack, in the order the agent emitted them.
    # The full run is 154 entities; the 145 interface entities, the two
    # ip_address and two prefix entities, the master's primary_ip4 sub-tree, the
    # platform nodes and the free-text descriptions are dropped here because
    # none of them reaches a VirtualChassis. Everything that does is verbatim,
    # including two details that matter: the standalone virtual_chassis entity
    # carries a name and a master stub and NOTHING else (no domain -- orb's
    # device_name builder emits none, at all, for any device), and vc_position
    # arrives as a string. The device and the chassis share the name because the
    # capture's own redaction wrote "<private>" into both.
    _ORB_MASTER_STUB = {
        "name": "<private>",
        "device_type": {"manufacturer": {"name": "Cisco"}, "model": "WS-C2960X-48FPS-L"},
        "role": {"name": "switch"},
        "serial": "FCW1929B68S",
        "site": {"name": "orbvc-lab"},
    }

    def _orb_capture_payloads(self):
        """The four entities above, as ingest payloads in producer order."""
        def device(name, model, serial, extra=None):
            entity = {
                "name": name,
                "device_type": {"manufacturer": {"name": "Cisco"}, "model": model},
                "role": {"name": "switch"},
                "serial": serial,
                "site": {"name": "orbvc-lab"},
            }
            entity.update(extra or {})
            return {"timestamp": 1, "object_type": "dcim.device",
                    "entity": {"device": entity}}

        return [
            device("<private>", "WS-C2960X-48FPS-L", "FCW1929B68S"),
            {"timestamp": 1, "object_type": "dcim.virtualchassis",
             "entity": {"virtual_chassis": {
                 "name": "<private>", "master": dict(self._ORB_MASTER_STUB)}}},
            device("<private>-2", "WS-C2960X-24PS-L", "FCW1931A06Z", {
                "vc_position": "2",
                "virtual_chassis": {
                    "name": "<private>", "master": dict(self._ORB_MASTER_STUB)},
            }),
            device("<private>-3", "WS-C2960X-48FPS-L", "FCW1929B6BP", {
                "vc_position": "3",
                "virtual_chassis": {
                    "name": "<private>", "master": dict(self._ORB_MASTER_STUB)},
            }),
        ]

    def test_the_real_orb_agent_capture_gets_its_own_chassis_beside_a_same_named_row(self):
        """
        THE headline case, and the one that chose decline-and-create over refuse.

        A masterless, populated, same-named VirtualChassis is already in NetBox
        -- somebody else's stack, or this producer's own earlier member-first
        pass, indistinguishable from the payload's side. The capture above is
        then replayed in producer order. Three behaviours were measured on
        v4.5.5 with the full 154-entity run:

          - develop (08af3fb, no adoption at all): 200, the stack gets its own
            chassis, members at 1/2/3, empty re-diff.
          - the branch before this series: adoption took the oldest masterless
            same-named row, so the master was hijacked into the foreign row --
            dragged in at position 3, with the row's cached member_count left at
            1 against 3 real members. Because that row is not the one the
            members' own payloads resolve to, their two device entities were
            REJECTED (400 at apply-change-set, aggregated to 207 at
            bulk-plan-apply: "Device with this Virtual chassis and VC position
            already exists"), on that pass and on every later one; the two
            switches then reach NetBox only through their interface entities,
            which create them with virtual_chassis NULL and vc_position NULL.
            The rejection is loud and repeats; the hijack next to it is silent.
            Measured on the full 154-entity run, whose interface entities are
            what create those two devices at all -- the four entities
            transcribed below would leave them absent.
          - refusing the ambiguous adoption: 207 on every pass, forever. The
            standalone virtual_chassis entity carries name + master and nothing
            else, so no remedy naming a payload field is one this producer can
            take, and the identical bytes arrive again every run.

        Declining the adoption and letting the CREATE proceed reproduces
        develop's outcome exactly, which is the point: this is a branch that
        adds a mechanism, and the mechanism must not make the branch worse than
        its own base on its own producer's data.

        Asserted here: the payload's stack ends up in a chassis of its OWN with
        all three members at 1/2/3, the cached member_count agrees with the real
        one, the foreign row is byte-identical (last_updated, master, members),
        and every entity re-diffs empty.
        """
        other = Site.objects.create(name="orbvc-other", slug="orbvc-other")
        foreign = VirtualChassis.objects.create(name="<private>")
        for name, position in (("orbvc-decoy-1", 1), ("orbvc-decoy-2", 2)):
            d = Device.objects.create(
                name=name, site=other, device_type=self.dt, role=self.role)
            Device.objects.filter(pk=d.pk).update(
                virtual_chassis=foreign, vc_position=position)
        foreign.refresh_from_db()
        before = foreign.last_updated
        foreign_members = sorted(foreign.members.values_list("name", "vc_position"))

        payloads = self._orb_capture_payloads()
        for payload in payloads:
            self._diff_apply(payload, allow_empty=True)

        mine = VirtualChassis.objects.exclude(pk=foreign.pk).get(name="<private>")
        self.assertEqual(mine.master.name, "<private>")
        self.assertEqual(
            sorted(mine.members.values_list("name", "vc_position")),
            [("<private>", 1), ("<private>-2", 2), ("<private>-3", 3)],
        )
        self.assertEqual(mine.member_count, mine.members.count())
        self.assertEqual(mine.member_count, 3)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.master_id, "the master was hijacked into the foreign row")
        self.assertEqual(foreign.last_updated, before, "the foreign row was written")
        self.assertEqual(
            sorted(foreign.members.values_list("name", "vc_position")), foreign_members)

        for payload in payloads:
            self._assert_noop_rediff(payload)

    def test_the_capture_reconverges_after_an_operator_re_elects_the_master(self):
        """
        An operator re-elects the master in NetBox; the next ingest must find the row.

        Measured on v4.5.5 and v4.4.10 with the full 154-entity capture replayed
        through bulk-plan-apply, after ``vc.master`` was moved to the member at
        position 2:

          - without the member-holding candidate below: the standalone
            virtual_chassis entity resolves to nothing (the name matcher is
            gated off by its master stub, unique_master looks for a row mastered
            by a device that no longer masters one, and the adopter only
            considered masterless rows), so it CREATES a second chassis. The
            three-member stack ends up split 1 + 2 across two rows and the
            member that is still the old row's master fails forever with
            NetBox's "Device cannot be removed from virtual chassis ... because
            it is currently designated as its master" -- 207 on every pass.
          - with it: 200, one row, the master restored, the members untouched.

        The candidate is admitted on the strongest identity evidence there is
        (rule 1: the requested master is ALREADY a member of that row, so
        nothing is moved on the strength of a name) and only when the row does
        not already carry that master -- a row that does is resolved by
        unique_master at plan time, and a CREATE that reached the adopter for it
        is a stale plan the create path must settle without rewriting the row
        (test_master_already_owning_a_chassis_declines_adoption_of_a_decoy).

        The find-obj lookup cache is disabled for the second pass on purpose.
        With it warm, develop and the pre-identity branch head appear to
        converge here -- but only because a 30-second cached pk is served for a
        payload that no matcher resolves any more, so the apparent convergence
        expires with the cache and orb-agent's re-run interval is longer than
        that. The behaviour has to hold without it.
        """
        payloads = self._orb_capture_payloads()
        for payload in payloads:
            self._diff_apply(payload, allow_empty=True)

        vc = VirtualChassis.objects.get(name="<private>")
        successor = vc.members.exclude(pk=vc.master_id).order_by("vc_position").first()
        vc.master = successor
        vc.save()
        self.assertEqual(vc.master.name, "<private>-2")

        with mock.patch.object(matcher, "_get_find_obj_cache_ttl", return_value=0):
            for payload in payloads:
                self._diff_apply(payload, allow_empty=True)

            self.assertEqual(VirtualChassis.objects.filter(name="<private>").count(), 1)
            reconverged = self._assert_single_vc(
                "<private>", master_name="<private>",
                members={"<private>": 1, "<private>-2": 2, "<private>-3": 3},
            )
            self.assertEqual(reconverged.pk, vc.pk, "the stack was split into a second row")
            self.assertEqual(reconverged.member_count, reconverged.members.count())

            for payload in payloads:
                self._assert_noop_rediff(payload)

    def test_a_second_producers_member_first_stack_gets_its_own_row(self):
        """
        Producer B never lands in producer A's stack. This is the data-integrity line.

        Producer A ingests two site-A devices member-first, leaving a masterless
        "vce-shared" holding both. Producer B then ingests one site-B device
        whose nested virtual_chassis carries the same name, its own master, and
        no domain. It gets its OWN row: producer A's row keeps exactly its two
        members and stays masterless, and nothing of B's is written into it.

        This used to adopt, and what it did was the worst outcome reachable on
        this path -- B's device joined A's stack and became its master, reported
        200, and left no duplicate and no ambiguity for anyone to notice. The
        licence was that this changeset plans B's membership of the chassis it
        is creating; but that Device change names the CREATE's own reference,
        not A's row, and adoption is the step that would redirect it, so the
        reference could never be evidence about which row to take.

        The payload is byte-for-byte the shape of a legitimate member-first
        second pass by ONE producer (test_fresh_stack_two_requests_both_orders_
        are_stable, member_first), which is exactly why no rule over these rows
        can separate them: same name, masterless, populated, unlabelled either
        way. So both now decline, and the cost lands on the honest case as a
        duplicate rather than on the dishonest one as a silent merge. Closing it
        properly needs identity the source owns.

        What is held down here: the split CONVERGES -- the re-diff is empty, so
        no pass re-plans -- and it is visible, two rows an operator can merge.
        The sibling test below is the escape hatch: a domain makes producer B's
        payload describe its own chassis and it gets one.
        """
        self._seed_a_producers_member_first_stack()

        payload = self._second_producer_payload()
        cs = self._diff(payload)
        vc_changes = [c for c in cs["changes"] if c["object_type"] == "dcim.virtualchassis"]
        self.assertEqual([c["change_type"] for c in vc_changes], ["create"], cs)

        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))

        self._assert_split_vc(
            "vce-shared", master_name="vce-b3",
            mastered_members={"vce-b3": 3},
            orphan_members={"vce-a1": 1, "vce-a2": 2},
        )
        self._assert_noop_rediff(payload)

    def test_the_row_a_decline_leaves_behind_refuses_a_later_name_only_member(self):
        """
        What declining COSTS, measured, one producer and no foreign row anywhere.

        Nothing here is another source's stack. One producer sends two members
        with name-only virtual_chassis references, which builds a single
        masterless row; then a STANDALONE dcim.virtualchassis entity naming a
        master for the same name. That entity plans no device change, so
        adoption declines it (_choose_adoption_candidate: a populated candidate,
        no membership planned) and the create gives it a row of its own. Two
        populated rows now share the name.

        From there:
          - an EXISTING member re-ingests clean, because the member hint puts it
            in the row it is already in (VirtualChassisNameMatcher rule 1);
          - a NEW member with the same name-only reference is a 400 at
            generate-diff -- on that pass and every later one -- and its device
            is not created at all.

        That is the residual cost of decline-and-create, and it is asserted here
        rather than described in a docstring: the refusal is loud, it names the
        two rows and remedies that work, and no row was written -- but a
        producer in this shape is blocked on this name until an operator merges
        the rows or places the device. The alternative measured against it was
        adopting the populated row on the strength of the name, which is the
        hijack this series removed.
        """
        for name, position in (("vce-m1", 1), ("vce-m2", 2)):
            self._diff_apply(self._device_payload(name, {
                "vc_position": position,
                "virtual_chassis": {"name": "vce-solo"},
            }))
        row = VirtualChassis.objects.get(name="vce-solo")
        self.assertIsNone(row.master_id)
        self.assertEqual(row.members.count(), 2)

        # The master arrives as a plain device entity first and then as the
        # standalone chassis entity's master stub -- orb-agent's own shape.
        self._diff_apply(self._device_payload("vce-m3"))
        self._diff_apply(self._vc_payload("vce-solo", "vce-m3"))
        rows = VirtualChassis.objects.filter(name="vce-solo").order_by("pk")
        self.assertEqual(rows.count(), 2)
        row.refresh_from_db()
        self.assertIsNone(row.master_id, "the decline mastered the row anyway")
        self.assertEqual(row.members.count(), 2)
        self.assertEqual(rows.last().master.name, "vce-m3")

        # An existing member is unaffected: the hint answers for it.
        self._assert_noop_rediff(self._device_payload("vce-m1", {
            "vc_position": 1, "virtual_chassis": {"name": "vce-solo"}}))

        # A new member is not: two populated rows, nothing to tell them apart.
        r = self.client.post(
            self.diff_url,
            data=self._device_payload("vce-m4", {
                "vc_position": 4, "virtual_chassis": {"name": "vce-solo"}}),
            format="json", **self.auth,
        )
        self.assertEqual(r.status_code, 400, r.content)
        error = r.json()["errors"]["dcim.virtualchassis"]["name"][0]
        self.assertIn("merge the duplicates", error)
        self.assertFalse(Device.objects.filter(name="vce-m4").exists())
        self.assertEqual(VirtualChassis.objects.filter(name="vce-solo").count(), 2)

    def test_the_second_producer_gets_its_own_row_once_it_says_which(self):
        """
        The escape hatch from the bound above, and it does not touch A's row.

        A domain makes the payload describe ITS chassis rather than share a name
        with one, and no candidate carries that value -- which is not ambiguity
        but "a chassis that does not exist yet", so it is created. Two rows share
        the name afterwards and that is the correct answer: they are two stacks.
        This is the one lever a producer has over the bound, and it is why the
        bound is a bound rather than a defect: the ambiguity is in the data.
        """
        row = self._seed_a_producers_member_first_stack()
        before = row.last_updated

        payload = self._second_producer_payload(domain="vce-site-b")
        self._diff_apply(payload)
        self._assert_noop_rediff(payload)

        self._assert_producer_a_row_untouched(row, before, extra_rows=1)
        mine = VirtualChassis.objects.exclude(pk=row.pk).get(name="vce-shared")
        self.assertEqual(mine.domain, "vce-site-b")
        self.assertEqual(mine.master.name, "vce-b3")
        self.assertEqual(
            list(mine.members.values_list("name", flat=True)), ["vce-b3"])

    def test_bulk_plan_apply_reaches_the_same_answer_for_a_second_producer(self):
        """
        Same bound through the other door, and the reason to test it twice.

        /bulk-plan-apply/ plans and applies each entity in turn, sharing the
        request-scoped caches, so entity 3 is planned against a database that
        already holds entities 1 and 2 -- a real single-request analogue of the
        cross-producer sequence. The point of testing it here is that the two
        doors must not disagree: a 207 at one and a 200 at the other would mean
        the same three payloads reconcile differently depending on how the
        reconciler batched them.
        """
        Site.objects.create(name="vce-site-b", slug="vce-site-b")
        entities = [
            {"id": "a1", "object_type": "dcim.device",
             "entity": self._device_payload("vce-a1", {
                 "vc_position": 1, "virtual_chassis": {"name": "vce-shared"}})["entity"]},
            {"id": "a2", "object_type": "dcim.device",
             "entity": self._device_payload("vce-a2", {
                 "vc_position": 2, "virtual_chassis": {"name": "vce-shared"}})["entity"]},
            {"id": "b3", "object_type": "dcim.device",
             "entity": self._second_producer_payload()["entity"]},
        ]
        r = self.client.post(self.bulk_plan_apply_url, data={"entities": entities},
                             format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        results = {res["id"]: res for res in r.json()["results"]}
        for key in ("a1", "a2", "b3"):
            self.assertIsNone(results[key].get("errors"), results[key])

        self._assert_split_vc(
            "vce-shared", master_name="vce-b3",
            mastered_members={"vce-b3": 3},
            orphan_members={"vce-a1": 1, "vce-a2": 2},
        )

    def test_the_differ_drops_an_empty_domain_from_a_planned_create(self):
        """
        A producer that sends domain "" is, by the time apply reads it, silent.

        Measured here rather than assumed: the differ DROPS ``domain: ""`` from
        the CREATE it plans, because it equals the column default for a row that
        does not exist yet -- so whatever the producer sent, adoption sees no
        assertion at all and this payload gets exactly the treatment of one that
        omitted the field. If that ever changes, this test is what notices.

        The other half -- that "" could not IDENTIFY a row even where it does
        survive, because every chassis that never set a domain carries it -- is
        pinned at the door where it survives, apply-change-set, whose changes no
        differ built: test_an_empty_domain_is_not_an_identification_that_
        licenses_adoption in the matcher suite.
        """
        self._seed_a_producers_member_first_stack()

        cs = self._diff(self._second_producer_payload(domain=""))
        vc_create = [c for c in cs["changes"]
                     if c["object_type"] == "dcim.virtualchassis"][0]
        self.assertEqual(vc_create["change_type"], "create")
        self.assertNotIn("domain", vc_create["data"], vc_create)

    def test_a_labelled_row_survives_a_payload_that_asserts_no_domain(self):
        """
        An explicitly empty domain is a value, and it excludes the labelled row.

        Two same-named populated stacks, one labelled "vce-dom" and one not. A
        member payload carrying domain "" used to have that value DROPPED as if
        absent: the reference was then ambiguous between both rows, and where it
        did resolve, "" was written over the matched row's domain -- destroying
        the one field the ambiguity refusal tells the operator to set. Now ""
        narrows to the row that carries no domain, and the labelled row is not
        read, not bound and not written.
        """
        labelled, _ = self._seed_named_stack("vce-dup", "vce-dom", {"vce-d1": 1})
        plain, _ = self._seed_named_stack("vce-dup", "", {"vce-p1": 1})
        before = labelled.last_updated

        payload = self._device_payload("vce-p2", {
            "vc_position": 2,
            "virtual_chassis": {"name": "vce-dup", "domain": ""},
        })
        self._diff_apply(payload)

        self.assertEqual(Device.objects.get(name="vce-p2").virtual_chassis_id, plain.pk)
        labelled.refresh_from_db()
        self.assertEqual(labelled.domain, "vce-dom", "the payload's '' was written over it")
        self.assertEqual(labelled.last_updated, before)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-dup").count(), 2)

    def test_standalone_vc_payload_adopts_a_domain_identified_row_and_attaches_master(self):
        """
        FIRST ingest must converge: adoption has to establish membership itself.

        A member-first ingest leaves a masterless VC; the standalone VC payload
        that names the master IS the whole payload, so there is no "later
        ingest" of it that would ever attach the master device. Dropping master
        here leaves the row masterless forever and re-plans the same CREATE on
        every pass.

        THE OLD EXPECTATION HERE WAS UNSAFE in one respect, and the payload is
        what changed: this test used to adopt a POPULATED masterless row on the
        strength of its NAME alone, which is exactly the shape that attaches
        this payload's master to another producer's stack and drags the device
        into it (see test_standalone_vc_payload_creates_its_own_row_rather_than_
        taking_one, which now pins the decline). The payload therefore carries
        domain, so the row is identified rather than guessed -- and every
        assertion about what adoption then DOES is unchanged, because none of
        them was the problem.

        The master lands at position 1 -- the position NetBox itself assigns
        when it attaches a new chassis's master (dcim.signals.
        assign_virtualchassis_master) -- because the payload carries none.
        """
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        vc = VirtualChassis.objects.create(name="vce-stack", domain="vce-dom")
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc, vc_position=2)
        Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        count_before = VirtualChassis.objects.get(pk=vc.pk).member_count

        payload = self._vc_payload("vce-stack", "vce-sw1", domain="vce-dom")
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

    def test_standalone_vc_payload_creates_its_own_row_rather_than_taking_one(self):
        """
        The measured hazard, and the answer that both avoids it and converges.

        Identical to the adoption test above except that the payload carries no
        discriminator. The row named "vce-stack" already holds vce-sw2; the
        payload asks for vce-sw1 to master a chassis of that name. Adopting on
        the name alone attached vce-sw1 to THAT row -- measured 200, master set,
        the device dragged into a stack it was never in, and no later diff
        mentioning the join, because a VirtualChassis payload names no members.

        It now creates its own row, and the pre-existing one is left
        byte-identical: last_updated is the assertion that proves it, because
        "untouched" and "saved with the same two fields" look alike in name and
        master.

        This shape is the one that decided the design. It is orb-agent's
        standalone virtual_chassis entity -- the second of the 154 entities its
        snmp-discovery run emits (see test_the_real_orb_agent_stack_capture_...).
        A refusal here is permanent for that producer: the entity carries a name
        and a master and nothing else, orb emits no domain at all, and every
        subsequent run re-sends the identical bytes. Measured against the real
        capture with a masterless same-named row present: refusing gave 207 on
        every pass forever; declining gives 200, the stack's own chassis, and an
        empty re-diff -- which is also what develop does with the same input.
        """
        member = Device.objects.create(
            name="vce-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        vc = VirtualChassis.objects.create(name="vce-stack")
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc, vc_position=2)
        master = Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        vc.refresh_from_db()
        before = vc.last_updated

        payload = self._vc_payload("vce-stack", "vce-sw1")
        self._diff_apply(payload)

        vc.refresh_from_db()
        self.assertIsNone(vc.master_id)
        self.assertEqual(vc.last_updated, before, "the declined adoption saved the row")
        self.assertEqual(list(vc.members.values_list("name", flat=True)), ["vce-sw2"])

        mine = VirtualChassis.objects.exclude(pk=vc.pk).get(name="vce-stack")
        self.assertEqual(mine.master_id, master.pk)
        master.refresh_from_db()
        self.assertEqual(master.virtual_chassis_id, mine.pk)
        self.assertEqual(master.vc_position, 1)
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
        vc = VirtualChassis.objects.create(name="vce-stack", domain="vce-dom")
        for name, position in (("vce-sw2", 1), ("vce-sw3", 3)):
            d = Device.objects.create(
                name=name, site=self.site, device_type=self.dt, role=self.role
            )
            Device.objects.filter(pk=d.pk).update(virtual_chassis=vc, vc_position=position)
        Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )

        # domain identifies the row: a POPULATED chassis is not adopted on its
        # name alone any more, so the position rule is exercised through the
        # discriminated path (see test_standalone_vc_payload_creates_its_own_
        # row_rather_than_taking_one).
        self._diff_apply(self._vc_payload("vce-stack", "vce-sw1", domain="vce-dom"))
        self._assert_single_vc("vce-stack", master_name="vce-sw1",
                              members={"vce-sw1": 2, "vce-sw2": 1, "vce-sw3": 3})

    def test_a_device_payload_may_move_itself_into_the_chassis_it_names(self):
        """A device that carries its own membership change IS authority for the move."""
        # The sibling test above refuses this move for a STANDALONE chassis payload,
        # and that refusal was right. Applied to a device payload it was wrong: the
        # plan contains `update dcim.device` asserting this chassis, ordered after the
        # create it names, so the move is the producer's request and is in the preview.
        # Refusing it rejected the apply before that update ran, rolled it back, and
        # every identical re-ingest failed the same way -- a permanent 400 on the
        # member-first shape this feature exists for.
        old = VirtualChassis.objects.create(name="vcm-old")
        mover = Device.objects.create(
            name="vcm-d1", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=mover.pk).update(virtual_chassis=old, vc_position=1)
        target = VirtualChassis.objects.create(name="vcm-new")

        payload = self._device_payload("vcm-d1", extra={
            "virtual_chassis": {
                "name": "vcm-new",
                "master": self._device_payload("vcm-d1")["entity"]["device"],
            },
            "vc_position": 1,
        })
        self._diff_apply(payload)

        mover.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(mover.virtual_chassis_id, target.pk)
        self.assertEqual(target.master_id, mover.pk)
        self.assertEqual(VirtualChassis.objects.filter(name="vcm-new").count(), 1)
        self.assertEqual(target.member_count, target.members.count())
        self._assert_noop_rediff(payload)

    def test_standalone_vc_payload_will_not_move_master_out_of_another_chassis(self):
        """
        Adoption attaches a chassis-less device; it does not relocate one -- and says so.

        Membership is asserted by the DEVICE payload (Device.virtual_chassis).
        A VC payload naming a master is not authority to pull that device out
        of a chassis it already belongs to. That part is unchanged.

        THE OLD EXPECTATION HERE WAS UNSAFE in the answer it accepted, not in
        the refusal: this test used to assert 200 with master silently dropped.
        A payload requesting VirtualChassis(name=X, master=Y) was reported as
        successfully applied while Y was not made master of anything, and
        because a standalone VC payload carries nothing else, an identical
        re-ingest re-planned the identical CREATE forever -- three plan+apply
        cycles measured, each 200 with errors null, master never set. "The
        deviation stays visible because the CREATE keeps re-planning" is not
        visibility; it asks the producer to infer a conflict from a plan that
        never empties. It is now a per-entity conflict on field master, and the
        DB is left exactly as it was.

        The remedy had to be rewritten too. It used to read "ingest the device's
        own payload with virtual_chassis and vc_position to move it", which is
        advice orb-agent cannot follow: it sends the stack master as a plain
        device entity with no chassis fields at all (that is what the standalone
        virtual_chassis entity is for), so there is no payload of its it could
        change. The move it names now is one an operator can make in NetBox, and
        the message says the payload then applies unchanged.
        """
        other, _ = self._seed_stack(vc_name="vce-other", master="vce-sw9")
        member = Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=other, vc_position=2)
        vc = VirtualChassis.objects.create(name="vce-stack")
        vc.refresh_from_db()
        before = vc.last_updated

        cs = self._diff(self._vc_payload("vce-stack", "vce-sw1"))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        error = r.json()["errors"]["dcim.virtualchassis"]["master"][0]
        self.assertIn("vce-sw1", error)
        self.assertIn("vce-other", error)
        self.assertIn("Move the device in NetBox", error,
                      "the error must name a move somebody can actually make")
        self.assertIn("applies unchanged on the next pass", error)
        self.assertNotIn(
            "vc_position", error,
            "orb-agent sends the master with no chassis fields; asking it for "
            "virtual_chassis + vc_position on that device is advice it cannot take",
        )

        member.refresh_from_db()
        self.assertEqual(member.virtual_chassis_id, other.pk)  # not relocated
        self.assertEqual(member.vc_position, 2)
        vc.refresh_from_db()
        self.assertIsNone(vc.master_id)
        self.assertEqual(vc.members.count(), 0)
        self.assertEqual(vc.last_updated, before, "the refused apply saved the chassis anyway")
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)

    def test_a_conflicted_master_converges_once_the_device_moves_itself(self):
        """
        The conflict is a conflict, not a dead end: the device's own payload clears it.

        This is what makes the refusal above the right answer rather than merely
        a louder one. The device payload owns membership, so ingesting IT moves
        vce-sw1 into vce-stack; the identical VirtualChassis payload then adopts
        the row (its master is now a member -- the strongest identity there is)
        and sets master. Two ingests, no guess, no relocation performed by a
        chassis payload.
        """
        other, _ = self._seed_stack(vc_name="vce-other", master="vce-sw9")
        member = Device.objects.create(
            name="vce-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=member.pk).update(virtual_chassis=other, vc_position=2)
        vc = VirtualChassis.objects.create(name="vce-stack")

        vc_payload = self._vc_payload("vce-stack", "vce-sw1")
        cs = self._diff(vc_payload)
        self.assertEqual(
            self.client.post(self.apply_url, data=cs, format="json", **self.auth).status_code,
            400,
        )

        # the device asserts its own membership
        self._diff_apply(self._device_payload("vce-sw1", {
            "vc_position": 1, "virtual_chassis": {"name": "vce-stack"},
        }))
        # ...and now the same chassis payload lands
        self._diff_apply(vc_payload)
        adopted = self._assert_single_vc("vce-stack", master_name="vce-sw1",
                                        members={"vce-sw1": 1})
        self.assertEqual(adopted.pk, vc.pk)
        self._assert_noop_rediff(vc_payload)

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

    def test_missing_master_and_conflicted_master_are_not_the_same_outcome(self):
        """
        Adoption's two "cannot attach" cases must not collapse into one branch.

        THE OLD EXPECTATION HERE WAS UNSAFE for the first half: it asserted 200
        with master dropped when the named master lived in another chassis. Both
        halves are 400 now, so the point of the test moves -- but it does not
        disappear, because the two are still different answers and collapsing
        them still costs a property:

        - the named master is a MEMBER of a different chassis: a CONFLICT the
          producer can act on, reported on field master, naming the chassis
          that holds the device and how to move it. It is recoverable by
          ingesting the device (test_a_conflicted_master_converges_once_the_
          device_moves_itself).
        - the named master DOES NOT EXIST: a dangling reference, reported by
          NetBox's own serializer on the same field. Nothing can converge a pk
          that resolves to nothing -- the producer's fix is a different payload,
          not a different order.

        The shared field is deliberate (both are about master) and it is why
        the MESSAGES are asserted rather than just the status: a single branch
        handling both would still produce two 400s, and only the text would
        reveal that the reconciler had been told the wrong thing.
        """
        # conflict: the master is real, but it lives in another chassis
        other, _ = self._seed_stack(vc_name="vce-other", master="vce-sw9")
        held = Device.objects.create(
            name="vce-held", site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=held.pk).update(virtual_chassis=other, vc_position=2)
        conflicted_vc = VirtualChassis.objects.create(name="vce-conflicted")

        cs = self._diff(self._vc_payload("vce-conflicted", "vce-held"))
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        conflict = r.json()["errors"]["dcim.virtualchassis"]["master"][0]
        self.assertIn("member of", conflict, conflict)
        self.assertIn("vce-other", conflict, conflict)
        conflicted_vc.refresh_from_db()
        self.assertIsNone(conflicted_vc.master_id)
        held.refresh_from_db()
        self.assertEqual(held.virtual_chassis_id, other.pk)

        # missing: same inability to attach, different reason and different error
        missing_vc, cs = self._plan_vc_create_then_delete_master("vce-missing", "vce-ghost")
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, (cs, r.content))
        dangling = r.json()["errors"]["dcim.virtualchassis"]["master"][0]
        self.assertNotIn("member of", dangling, dangling)
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

        With master present the matcher that answers find_existing_object is
        the auto-derived unique_master one, so a master-bearing CREATE routed
        onto the pre-save path resolves "create the chassis named NEW, mastered
        by D" onto whichever chassis D already masters -- an unrelated,
        already-converged row.

        Read the mechanism carefully, because it changed inside this series and
        the earlier wording here was wrong. It is NOT the payload gate
        (matcher._virtualchassis_pre_save_match_applies) that keeps this green
        today: bind-only does (matcher._PRE_SAVE_MATCH_BIND_ONLY), because a
        pre-save-matched dcim.virtualchassis CREATE no longer writes the row it
        resolves onto at all. Removing the gate leaves this test -- and every
        other test in this file -- passing; only the gate's own seam test,
        test_route_is_scoped_to_masterless_payloads, fails. The gate still
        earns its place as a seam pin: it keeps master out of an ORM pk filter,
        where the malformed forms below coerce silently rather than declining
        (True -> pk 1, 7.5 -> pk 7, 7.0 -> pk 7), and it stops a master-bearing
        CREATE binding to the wrong row so that later references in the
        changeset resolve to it. Neither of those is a WRITE, which is why
        mutating the gate away does not fail this assertion.

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


    # ---- bind, do not overwrite -------------------------------------------

    def _row_fields(self, pks):
        """
        The chassis fields a CREATE must never write, per row.

        member_count is deliberately NOT here: binding a member legitimately
        changes it, and _assert_member_count_is_honest checks that instead.
        last_updated IS here, because it is the only witness that separates
        "untouched" from "saved with values identical to its own".
        """
        return {
            row.pk: (row.name, row.description, row.domain, row.master_id, row.last_updated)
            for row in VirtualChassis.objects.filter(pk__in=pks)
        }

    def _assert_member_count_is_honest(self, *vcs):
        """Stored member_count against the membership that actually exists."""
        for vc in vcs:
            fresh = VirtualChassis.objects.get(pk=vc.pk)
            self.assertEqual(
                fresh.member_count, fresh.members.count(),
                f"{fresh.name}: stored member_count diverged from reality",
            )

    #: Every shape a name-only match can land on. Not variations on a theme --
    #: three distinct ways that criterion can be wrong about identity, and the
    #: two that are not "masterless and empty" are the two it was never
    #: verified against.
    ROW_KINDS = (
        # A converged stack another source owns. VirtualChassis.name has no
        # unique constraint, so a same-named plan is no evidence of sameness.
        "mastered",
        # The row the plan-ahead race actually means: inserted moments earlier
        # by a sibling plan built from the same masterless payload.
        "masterless_empty",
        # What a member-first ingest leaves, and what this PR's own
        # IN_OTHER_CHASSIS branch deliberately leaves: real members, no master.
        # A guard keyed on master__isnull calls this one fresh and writes it.
        "masterless_populated",
    )

    def _seed_row_kind(self, kind, name):
        """One chassis of the given shape, owned by another site, with fields set."""
        vc = VirtualChassis.objects.create(
            name=name, domain="dom-b", description="OWNED-BY-B"
        )
        if kind == "masterless_empty":
            return vc
        site_b = Site.objects.filter(slug="vce-site-b").first() or Site.objects.create(
            name="vce-site-b", slug="vce-site-b"
        )
        members = []
        for position in (1, 2):
            d = Device.objects.create(
                name=f"{name}-b{position}", site=site_b,
                device_type=self.dt, role=self.role,
            )
            d.virtual_chassis = vc
            d.vc_position = position
            d.save()
            members.append(d)
        if kind == "mastered":
            # Re-read before saving: the membership signals have since bumped
            # member_count by direct UPDATE, and a full save() through this
            # instance would write its own stale 0 back over them. (The applier
            # guards the same hazard in _try_adopt_masterless_virtualchassis.)
            vc.refresh_from_db()
            vc.master = members[0]
            vc.save()
        vc.refresh_from_db()
        return vc

    def test_a_masterless_create_never_writes_the_row_it_matched(self):
        """
        The contract, at the applier's own door: bind the row, write nothing.

        The pre-save match (matcher._REQUIRES_PRE_SAVE_MATCH) exists to stop a
        duplicate INSERT, and for dcim.virtualchassis that is ALL it may do.
        Applying the CREATE's payload to the matched row is a separate act and
        an unsafe one: the criterion is the name alone, VirtualChassis.name has
        no unique constraint, so the row may be a converged stack another
        source owns. Measured before this change -- a stale plan's description
        and domain landed on another site's live chassis, 200, no error, and
        nothing in any later diff to repair it, after which the two sources
        flapped those fields indefinitely.

        Both masterless wire forms are covered because a client changeset
        reaches the applier without the transformer, so master may be absent or
        explicitly null; the payload gate treats them alike and so must this.
        The explicit-null form is the more destructive, and it is where this
        change FIXES a bug rather than merely declining to introduce one: an
        explicit master: null on a matched row set that row's master_id to NULL
        while every member stayed attached -- measured on the parent commit AND
        on the first draft of this branch, which is why the "master_null" arms
        below are assertions about a repair, not a regression guard.

        The row-count assertion is the other half, and it is why this refuses
        the WRITE rather than the MATCH: exactly one row, so no duplicate is
        left behind for nothing to clean up.
        """
        for kind in self.ROW_KINDS:
            for label, extra in (("master_absent", {}), ("master_null", {"master": None})):
                with self.subTest(row=kind, master=label):
                    name = f"vce-{kind.replace('_', '-')}-{label.replace('_', '-')}"
                    vc = self._seed_row_kind(kind, name)
                    before = self._row_fields([vc.pk])
                    # No domain asserted on purpose: this payload must stay
                    # INDISTINGUISHABLE from the row it races, which is what
                    # makes the bind-only contract the thing under test. A
                    # payload asserting a domain the row does not carry is a
                    # different case entirely -- it identifies a different
                    # chassis and gets its own row (see
                    # test_a_contradicting_domain_takes_its_own_row).
                    data = {"name": name, "description": "FROM-A"}
                    data.update(extra)

                    r = self.client.post(
                        self.apply_url,
                        data={
                            "id": str(uuid.uuid4()),
                            "changes": [{
                                "change_id": str(uuid.uuid4()),
                                "change_type": "create",
                                "object_version": None,
                                "object_type": "dcim.virtualchassis",
                                "object_id": None,
                                "ref_id": "1",
                                "data": data,
                            }],
                        },
                        format="json", **self.auth,
                    )

                    self.assertEqual(r.status_code, 200, r.content)
                    self.assertIsNone(r.json().get("errors"))
                    self.assertEqual(self._row_fields([vc.pk]), before,
                                     f"{kind}/{label}: the matched chassis was written")
                    self.assertEqual(
                        VirtualChassis.objects.filter(name=name).count(), 1,
                        f"{kind}/{label}: the CREATE left a duplicate row behind",
                    )
                    self._assert_member_count_is_honest(vc)

    def test_a_bind_says_which_submitted_values_it_discarded(self):
        """
        A bind answers 200 having written nothing, so it has to say so.

        Refusing the write is right -- the criteria carry no database uniqueness,
        so the row may be a different object that merely matches -- but a caller
        told only "200, errors null" has been told its change applied when its
        payload was dropped. A producer that replays its whole state converges
        on the next pass, through the object-id UPDATE the row's existence now
        makes plannable; a one-shot or push-on-change producer never sends that
        pass and would never learn.

        The warning names only what was actually dropped. `name` was submitted
        too and is NOT named, because the row already carries it -- proof that
        this reports discarded writes rather than every field in the payload.
        """
        name = "vce-warns"
        vc = self._seed_row_kind("masterless_populated", name)

        r = self.client.post(
            self.apply_url,
            data={
                "id": str(uuid.uuid4()),
                "changes": [{
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.virtualchassis",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {"name": name, "description": "FROM-A"},
                }],
            },
            format="json", **self.auth,
        )
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertIsNone(body.get("errors"))

        warnings = body.get("warnings")
        self.assertEqual(len(warnings or []), 1, body)
        warning = warnings[0]
        self.assertEqual(warning["object_type"], "dcim.virtualchassis")
        self.assertEqual(warning["object_id"], vc.pk)
        self.assertIn("description", warning["fields"])
        self.assertNotIn(
            "name", warning["fields"],
            "reported a field the row already carried, so this is not a discard report",
        )
        self.assertIn("object_id", warning["message"])

    def test_an_ordinary_apply_carries_no_warnings_key(self):
        """The key is absent unless something happened, so a clean apply is unchanged."""
        payload = self._device_payload("vce-nowarn", {
            "vc_position": 1,
            "virtual_chassis": {
                "name": "vce-nowarn-stack",
                "master": {"name": "vce-nowarn", "site": {"name": "vce-site"}},
            },
        })
        cs = self._diff(payload)
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("warnings", r.json())

    def test_a_contradicting_domain_takes_its_own_row(self):
        """
        The other half of the bind-only contract: a domain the row lacks is identity.

        The same race as the tests above -- a same-named row already exists,
        owned by another source, carrying domain "dom-b" -- except this payload
        asserts "dom-a". A NON-EMPTY discriminator the row does not carry means
        "not this row" (matcher.contradicting_vc_discriminator), so the pre-save
        match does not bind it. The payload gets its own row and the other is
        byte-identical afterwards, last_updated included.

        This is the price of making domain mean ONE thing, and it is a real
        price: a plan-ahead race with a domain in it now ends in two rows where
        the name-only race still ends in one, because the match can no longer
        de-duplicate against a row the payload has just contradicted. It is
        the better half of the trade -- the alternative bound the row and then
        re-proposed the contradicted domain onto it on every later pass, forever
        -- and this one converges: the next pass narrows the two rows by the
        asserted domain and finds its own.
        """
        name = "vce-contradict"
        vc = self._seed_row_kind("masterless_populated", name)
        before = self._row_fields([vc.pk])

        r = self.client.post(
            self.apply_url,
            data={
                "id": str(uuid.uuid4()),
                "changes": [{
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.virtualchassis",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {"name": name, "description": "FROM-A", "domain": "dom-a"},
                }],
            },
            format="json", **self.auth,
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))

        self.assertEqual(self._row_fields([vc.pk]), before,
                         "the contradicted row was written after all")
        rows = VirtualChassis.objects.filter(name=name)
        self.assertEqual(rows.count(), 2, list(rows.values_list("pk", "domain")))
        own = rows.exclude(pk=vc.pk).get()
        self.assertEqual(own.domain, "dom-a")
        self.assertEqual(own.description, "FROM-A")

    def test_a_masterless_create_on_an_ambiguous_name_is_a_structured_400(self):
        """
        The APPLY boundary for ambiguity, reached without the transformer.

        apply-change-set and bulk-apply take a client-supplied changeset
        straight to the applier, so any ambiguity guard that lived only in the
        plan path would be a guard with a documented bypass. This is the same
        two-stacks-one-name state as the plan-time test, driven through the
        pre-save match instead: it calls find_existing_object, which is where
        the refusal lives, so one raise covers both doors.

        Neither row may be written and no third row may be inserted -- the two
        wrong answers here are opposite (bind one, or create a duplicate) and
        both are silent, which is why the row snapshot and the count are
        asserted together.
        """
        vc_a, _ = self._seed_named_stack(
            "vce-shared", "building-a", {"vce-a1": 1, "vce-a2": 2})
        vc_b, _ = self._seed_named_stack(
            "vce-shared", "building-b", {"vce-b1": 1, "vce-b2": 2})
        before = self._row_fields([vc_a.pk, vc_b.pk])

        r = self.client.post(
            self.apply_url,
            data={
                "id": str(uuid.uuid4()),
                "changes": [{
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.virtualchassis",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {"name": "vce-shared", "description": "FROM-A"},
                }],
            },
            format="json", **self.auth,
        )

        self.assertEqual(r.status_code, 400, r.content)
        error = r.json()["errors"]["dcim.virtualchassis"]["name"][0]
        self.assertIn(f"id {vc_a.pk}", error)
        self.assertIn(f"id {vc_b.pk}", error)
        self.assertEqual(self._row_fields([vc_a.pk, vc_b.pk]), before)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-shared").count(), 2)

    def test_a_stale_plan_applied_never_writes_the_row_it_matched(self):
        """
        The same contract through the real plan path, which is how it happens.

        The pre-save match exists for plans that went stale: a worker plans a
        member device whose chassis is named but not yet present, so its plan
        holds a masterless create dcim.virtualchassis carrying whatever the
        source says about the chassis. Between plan and apply, the row appears.

        The membership DOES bind -- the device joins the matched chassis -- and
        that is the point rather than a concession: binding is what keeps a
        second, permanently orphaned row out of the table. What must not travel
        with it is the payload's opinion of that row's own fields.
        """
        for kind in self.ROW_KINDS:
            with self.subTest(row=kind):
                name = f"vce-plan-{kind.replace('_', '-')}"
                dev_name = f"vce-a-{kind.replace('_', '-')}"
                payload = self._device_payload(dev_name, {
                    "vc_position": 9,
                    "virtual_chassis": {
                        # see the note above: no domain, so the row is not
                        # contradicted and the bind is what gets exercised
                        "name": name, "description": "FROM-A",
                    },
                })

                cs = self._diff(payload)
                vc_creates = [c for c in cs["changes"]
                              if c["object_type"] == "dcim.virtualchassis"
                              and c["change_type"] == "create"]
                self.assertEqual(len(vc_creates), 1, cs)
                self.assertIsNone(vc_creates[0]["data"].get("master"), vc_creates[0])

                # ...and only now does the chassis appear.
                vc = self._seed_row_kind(kind, name)
                before = self._row_fields([vc.pk])

                r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
                self.assertEqual(r.status_code, 200, r.content)
                self.assertIsNone(r.json().get("errors"))

                self.assertEqual(self._row_fields([vc.pk]), before,
                                 f"{kind}: a plan that never named this row wrote it")
                self.assertEqual(VirtualChassis.objects.filter(name=name).count(), 1,
                                 f"{kind}: the stale plan left a duplicate row")
                self.assertEqual(Device.objects.get(name=dev_name).virtual_chassis_id, vc.pk)
                self._assert_member_count_is_honest(vc)

    def test_a_stale_member_only_plan_leaves_no_orphan_chassis(self):
        """
        The routing's own headline case: a name-only plan, applied late.

        This is the shape the pre-save match was added for, and the one a
        row-level refusal breaks. A member device whose payload names the
        chassis and nothing else plans exactly
        create dcim.virtualchassis {"name": X} -- no master, no fields. Refuse
        that match and the apply inserts a SECOND masterless X, binds the
        member to it, and leaves it behind forever once a later round rebinds
        the member: same name, no master, no members, and no diff that ever
        mentions it, because ingest does not delete rows.

        Binding gives the parent commit's behaviour back exactly: one row, the
        member joins the converged chassis, its fields untouched, member_count
        truthful, and the immediate re-diff empty -- converged in one apply.
        """
        payload = self._device_payload("vce-late", {
            "vc_position": 2, "virtual_chassis": {"name": "vce-stack"},
        })
        cs = self._diff(payload)
        vc_creates = [c for c in cs["changes"]
                      if c["object_type"] == "dcim.virtualchassis"
                      and c["change_type"] == "create"]
        self.assertEqual(len(vc_creates), 1, cs)
        self.assertEqual(vc_creates[0]["data"].get("name"), "vce-stack")
        self.assertIsNone(vc_creates[0]["data"].get("master"), vc_creates[0])
        self.assertNotIn("description", vc_creates[0]["data"], vc_creates[0])

        # ...and only now does a converged, MASTERED vce-stack appear.
        vc, master = self._seed_stack()
        VirtualChassis.objects.filter(pk=vc.pk).update(
            description="CONVERGED-B", domain="dom-b"
        )
        vc.refresh_from_db()
        before = self._row_fields([vc.pk])

        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))

        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1,
                         "a second, permanently orphaned chassis was inserted")
        self.assertEqual(self._row_fields([vc.pk]), before)
        self.assertEqual(Device.objects.get(name="vce-late").virtual_chassis_id, vc.pk)
        self.assertEqual(VirtualChassis.objects.get(pk=vc.pk).member_count, 2)
        self._assert_member_count_is_honest(vc)
        self._assert_noop_rediff(payload)

    def test_three_stale_member_only_plans_leave_no_orphan_chassis(self):
        """
        Orphans accumulate one per stale plan, so more than one must be shown.

        A single stale plan hides the shape of the defect: refusing the match
        yields ONE extra row, which reads like a duplicate some later pass
        tidies up. Nothing tidies it up, and every additional planner adds
        another. Three plans, all built before the chassis existed, must still
        converge on one row.
        """
        plans = []
        for position, dev_name in enumerate(("vce-l1", "vce-l2", "vce-l3"), start=2):
            plans.append(self._diff(self._device_payload(dev_name, {
                "vc_position": position, "virtual_chassis": {"name": "vce-stack"},
            })))

        vc, _ = self._seed_stack()

        for cs in plans:
            r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
            self.assertEqual(r.status_code, 200, r.content)
            self.assertIsNone(r.json().get("errors"))

        rows = VirtualChassis.objects.filter(name="vce-stack")
        self.assertEqual(rows.count(), 1,
                         list(rows.values("pk", "master_id", "member_count")))
        for dev_name in ("vce-l1", "vce-l2", "vce-l3"):
            self.assertEqual(
                Device.objects.get(name=dev_name).virtual_chassis_id, vc.pk, dev_name
            )
        self.assertEqual(VirtualChassis.objects.get(pk=vc.pk).member_count, 4)
        self._assert_member_count_is_honest(vc)

    def test_a_bound_chassis_converges_its_fields_on_the_next_pass(self):
        """
        Refusing the write is a DEFERRAL, and the design is wrong unless it is.

        The fields a bound CREATE declines to write have to land eventually, or
        binding is just data loss. They do, and by the ordinary route: the row
        now exists, so the next generate-diff matches it and plans an UPDATE
        addressed by object_id -- the path that is entitled to write, because
        that plan was built against the row rather than guessing at it. One
        further round is all it takes.
        """
        payload = self._device_payload("vce-sw2", {
            "vc_position": 2,
            "virtual_chassis": {
                "name": "vce-stack", "description": "FROM-A",
            },
        })

        # Planned while no such chassis exists, so the plan holds a masterless
        # CREATE. Seeding the row first would have the matcher find it at PLAN
        # time and plan an UPDATE, which never exercises the bind.
        cs = self._diff(payload)
        vc_creates = [c for c in cs["changes"]
                      if c["object_type"] == "dcim.virtualchassis"
                      and c["change_type"] == "create"]
        self.assertEqual(len(vc_creates), 1, cs)
        vc = VirtualChassis.objects.create(name="vce-stack")

        # pass 1: the CREATE binds the row and declines its fields
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        fresh = VirtualChassis.objects.get(pk=vc.pk)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)
        self.assertEqual(fresh.description, "")
        self.assertEqual(fresh.domain, "")
        self.assertEqual(Device.objects.get(name="vce-sw2").virtual_chassis_id, vc.pk)

        # pass 2: the matcher finds the row, so this is an UPDATE naming it
        cs = self._diff(payload)
        vc_changes = [c for c in cs["changes"] if c["object_type"] == "dcim.virtualchassis"]
        self.assertEqual(len(vc_changes), 1, cs)
        self.assertEqual(vc_changes[0]["change_type"], "update", vc_changes[0])
        self.assertEqual(vc_changes[0]["object_id"], vc.pk, vc_changes[0])
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))

        fresh = VirtualChassis.objects.get(pk=vc.pk)
        self.assertEqual(fresh.description, "FROM-A")
        # Still empty because this payload never asserted a domain -- it must
        # not, or it would identify a different chassis and never bind at all.
        self.assertEqual(fresh.domain, "")

        # ...and that is convergence, not a flap: nothing left to plan.
        self._assert_noop_rediff(payload)

    def test_a_bound_chassis_is_referenceable_later_in_the_same_changeset(self):
        """
        A bound row must reach created[ref_id], or the changeset breaks.

        The find-first path returns the row it settled on and _apply_change
        stores it under the change's ref_id; every later change resolves its
        new_object references out of that dict. Binding returns an instance
        this request never saved, which is exactly the case where an
        implementation could plausibly return None ("nothing happened") and
        turn the next change into a KeyError -- surfaced as "unresolved
        reference", a 400 for a changeset that is perfectly valid.
        """
        vc = VirtualChassis.objects.create(name="vce-stack")

        r = self.client.post(
            self.apply_url,
            data={
                "id": str(uuid.uuid4()),
                "changes": [
                    {
                        "change_id": str(uuid.uuid4()),
                        "change_type": "create",
                        "object_version": None,
                        "object_type": "dcim.virtualchassis",
                        "object_id": None,
                        "ref_id": "vc-1",
                        "data": {"name": "vce-stack", "description": "FROM-A"},
                    },
                    {
                        "change_id": str(uuid.uuid4()),
                        "change_type": "create",
                        "object_version": None,
                        "object_type": "dcim.device",
                        "object_id": None,
                        "ref_id": "dev-1",
                        "new_refs": ["virtual_chassis"],
                        "data": {
                            "name": "vce-sw2",
                            "site": self.site.pk,
                            "role": self.role.pk,
                            "device_type": self.dt.pk,
                            "vc_position": 2,
                            "virtual_chassis": "vc-1",
                        },
                    },
                ],
            },
            format="json", **self.auth,
        )

        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))
        self.assertEqual(Device.objects.get(name="vce-sw2").virtual_chassis_id, vc.pk)
        self.assertEqual(VirtualChassis.objects.filter(name="vce-stack").count(), 1)
        # ...and the reference did not smuggle the payload in either
        self.assertEqual(VirtualChassis.objects.get(pk=vc.pk).description, "")


    # ---- bind, but still report the payload's errors ----------------------

    #: Over-length values for two VirtualChassis CharFields. Any serializer at
    #: all rejects these, which is the point: the question under test is
    #: whether a serializer runs, not which error it produces.
    TOO_LONG = (("description", "d" * 400), ("domain", "x" * 300))

    def test_an_invalid_masterless_create_on_a_matched_row_is_still_a_400(self):
        """
        Binding declines the WRITE. It must not also swallow the payload error.

        _try_bind_existing_instance hands the matched row back without saving,
        and the first draft of it ran no serializer at all -- so
        is_valid(raise_exception=True), the only thing that reports a bad
        payload, never fired. An INVALID masterless CREATE that matched an
        existing row therefore answered 200 with errors null while storing
        nothing, where the parent commit answered 400. Two harms, both real:
        the reconciler is told a change applied when nothing was stored, and
        the change no longer aborts its own changeset, so companion changes the
        parent rolled back are left half-landed. It is not permanent silence --
        the next generate-diff plans an UPDATE carrying the same invalid value
        and that 400s -- but it is a misreport, and a regression against base.

        The fix validates against the matched instance and discards the save,
        so both halves have to hold at once: the 400 comes back (here) and the
        row is still untouched (asserted here too, and across the full matrix
        in test_a_masterless_create_never_writes_the_row_it_matched).

        Every row kind and both masterless wire forms are covered, because the
        bind is reached identically for all six and a fix that only restored
        the error for one of them would be worse than useless.
        """
        for field, value in self.TOO_LONG:
            for kind in self.ROW_KINDS:
                for label, extra in (("master_absent", {}), ("master_null", {"master": None})):
                    with self.subTest(field=field, row=kind, master=label):
                        name = f"vce-inv-{field[:3]}-{kind.replace('_', '-')}-{label.replace('_', '-')}"
                        vc = self._seed_row_kind(kind, name)
                        before = self._row_fields([vc.pk])
                        data = {"name": name, field: value}
                        data.update(extra)

                        r = self.client.post(
                            self.apply_url,
                            data={
                                "id": str(uuid.uuid4()),
                                "changes": [{
                                    "change_id": str(uuid.uuid4()),
                                    "change_type": "create",
                                    "object_version": None,
                                    "object_type": "dcim.virtualchassis",
                                    "object_id": None,
                                    "ref_id": "1",
                                    "data": data,
                                }],
                            },
                            format="json", **self.auth,
                        )

                        self.assertEqual(r.status_code, 400, r.content)
                        errors = r.json()["errors"]
                        self.assertIn("dcim.virtualchassis", errors, errors)
                        self.assertIn(field, errors["dcim.virtualchassis"], errors)

                        # ...and rejecting it wrote nothing either.
                        self.assertEqual(self._row_fields([vc.pk]), before,
                                         f"{field}/{kind}/{label}: the rejected create wrote the row")
                        self.assertEqual(
                            VirtualChassis.objects.filter(name=name).count(), 1,
                            f"{field}/{kind}/{label}: the rejected create inserted a row",
                        )
                        self._assert_member_count_is_honest(vc)

    def test_a_valid_masterless_create_on_a_matched_row_is_200_and_writes_nothing(self):
        """
        The other half of the same seam, stated next to it.

        Restoring the serializer must not turn a legal payload into an error,
        and running the serializer must not turn a bind into a write. The
        values here are well inside both columns' limits and are DIFFERENT from
        the seeded row's, so a save of any kind would show up in _row_fields --
        last_updated included, which is what separates "untouched" from "saved
        with values identical to its own".
        """
        for kind in self.ROW_KINDS:
            for label, extra in (("master_absent", {}), ("master_null", {"master": None})):
                with self.subTest(row=kind, master=label):
                    name = f"vce-val-{kind.replace('_', '-')}-{label.replace('_', '-')}"
                    vc = self._seed_row_kind(kind, name)
                    before = self._row_fields([vc.pk])
                    # No domain asserted on purpose: this payload must stay
                    # INDISTINGUISHABLE from the row it races, which is what
                    # makes the bind-only contract the thing under test. A
                    # payload asserting a domain the row does not carry is a
                    # different case entirely -- it identifies a different
                    # chassis and gets its own row (see
                    # test_a_contradicting_domain_takes_its_own_row).
                    data = {"name": name, "description": "FROM-A"}
                    data.update(extra)

                    r = self.client.post(
                        self.apply_url,
                        data={
                            "id": str(uuid.uuid4()),
                            "changes": [{
                                "change_id": str(uuid.uuid4()),
                                "change_type": "create",
                                "object_version": None,
                                "object_type": "dcim.virtualchassis",
                                "object_id": None,
                                "ref_id": "1",
                                "data": data,
                            }],
                        },
                        format="json", **self.auth,
                    )

                    self.assertEqual(r.status_code, 200, r.content)
                    self.assertIsNone(r.json().get("errors"))
                    self.assertEqual(self._row_fields([vc.pk]), before,
                                     f"{kind}/{label}: validating the payload also saved it")
                    self.assertEqual(VirtualChassis.objects.filter(name=name).count(), 1,
                                     f"{kind}/{label}: the CREATE left a duplicate row behind")
                    self._assert_member_count_is_honest(vc)

    def test_an_invalid_masterless_create_with_no_matching_row_is_still_a_400(self):
        """
        The control: with nothing to bind, the error was never in question.

        This is the arm that localises the bug to the bind. It 400s on the
        parent commit, on the first draft of this branch, and here, because it
        goes through _create_or_find_instance's own serializer. If it ever
        diverged from the matched-row arms above, the difference would be
        somewhere other than the seam this fix touches.
        """
        for field, value in self.TOO_LONG:
            with self.subTest(field=field):
                name = f"vce-nomatch-{field[:3]}"
                r = self.client.post(
                    self.apply_url,
                    data={
                        "id": str(uuid.uuid4()),
                        "changes": [{
                            "change_id": str(uuid.uuid4()),
                            "change_type": "create",
                            "object_version": None,
                            "object_type": "dcim.virtualchassis",
                            "object_id": None,
                            "ref_id": "1",
                            "data": {"name": name, field: value},
                        }],
                    },
                    format="json", **self.auth,
                )
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn(field, r.json()["errors"]["dcim.virtualchassis"], r.content)
                self.assertFalse(VirtualChassis.objects.filter(name=name).exists())


class BindOnlyReachabilityTests(SimpleTestCase):
    """
    The no-write guarantee covers the CREATE path. This pins its edge.

    _try_bind_existing_instance writes nothing to the row it binds. The UPDATE
    branch of _apply_change that resolves through created[ref_id] is an
    ordinary serializer.save(), so a changeset pairing a create with a
    ref_id-only update of the SAME object_type (object_id null) writes that
    update's payload onto the bound row. The parent commit does the same;
    origin/develop does not, because it inserts a duplicate and writes to that
    instead -- so it is a divergence from develop in the destructive direction,
    and the only reason it is a documentation item rather than a defect is that
    nothing plannable emits that shape.

    The same-type scoping is load-bearing. A ref_id-only update of a DIFFERENT
    object_type also matches the parent, but that is a property of
    applier._instance_for_deferred_update rather than of this branch, and
    DeferredUpdateRefTypeTests is what pins it.

    That reachability rests on ONE fact: transformer._IS_CIRCULAR_REFERENCE is
    what makes generate-diff split a create from a ref_id-only update, and no
    bind-only type is in it (measured: 0 of 960 planned changes across three
    matrix runs was a VC update with no object_id). Adding one there would make
    the gap reachable from ordinary ingest, silently.

    A runtime guard was considered and rejected: silently skipping a write a
    client changeset explicitly asked for is a new, undocumented behaviour, and
    it would mask the intent instead of surfacing it. A seam test costs nothing
    at runtime and fails loudly the moment the assumption stops holding, which
    is the behaviour that is actually wanted here.
    """

    def test_bind_only_types_are_not_circular_references(self):
        """No bind-only type may be split into a create plus a ref_id update."""
        overlap = _PRE_SAVE_MATCH_BIND_ONLY & set(_IS_CIRCULAR_REFERENCE)
        self.assertEqual(
            overlap, set(),
            "A bind-only type gained a deferred reference. _apply_change's "
            "ref_id UPDATE branch saves, so generate-diff can now write the "
            "row a CREATE only bound. Decide that deliberately before "
            "relaxing this test.",
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
