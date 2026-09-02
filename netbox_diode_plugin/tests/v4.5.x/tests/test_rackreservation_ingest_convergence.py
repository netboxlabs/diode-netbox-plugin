#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""RackReservation ingest convergence: overlap identity end-to-end.

All rack payloads here carry a location and the rack is pre-seeded with it,
so the rack ref re-matches on every cycle; the location-less rack shape has
a separate, known matching gap and is deliberately not used.
"""

from dcim.models import Location, Rack, RackReservation, Site
from django.test import TestCase
from tenancy.models import Tenant
from users.models import User

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import ChangeSetException
from netbox_diode_plugin.api.differ import generate_changeset


class RackReservationConvergenceTestCase(TestCase):
    """Diff+apply round-trips for the unit-overlap identity."""

    @classmethod
    def setUpTestData(cls):
        """Location-bearing rack and the reservation's user, pre-seeded."""
        cls.site = Site.objects.create(name="rrc-site", slug="rrc-site")
        cls.location = Location.objects.create(
            name="rrc-loc", slug="rrc-loc", site=cls.site
        )
        cls.rack = Rack.objects.create(
            name="rrc-rack", site=cls.site, location=cls.location
        )
        cls.user = User.objects.create(username="rrc-owner")
        cls.user2 = User.objects.create(username="rrc-owner2")
        cls.tenant = Tenant.objects.create(name="rrc-tenant", slug="rrc-tenant")

    def _entity(self, units, description="reserved", username="rrc-owner", tenant=None, status=None):
        entity = {
            "rack": {
                "name": "rrc-rack",
                "site": {"name": "rrc-site"},
                "location": {"name": "rrc-loc", "site": {"name": "rrc-site"}},
            },
            "units": units,
            "description": description,
            "user": {"username": username},
        }
        if tenant is not None:
            entity["tenant"] = {"name": tenant}
        if status is not None:
            entity["status"] = status
        return entity

    def _plan(self, entity):
        return generate_changeset(entity, "dcim.rackreservation").change_set

    def _apply(self, change_set):
        return apply_changeset(change_set, request=None)

    def _ingest(self, entity):
        self._apply(self._plan(entity))

    def test_identical_reingest_is_all_noop(self):
        """The convergence bug: a second identical ingest must plan nothing."""
        self._ingest(self._entity([1, 2, 3]))
        cs = self._plan(self._entity([1, 2, 3]))
        non_noop = [c for c in cs.changes if c.change_type.value != "noop"]
        self.assertEqual(non_noop, [], [c.to_dict() for c in non_noop])
        # and the rack itself re-matched (guards the test's own premise)
        self.assertEqual(Rack.objects.filter(name="rrc-rack").count(), 1)
        self.assertEqual(RackReservation.objects.count(), 1)

    def test_grow_units_updates_in_place(self):
        """[1, 2] -> [1, 2, 3] is an UPDATE of the same row."""
        self._ingest(self._entity([1, 2]))
        rr = RackReservation.objects.get()
        cs = self._plan(self._entity([1, 2, 3]))
        updates = [c for c in cs.changes
                   if c.object_type == "dcim.rackreservation"]
        self.assertEqual(len(updates), 1, [c.to_dict() for c in cs.changes])
        self.assertEqual(updates[0].change_type.value, "update")
        self.assertEqual(updates[0].object_id, rr.pk)
        self._apply(cs)
        rr.refresh_from_db()
        self.assertEqual(sorted(rr.units), [1, 2, 3])
        self.assertEqual(RackReservation.objects.count(), 1)

    def test_shrink_units_updates_in_place(self):
        """[1, 2, 3] -> [1, 2] shrinks the stored set (last writer wins)."""
        self._ingest(self._entity([1, 2, 3]))
        rr = RackReservation.objects.get()
        cs = self._plan(self._entity([1, 2]))
        updates = [c for c in cs.changes
                   if c.object_type == "dcim.rackreservation"]
        self.assertEqual(len(updates), 1, [c.to_dict() for c in cs.changes])
        self.assertEqual(updates[0].change_type.value, "update")
        self.assertEqual(updates[0].object_id, rr.pk)
        self._apply(cs)
        rr.refresh_from_db()
        self.assertEqual(sorted(rr.units), [1, 2])
        self.assertEqual(RackReservation.objects.count(), 1)

    def test_disjoint_units_create_second_reservation(self):
        """No shared unit means a different reservation, any user."""
        self._ingest(self._entity([1, 2]))
        self._ingest(self._entity([5, 6], username="rrc-owner2"))
        self.assertEqual(RackReservation.objects.count(), 2)
        by_units = {tuple(sorted(r.units)): r for r in RackReservation.objects.all()}
        self.assertEqual(by_units[(1, 2)].user_id, self.user.pk)
        self.assertEqual(by_units[(5, 6)].user_id, self.user2.pk)

    def test_field_changes_flow_through_on_match(self):
        """description/user/tenant/status updates ride the overlap match."""
        self._ingest(self._entity([1, 2], description="old"))
        self._ingest(self._entity(
            [1, 2],
            description="new",
            username="rrc-owner2",
            tenant="rrc-tenant",
            status="pending"
        ))
        rr = RackReservation.objects.get()
        self.assertEqual(rr.description, "new")
        self.assertEqual(rr.user_id, self.user2.pk)
        self.assertEqual(rr.tenant_id, self.tenant.pk)
        self.assertEqual(rr.status, "pending")

    def test_multi_overlap_fails_at_plan_time(self):
        """A payload spanning two reservations is refused loudly at plan."""
        RackReservation.objects.create(
            rack=self.rack, units=[1], user=self.user, description="a"
        )
        RackReservation.objects.create(
            rack=self.rack, units=[2], user=self.user, description="b"
        )
        with self.assertRaises(ChangeSetException) as cm:
            self._plan(self._entity([1, 2]))
        self.assertIn("overlap", str(cm.exception))
        self.assertEqual(RackReservation.objects.count(), 2)  # nothing changed

    def test_batch_invariance_overlapping_plans_converge_to_one_row(self):
        """Two overlapping CREATEs planned together adopt, last writer wins."""
        cs_a = self._plan(self._entity([1, 2], description="first"))
        cs_b = self._plan(self._entity([2, 3], description="second"))
        self._apply(cs_a)
        self._apply(cs_b)  # pre-save re-match adopts the row cs_a created
        rr = RackReservation.objects.get()
        self.assertEqual(sorted(rr.units), [2, 3])
        self.assertEqual(rr.description, "second")

    def test_stale_create_adopts_existing_reservation(self):
        """A CREATE planned before the row existed applies onto it, not beside it."""
        cs = self._plan(self._entity([1, 2], description="from-plan"))
        RackReservation.objects.create(
            rack=self.rack, units=[1, 2], user=self.user, description="raced-in"
        )
        self._apply(cs)
        rr = RackReservation.objects.get()
        self.assertEqual(rr.description, "from-plan")  # payload applied
        self.assertEqual(sorted(rr.units), [1, 2])
