#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Rack ingest convergence: site-scoped identity end-to-end."""

from dcim.models import Location, Rack, RackReservation, Site
from django.test import TestCase
from users.models import User

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import ChangeType
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.matcher import AmbiguousObjectMatch


class RackConvergenceTestCase(TestCase):
    """Plan+apply round-trips for location-less rack identity."""

    @classmethod
    def setUpTestData(cls):
        """Site, location, user pre-seeded; racks vary per test."""
        cls.site = Site.objects.create(name="rc-site", slug="rc-site")
        cls.location = Location.objects.create(
            name="rc-loc", slug="rc-loc", site=cls.site
        )
        cls.user = User.objects.create(username="rc-owner")

    def _reservation_entity(self, rack_extra=None):
        rack = {"name": "rc-r1", "site": {"name": "rc-site"}}
        rack.update(rack_extra or {})
        return {"rack": rack, "units": [1, 2], "description": "rc",
                "user": {"username": "rc-owner"}}

    def _rack_entity(self, extra=None):
        entity = {"name": "rc-r1", "site": {"name": "rc-site"}}
        entity.update(extra or {})
        return entity

    def _plan(self, entity, object_type):
        return generate_changeset(entity, object_type).change_set

    def _apply(self, cs):
        return apply_changeset(cs, request=None)

    def _ingest(self, entity, object_type):
        self._apply(self._plan(entity, object_type))

    def test_location_less_rack_reingest_converges(self):
        """The original bug: nested location-less rack duplicated per cycle."""
        self._ingest(self._reservation_entity(), "dcim.rackreservation")
        cs = self._plan(self._reservation_entity(), "dcim.rackreservation")
        non_noop = [c for c in cs.changes if c.change_type != ChangeType.NOOP]
        self.assertEqual(non_noop, [], [c.to_dict() for c in non_noop])
        self.assertEqual(Rack.objects.count(), 1)
        self.assertEqual(RackReservation.objects.count(), 1)

    def test_binds_operator_located_rack_location_untouched(self):
        """A location-less ref binds the located rack and leaves its location."""
        rack = Rack.objects.create(
            name="rc-r1", site=self.site, location=self.location
        )
        cs = self._plan(self._reservation_entity(), "dcim.rackreservation")
        rack_creates = [c for c in cs.changes
                        if c.object_type == "dcim.rack"
                        and c.change_type == ChangeType.CREATE]
        self.assertEqual(rack_creates, [], [c.to_dict() for c in rack_creates])
        self._apply(cs)
        rack.refresh_from_db()
        self.assertEqual(rack.location_id, self.location.pk)
        self.assertEqual(Rack.objects.count(), 1)

    def test_two_populated_racks_fail_at_plan(self):
        """Genuine ambiguity refuses loudly, DB untouched."""
        loc2 = Location.objects.create(name="rc-loc2", slug="rc-loc2", site=self.site)
        a = Rack.objects.create(name="rc-r1", site=self.site, location=self.location)
        b = Rack.objects.create(name="rc-r1", site=self.site, location=loc2)
        RackReservation.objects.create(rack=a, units=[1], user=self.user, description="a")
        RackReservation.objects.create(rack=b, units=[2], user=self.user, description="b")
        with self.assertRaises(AmbiguousObjectMatch) as cm:
            self._plan(self._reservation_entity(), "dcim.rackreservation")
        self.assertIn(str(a.pk), str(cm.exception))
        self.assertIn(str(b.pk), str(cm.exception))
        self.assertEqual(Rack.objects.count(), 2)

    def test_explicit_null_does_not_adopt_located_rack(self):
        """With only a located rack, explicit null creates its own null rack."""
        located = Rack.objects.create(
            name="rc-r1", site=self.site, location=self.location
        )
        self._ingest(self._rack_entity({"location": None}), "dcim.rack")
        located.refresh_from_db()
        self.assertEqual(located.location_id, self.location.pk)
        self.assertEqual(Rack.objects.count(), 2)
        # third pass converges onto the null rack (all-NOOP)
        cs = self._plan(self._rack_entity({"location": None}), "dcim.rack")
        non_noop = [c for c in cs.changes if c.change_type != ChangeType.NOOP]
        self.assertEqual(non_noop, [], [c.to_dict() for c in non_noop])

    def test_stale_locationless_create_binds_only(self):
        """Bind-only: the operator's distinctive fields survive a stale CREATE."""
        cs = self._plan(self._rack_entity(), "dcim.rack")
        rack = Rack.objects.create(
            name="rc-r1", site=self.site, status="reserved", width=23, u_height=48
        )
        result = self._apply(cs)
        rack.refresh_from_db()
        self.assertEqual(Rack.objects.count(), 1)
        self.assertEqual(rack.status, "reserved")
        self.assertEqual(rack.width, 23)
        self.assertEqual(rack.u_height, 48)
        warnings = result.to_dict().get("warnings") or []
        self.assertEqual(len(warnings), 1, warnings)
        self.assertEqual(warnings[0]["object_type"], "dcim.rack")
        self.assertEqual(warnings[0]["object_id"], rack.pk)
        self.assertTrue(
            {"status", "width", "u_height"} <= set(warnings[0]["fields"]),
            warnings,
        )
