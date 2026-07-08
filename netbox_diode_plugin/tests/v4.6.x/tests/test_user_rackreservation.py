#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - User (match-only) + RackReservation ingest tests."""

from dcim.models import RackReservation
from django.test import TestCase
from users.models import User

from netbox_diode_plugin.api import transformer
from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeSetException, ChangeType
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.supported_models import extract_supported_models


class MatchOnlyUserTransformTestCase(TestCase):
    """The plan path (transformer) never creates a user for an unresolved ref."""

    def test_unknown_user_ref_deviates_not_create(self):
        """An unresolved users.user ref raises a deviation, not a CREATE."""
        supported = extract_supported_models()
        with self.assertRaises(ChangeSetException):
            transformer.transform_proto_json({"username": "ghost"}, "users.user", supported)
        self.assertFalse(User.objects.filter(username="ghost").exists())


class MatchOnlyUserApplyTestCase(TestCase):
    """The direct-apply path (applier) rejects create/update of users.user."""

    def test_direct_create_user_changeset_rejected(self):
        """A CREATE changeset for users.user is rejected by the match-only guard."""
        # Serializer-valid payload (password present) so the assertion detects
        # the guard specifically, not incidental UserSerializer validation.
        cs = ChangeSet(id="cs-u-create", changes=[Change(
            change_type=ChangeType.CREATE, object_type="users.user",
            ref_id="new_object:users.user:x",
            data={"username": "should-not-create", "password": "Str0ng-P@ssw0rd!"}, new_refs=[])])
        with self.assertRaises(ChangeSetException) as cm:
            apply_changeset(cs, request=None)
        self.assertIn("match-only", str(cm.exception))
        self.assertFalse(User.objects.filter(username="should-not-create").exists())

    def test_direct_update_user_changeset_rejected_username_unchanged(self):
        """An UPDATE changeset for users.user is rejected; the username is unchanged."""
        u = User.objects.create(username="keep-me")
        cs = ChangeSet(id="cs-u-update", changes=[Change(
            change_type=ChangeType.UPDATE, object_type="users.user",
            object_id=u.pk, data={"username": "renamed"}, new_refs=[])])
        with self.assertRaises(ChangeSetException):
            apply_changeset(cs, request=None)
        u.refresh_from_db()
        self.assertEqual(u.username, "keep-me")


class RackReservationApplyTestCase(TestCase):
    """
    RackReservation ingests end-to-end with a match-only user reference.

    Only the user is pre-seeded; the site + rack are created by the ingest.
    (dcim.rack has no reliable natural-key match when location is null, so
    assertions key off the reservation's resolved user, not a pre-seeded rack.)
    """

    @classmethod
    def setUpTestData(cls):
        """Seed only the existing user the reservation will reference."""
        cls.user = User.objects.create(username="rr-owner")

    def _entity(self, username):
        return {"rack": {"name": "RR-Rack", "site": {"name": "RR-Site"}},
                "units": [1, 2, 3], "description": "reserved",
                "user": {"username": username}}

    def test_reservation_with_existing_user_applies(self):
        """A reservation referencing an existing user resolves that user + applies."""
        r = generate_changeset(self._entity("rr-owner"), "dcim.rackreservation")
        # match-only: the matched user is a pure reference — NO users.user change
        self.assertFalse(any(c.object_type == "users.user" for c in r.change_set.changes))
        apply_changeset(r.change_set, request=None)
        self.assertEqual(RackReservation.objects.count(), 1)
        rr = RackReservation.objects.first()
        self.assertEqual(rr.user_id, self.user.pk)  # matched existing user, not created
        self.assertEqual(rr.rack.name, "RR-Rack")
        self.assertEqual(sorted(rr.units), [1, 2, 3])
        # match-only: no new user was minted
        self.assertEqual(User.objects.filter(username="rr-owner").count(), 1)

    def test_reservation_with_unknown_user_deviates(self):
        """An unknown user -> clean deviation, no user created, no reservation."""
        with self.assertRaises(ChangeSetException):
            generate_changeset(self._entity("ghost"), "dcim.rackreservation")
        self.assertFalse(User.objects.filter(username="ghost").exists())
        self.assertEqual(RackReservation.objects.count(), 0)

    def test_reservation_case_mismatch_username_deviates(self):
        """Username matching is exact/case-sensitive: a case variant deviates."""
        with self.assertRaises(ChangeSetException):
            generate_changeset(self._entity("RR-OWNER"), "dcim.rackreservation")
        self.assertFalse(User.objects.filter(username="RR-OWNER").exists())
        self.assertEqual(RackReservation.objects.count(), 0)

    def test_reservation_missing_user_deviates(self):
        """RackReservation.user is required; omitting it -> deviation, no row created."""
        entity = {"rack": {"name": "RR-Rack", "site": {"name": "RR-Site"}},
                  "units": [1, 2, 3], "description": "reserved"}
        with self.assertRaises(ChangeSetException):
            r = generate_changeset(entity, "dcim.rackreservation")
            apply_changeset(r.change_set, request=None)
        self.assertEqual(RackReservation.objects.count(), 0)

    def test_existing_user_resolution_is_idempotent(self):
        """
        Re-ingesting resolves the same existing user each time; never mints one.

        Scoped to the USER guarantee: the match-only user is resolved (never
        duplicated or created) and the reservation's user never changes across
        re-ingests. NOTE: a full-changeset NOOP is NOT asserted here because
        dcim.rackreservation is keyless AND its nested dcim.rack has no reliable
        natural-key match when location is null (a re-diff re-creates the rack
        and re-points the reservation) — both pre-existing behaviors unrelated
        to this feature. The user, correctly, is the one node that stays matched.
        """
        for _ in range(2):
            r = generate_changeset(self._entity("rr-owner"), "dcim.rackreservation")
            # user is always a pure reference — never a change node
            self.assertFalse(any(c.object_type == "users.user" for c in r.change_set.changes))
            apply_changeset(r.change_set, request=None)
        self.assertEqual(User.objects.filter(username="rr-owner").count(), 1)
        self.assertTrue(all(rr.user_id == self.user.pk for rr in RackReservation.objects.all()))
