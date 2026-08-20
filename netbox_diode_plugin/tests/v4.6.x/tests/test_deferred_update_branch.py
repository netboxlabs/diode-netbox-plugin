"""The applier's deferred (ref_id) UPDATE branch: what it re-reads, and what that costs."""
import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectChange
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    InterfaceTemplate,
    Manufacturer,
    Site,
    VirtualChassis,
)
from django.test import TestCase
from utilities.testing import APITestCase

from netbox_diode_plugin.api.applier import (
    _carry_forward_relation_cache,
    _instance_for_deferred_update,
)
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class CarryForwardRelationCacheTests(TestCase):
    """
    The deferred UPDATE re-reads its row; re-reading must not re-fetch its FKs.

    _apply_change's ref_id UPDATE branch replaced the CREATE's in-memory
    instance with a fresh read of the row, because the counter machinery
    decides from the instance's change tracker and a stale instance makes it
    double-count (test_virtualchassis_ingest covers that end of it). What the
    row's own columns must be fresh for, the rows its FKs point AT do not: a
    fresh load starts with an empty _state.fields_cache, so every forward FK
    the serializer's validators or the model's own save() touch is fetched
    again.

    That cost lands on far more than VirtualChassis. transformer's
    _IS_CIRCULAR_REFERENCE routes thirteen (type, field) shapes over seven
    types through this branch, including dcim.interface.primary_mac_address --
    every mac-bearing interface in an ingest. Measured on a 48-interface
    /bulk-plan-apply/, per deferred update, and stated as three separate
    numbers because the first draft of this branch conflated them: the re-read
    ITSELF costs +3 (2758 -> 2902 over 48, 79 -> 82 on one); the changelog
    snapshot that first draft also took cost a further +4 and has since been
    dropped; and carrying the relation cache forward gives back 2 of the 3
    (2902 -> 2806, 82 -> 80), those two being full-row re-fetches of dcim_device
    and dcim_site the CREATE's instance already had in hand. +7 was the whole
    first draft, not the re-read. What ships is +1 per deferred update.
    """

    def setUp(self):
        """Two devices in one site, and an interface on the first."""
        self.site = Site.objects.create(name="dub-site", slug="dub-site")
        mfr = Manufacturer.objects.create(name="dub-mfr", slug="dub-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="dub-dt", slug="dub-dt")
        self.role = DeviceRole.objects.create(name="dub-role", slug="dub-role")
        self.dev = Device.objects.create(
            name="dub-sw1", site=self.site, device_type=self.dt, role=self.role
        )
        self.other = Device.objects.create(
            name="dub-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        self.iface = Interface.objects.create(
            device=self.dev, name="eth0", type="1000base-t"
        )

    def _instance_with_loaded_relations(self):
        """Stand in for the CREATE's instance: device, and device.site, attached."""
        stale = Interface.objects.select_related("device__site").get(pk=self.iface.pk)
        self.assertEqual(stale.device.site.pk, self.site.pk)
        return stale

    def test_a_fresh_read_alone_pays_for_the_relations_again(self):
        """The cost being removed, measured on the same objects as the test below."""
        fresh = Interface.objects.get(pk=self.iface.pk)
        with self.assertNumQueries(2):
            self.assertEqual(fresh.device.pk, self.dev.pk)
            self.assertEqual(fresh.device.site.pk, self.site.pk)

    def test_carried_forward_relations_cost_nothing(self):
        """After the carry-forward the same two accesses are free."""
        stale = self._instance_with_loaded_relations()
        fresh = Interface.objects.get(pk=self.iface.pk)

        _carry_forward_relation_cache(stale, fresh)

        with self.assertNumQueries(0):
            self.assertEqual(fresh.device.pk, self.dev.pk)
            self.assertEqual(fresh.device.site.pk, self.site.pk)

    def test_the_row_s_own_columns_still_come_from_the_database(self):
        """
        Only _state.fields_cache is carried, never a column and never the tracker.

        The re-read exists so the change tracker starts from the row's real
        state; a carry-forward that touched model attributes would undo the
        thing it is decorating.
        """
        stale = self._instance_with_loaded_relations()
        Interface.objects.filter(pk=self.iface.pk).update(description="written by a signal")
        fresh = Interface.objects.get(pk=self.iface.pk)

        _carry_forward_relation_cache(stale, fresh)

        self.assertEqual(fresh.description, "written by a signal")
        self.assertEqual(stale.description, "")

    def test_an_fk_the_database_has_moved_is_not_paired_with_the_old_object(self):
        """
        A carried object must match the column, or it is not carried.

        This is the one hazard the guard does cover, and the guard is a COLUMN
        comparison: it cannot notice the target row's own contents changing
        under the stale instance. See _carry_forward_relation_cache for the
        known consumer of that -- ComponentModel.save's _site / _location /
        _rack denormalisation, on the base of every device component, not
        ModularComponentModel, which defines no save() of its own.
        """
        stale = self._instance_with_loaded_relations()
        Interface.objects.filter(pk=self.iface.pk).update(device=self.other)
        fresh = Interface.objects.get(pk=self.iface.pk)

        _carry_forward_relation_cache(stale, fresh)

        self.assertEqual(fresh.device_id, self.other.pk)
        self.assertEqual(fresh.device.pk, self.other.pk)


class DeferredUpdateRefTypeTests(APITestCase):
    """
    The re-read is a PK lookup, so it must only run for the ref's own type.

    _apply_change's ref_id UPDATE branch replaced the CREATE's in-memory
    instance with model_class.objects.get(pk=created[ref_id].pk). A pk only
    identifies a row WITHIN a type: point a ref_id-only UPDATE at a CREATE of a
    different object_type and that lookup lands on whatever row of the update's
    OWN type happens to carry the other type's pk -- an uninvolved bystander,
    which the payload is then written to. The parent commit wrote the payload
    onto the (wrongly typed) object the ref actually named, so re-reading
    unconditionally was a divergence from it in the destructive direction.

    Nothing plannable gets here -- differ.diff_to_change takes ref_id from the
    entity node's own id, so a create and its deferred update are one node and
    one type -- which is exactly why this needs a hand-built changeset to pin.
    """

    def setUp(self):
        """Mock OAuth2 introspection so the Diode endpoints accept requests."""
        super().setUp()
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

    def test_a_cross_type_ref_never_writes_a_bystander_row(self):
        """
        A Site planted at the new chassis's pk must come out untouched.

        The plant is what makes the assertion meaningful: without a row at that
        pk the lookup raises DoesNotExist and the changeset merely 400s, which
        would pass whether the guard existed or not.
        """
        probe = VirtualChassis.objects.create(name="dur-probe")
        bystander = Site.objects.create(
            pk=probe.pk + 1, name="dur-bystander", slug="dur-bystander",
            description="UNTOUCHED",
        )

        response = self.client.post(
            self.apply_url,
            data={
                "id": str(uuid.uuid4()),
                "changes": [
                    {
                        "change_id": str(uuid.uuid4()), "change_type": "create",
                        "object_version": None, "object_type": "dcim.virtualchassis",
                        "object_id": None, "ref_id": "1", "data": {"name": "dur-vc"},
                    },
                    {
                        "change_id": str(uuid.uuid4()), "change_type": "update",
                        "object_version": None, "object_type": "dcim.site",
                        "object_id": None, "ref_id": "1",
                        "data": {"description": "CROSSTYPE-WRITE"},
                    },
                ],
            },
            format="json", **self.auth,
        )

        self.assertEqual(response.status_code, 200, response.content)
        created_vc = VirtualChassis.objects.get(name="dur-vc")
        self.assertEqual(
            created_vc.pk, bystander.pk,
            "the pk collision this test depends on did not happen",
        )
        bystander.refresh_from_db()
        self.assertEqual(
            bystander.description, "UNTOUCHED",
            "a cross-type ref_id wrote an uninvolved row",
        )
        # The other half of "byte-identical to the parent": the parent did not
        # merely leave the bystander alone, it wrote the payload onto the object
        # the ref actually names. Asserting only the negative would also pass on
        # a version of this branch that quietly dropped the write.
        self.assertEqual(
            created_vc.description, "CROSSTYPE-WRITE",
            "the ref's own object did not receive the deferred payload",
        )


class InstanceForDeferredUpdateTests(TestCase):
    """
    The helper directly, so the guard is pinned in both directions.

    The end-to-end test above fails if the guard is removed. It does NOT fail
    if the guard is inverted or widened into "never re-read", because the
    cross-type path is then still correct -- what breaks is the counter fix the
    re-read exists for, and that is asserted in test_virtualchassis_ingest, a
    different file. These three assertions keep the whole contract in one
    place: re-read for the ref's own type, hand back the instance untouched for
    any other, and carry the stale instance's loaded relations across the
    re-read.
    """

    def setUp(self):
        """One device in one site, and an interface on it."""
        self.site = Site.objects.create(name="ifdu-site", slug="ifdu-site")
        mfr = Manufacturer.objects.create(name="ifdu-mfr", slug="ifdu-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="ifdu-dt", slug="ifdu-dt")
        role = DeviceRole.objects.create(name="ifdu-role", slug="ifdu-role")
        self.dev = Device.objects.create(
            name="ifdu-sw1", site=self.site, device_type=dt, role=role
        )

    def test_same_type_is_read_back_from_the_database(self):
        """The point of the branch: the returned row's columns come from the DB."""
        vc = VirtualChassis.objects.create(name="ifdu-vc", description="PERSISTED")
        vc.description = "IN MEMORY ONLY"

        fresh = _instance_for_deferred_update(vc, VirtualChassis)

        self.assertIsNot(fresh, vc)
        self.assertEqual(fresh.description, "PERSISTED")

    def test_a_cross_type_ref_is_handed_back_untouched(self):
        """A pk from another type's sequence is not looked up as this type."""
        vc = VirtualChassis.objects.create(name="ifdu-cross")

        self.assertIs(_instance_for_deferred_update(vc, Site), vc)

    def test_the_re_read_keeps_the_relations_the_stale_instance_had_loaded(self):
        """The carry-forward is wired into the helper, not just available beside it."""
        iface = Interface.objects.create(
            device=self.dev, name="ifdu-eth0", type="1000base-t"
        )
        self.assertTrue(Interface._meta.get_field("device").is_cached(iface))

        fresh = _instance_for_deferred_update(iface, Interface)

        self.assertIsNot(fresh, iface)
        self.assertTrue(
            Interface._meta.get_field("device").is_cached(fresh),
            "the re-read dropped a relation the CREATE's instance already had",
        )


    def test_the_re_read_keeps_the_prechange_snapshot_the_create_took(self):
        """
        The changelog record must survive the re-read, not only the relations.

        snapshot_for_apply attaches _prechange_snapshot to the instance it is
        given, and the CREATE path calls it whenever a CREATE resolves onto an
        existing row. The parent commit's deferred UPDATE saved THAT instance,
        so NetBox recorded prechange_data; a fresh read carries no such
        attribute. DeferredUpdateChangelogTests measures what that costs
        end-to-end -- this pins the mechanism.
        """
        vc = VirtualChassis.objects.create(name="ifdu-snap", description="BEFORE")
        vc._prechange_snapshot = {"description": "BEFORE"}

        fresh = _instance_for_deferred_update(vc, VirtualChassis)

        self.assertIsNot(fresh, vc)
        self.assertEqual(
            getattr(fresh, "_prechange_snapshot", None), {"description": "BEFORE"},
            "the re-read dropped the prechange snapshot the CREATE path took",
        )

    def test_no_snapshot_is_invented_when_the_create_took_none(self):
        """A plain CREATE takes no snapshot, and the re-read must not add one."""
        vc = VirtualChassis.objects.create(name="ifdu-nosnap")

        fresh = _instance_for_deferred_update(vc, VirtualChassis)

        self.assertFalse(hasattr(fresh, "_prechange_snapshot"))


class DeferredUpdateChangelogTests(APITestCase):
    """
    What the deferred UPDATE records in the changelog, on a planned shape.

    The re-read this branch added replaces the CREATE's instance, and the
    CREATE's instance is where snapshot_for_apply left its prechange. Dropping
    it changes the audit trail twice over: an update that persists something
    records prechange_data null instead of the row's before-state, and (through
    NetBox's ObjectChange.has_changes gate, which compares prechange with
    postchange) an update that persists NOTHING goes from being dropped to
    being recorded. Neither belongs in a counter fix, and both are measured
    here rather than argued: without _carry_forward_prechange_snapshot the
    first test sees prechange_data null and the second sees one row instead of
    none, on v4.4, v4.5 and v4.6 alike.
    """

    def setUp(self):
        """Mock OAuth2 introspection, and a device type with an eth0 template."""
        super().setUp()
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
        self.site = Site.objects.create(name="duc-site", slug="duc-site")
        self.mfr = Manufacturer.objects.create(name="duc-mfr", slug="duc-mfr")
        self.dt = DeviceType.objects.create(
            manufacturer=self.mfr, model="duc-dt", slug="duc-dt"
        )
        self.role = DeviceRole.objects.create(name="duc-role", slug="duc-role")

    def _interface_changes(self, iface):
        """This interface's changelog rows, oldest first."""
        return list(
            ObjectChange.objects.filter(
                changed_object_type__app_label="dcim",
                changed_object_type__model="interface",
                changed_object_id=iface.pk,
            ).order_by("pk")
        )

    def test_a_planned_deferred_update_records_its_prechange(self):
        """
        The PR's own headline cost shape, and its changelog.

        The device type carries an eth0 InterfaceTemplate, so the device CREATE
        auto-creates eth0 within this same apply; the interface CREATE then
        matches that row through the auto-created-component find-first path
        (which snapshots), and primary_mac_address is deferred to a ref_id
        UPDATE. Fully plan-reachable: generate-diff emits exactly this.
        """
        InterfaceTemplate.objects.create(
            device_type=self.dt, name="eth0", type="1000base-t"
        )
        payload = {"entities": [{
            "id": "duc-1",
            "object_type": "dcim.interface",
            "entity": {"interface": {
                "name": "eth0",
                "type": "1000base-t",
                "description": "FROM-INGEST",
                "device": {
                    "name": "duc-sw1",
                    "role": {"name": "duc-role"},
                    "site": {"name": "duc-site"},
                    "device_type": {
                        "manufacturer": {"name": "duc-mfr"}, "model": "duc-dt",
                    },
                },
                "primary_mac_address": {"mac_address": "00:00:00:00:00:34"},
            }},
        }]}

        response = self.client.post(
            self.bulk_plan_apply_url, data=payload, format="json", **self.auth
        )

        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()["results"][0]
        self.assertIsNone(result.get("errors"), result)
        # Pin the shape this test depends on: the interface's primary_mac_address
        # really is a ref_id UPDATE, not an object_id one.
        deferred = [c for c in result["change_set"]["changes"]
                    if c["object_type"] == "dcim.interface"
                    and c["change_type"] == "update"]
        self.assertEqual(len(deferred), 1, result["change_set"])
        self.assertIsNone(deferred[0]["object_id"], deferred[0])
        self.assertIsNotNone(deferred[0]["ref_id"], deferred[0])

        iface = Interface.objects.get(device__name="duc-sw1", name="eth0")
        self.assertIsNotNone(iface.primary_mac_address_id)
        last = self._interface_changes(iface)[-1]
        self.assertEqual(last.action, "update", last)
        self.assertIsNotNone(
            last.prechange_data,
            "the deferred update recorded no prechange_data at all",
        )
        self.assertEqual(
            sorted(k for k in last.postchange_data
                   if last.prechange_data.get(k) != last.postchange_data.get(k)),
            ["description", "primary_mac_address"],
            last.prechange_data,
        )

    def test_a_deferred_update_that_persists_nothing_records_nothing(self):
        """
        The has_changes gate: no write, no changelog row.

        Both writes in this changeset store what is already stored, so the
        prechange equals the postchange and NetBox drops the row. It can only
        do that if the prechange is there to compare.
        """
        device = Device.objects.create(
            name="duc-sw2", site=self.site, device_type=self.dt, role=self.role
        )
        iface = Interface.objects.create(
            device=device, name="eth0", type="1000base-t",
            description="SAME", label="SAMELABEL",
        )

        def change(change_type, data):
            return {
                "id": str(uuid.uuid4()), "change_type": change_type,
                "object_type": "dcim.interface", "object_id": None,
                "ref_id": "1", "data": data, "new_refs": [],
            }

        response = self.client.post(
            self.apply_url,
            data={"id": str(uuid.uuid4()), "changes": [
                change("create", {
                    "device": device.pk, "name": "eth0", "type": "1000base-t",
                    "description": "SAME", "label": "SAMELABEL",
                }),
                change("update", {"label": "SAMELABEL"}),
            ]},
            format="json", **self.auth,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json().get("errors"))
        self.assertEqual(
            Interface.objects.filter(device=device, name="eth0").count(), 1,
            "the matched CREATE inserted a duplicate",
        )
        self.assertEqual(
            self._interface_changes(iface), [],
            "a changeset that stored nothing wrote a changelog row",
        )
