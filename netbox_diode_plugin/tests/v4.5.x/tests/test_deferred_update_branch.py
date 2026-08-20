"""The applier's deferred (ref_id) UPDATE branch: what it re-reads, and what that costs."""
import uuid
from types import SimpleNamespace
from unittest import mock

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
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
