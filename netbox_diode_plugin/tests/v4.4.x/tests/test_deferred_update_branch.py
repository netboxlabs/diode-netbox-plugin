"""The applier's deferred (ref_id) UPDATE branch: what it re-reads, and what that costs."""
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.test import TestCase

from netbox_diode_plugin.api.applier import _carry_forward_relation_cache


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
    _IS_CIRCULAR_REFERENCE routes ten shapes through this branch, including
    dcim.interface.primary_mac_address -- every mac-bearing interface in an
    ingest. Measured on a 48-interface /bulk-plan-apply/: the re-read alone was
    +7 queries per interface, of which four were an unrelated changelog
    snapshot and two were full-row re-fetches of dcim_device and dcim_site that
    the CREATE's instance already had in hand. Carrying the cache forward is
    what brings the branch to the +1 the re-read itself costs.
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
        known consumer of that (ModularComponentModel.save's _site
        denormalisation).
        """
        stale = self._instance_with_loaded_relations()
        Interface.objects.filter(pk=self.iface.pk).update(device=self.other)
        fresh = Interface.objects.get(pk=self.iface.pk)

        _carry_forward_relation_cache(stale, fresh)

        self.assertEqual(fresh.device_id, self.other.pk)
        self.assertEqual(fresh.device.pk, self.other.pk)
