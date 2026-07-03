#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Cable apply integration tests."""

from dcim.models import (
    Cable,
    CableTermination,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.test import TestCase

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeSetException, ChangeType
from netbox_diode_plugin.api.differ import generate_changeset


class CableApplyTestCase(TestCase):
    """A cable CREATE materializes two CableTermination rows via CableSerializer."""

    @classmethod
    def setUpTestData(cls):
        """Set up test fixtures."""
        mfr = Manufacturer.objects.create(name="MFR-Cable", slug="mfr-cable")
        dt = DeviceType.objects.create(manufacturer=mfr, model="DT-Cable", slug="dt-cable")
        role = DeviceRole.objects.create(name="Role-Cable", slug="role-cable")
        site = Site.objects.create(name="Site-Cable", slug="site-cable")
        dev_a = Device.objects.create(name="Cable Device A", device_type=dt, role=role, site=site)
        dev_b = Device.objects.create(name="Cable Device B", device_type=dt, role=role, site=site)
        dev_c = Device.objects.create(name="Cable Device C", device_type=dt, role=role, site=site)
        cls.iface_a = Interface.objects.create(device=dev_a, name="Cable Iface A", type="1000base-t")
        cls.iface_b = Interface.objects.create(device=dev_b, name="Cable Iface B", type="1000base-t")
        cls.iface_c = Interface.objects.create(device=dev_c, name="Cable Iface C", type="1000base-t")

    def _cable_change(self, a_pk, b_pk, label="Cable 1"):
        return Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.cable",
            ref_id="new_object:dcim.cable:c1",
            data={
                "status": "connected",
                "label": label,
                "a_terminations": [{"object_type": "dcim.interface", "object_id": a_pk}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": b_pk}],
            },
            new_refs=[],
        )

    def test_cable_create_materializes_two_terminations(self):
        """Cable create materializes two terminations."""
        cs = ChangeSet(id="cs-cable-1", changes=[self._cable_change(self.iface_a.pk, self.iface_b.pk)])
        apply_changeset(cs, request=None)

        cable = Cable.objects.get(label="Cable 1")
        terms = CableTermination.objects.filter(cable=cable)
        self.assertEqual(terms.count(), 2)
        term_ids = {(t.cable_end, t.termination_id) for t in terms}
        self.assertIn(("A", self.iface_a.pk), term_ids)
        self.assertIn(("B", self.iface_b.pk), term_ids)

    def test_already_cabled_interface_fails_change_with_clean_error(self):
        """Already cabled interface fails change with clean error."""
        # First cable connects A<->B. A second, distinct-termination-set cable
        # that reuses interface A (now already cabled) cannot be matched by
        # CableTerminationSetMatcher (different termination set: A<->C vs A<->B),
        # so it falls through to CableSerializer.is_valid()/Cable.clean(), which
        # rejects it because interface A already has a cable connection.
        cs1 = ChangeSet(id="cs-cable-a", changes=[self._cable_change(self.iface_a.pk, self.iface_b.pk)])
        apply_changeset(cs1, request=None)

        cs2 = ChangeSet(id="cs-cable-b", changes=[self._cable_change(self.iface_a.pk, self.iface_c.pk, label="Cable 2")])
        with self.assertRaises(ChangeSetException) as ctx:
            apply_changeset(cs2, request=None)
        msg = str(ctx.exception).lower()
        self.assertTrue(
            any(s in msg for s in ("already", "cabled", "connect", "duplicate termination")),
            f"expected a cable-conflict clean() message, got: {ctx.exception}",
        )
        self.assertFalse(Cable.objects.filter(label="Cable 2").exists())


class CableMultiObjectUpdateTestCase(TestCase):
    """
    UPDATE of a multi-object end with mixed new/existing terminations resolves.

    Regression for the new_refs/resort desync: `_generate_changeset` computed
    index paths (``a_terminations.0.object_id``) via
    ``cleanup_unresolved_references`` BEFORE ``_partially_merge`` re-sorted the
    termination lists for stable comparison. On an end mixing an
    already-resolved pk (int, sorts first — digits precede ``new_object:``)
    with a still-unresolved new-object ref, the sort moved entries after the
    paths were fixed: the path landed on the int (skipped) while the
    unresolved ref at its new index was never resolved, reaching
    CableSerializer as a string and failing the whole update. Goes through the
    REAL ``generate_changeset`` -> ``apply_changeset`` path with the cable
    matched via ``metadata.source_match.netbox_id``.
    """

    @classmethod
    def setUpTestData(cls):
        """Set up an existing cabled pair; a third interface is created by ingest."""
        mfr = Manufacturer.objects.create(name="MFR-MultiTerm", slug="mfr-multiterm")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="DT-MultiTerm", slug="dt-multiterm")
        cls.role = DeviceRole.objects.create(name="Role-MultiTerm", slug="role-multiterm")
        cls.site = Site.objects.create(name="Site-MultiTerm", slug="site-multiterm")
        cls.dev_a = Device.objects.create(name="MultiTerm Device A", device_type=cls.dt, role=cls.role, site=cls.site)
        cls.dev_b = Device.objects.create(name="MultiTerm Device B", device_type=cls.dt, role=cls.role, site=cls.site)
        cls.iface_a0 = Interface.objects.create(device=cls.dev_a, name="mt-eth0", type="1000base-t")
        cls.iface_b0 = Interface.objects.create(device=cls.dev_b, name="mt-eth1", type="1000base-t")

    def _iface_payload(self, device, name):
        return {
            "object_interface": {
                "name": name,
                "type": "1000base-t",
                "device": {
                    "name": device.name,
                    "device_type": {
                        "manufacturer": {"name": "MFR-MultiTerm"},
                        "model": "DT-MultiTerm",
                    },
                    "role": {"name": "Role-MultiTerm"},
                    "site": {"name": "Site-MultiTerm"},
                },
            }
        }

    def test_add_new_termination_to_existing_cable_resolves(self):
        """Adding a NEW interface to an existing cable's end resolves + applies."""
        # existing cable A:[mt-eth0] <-> B:[mt-eth1]
        cs = ChangeSet(
            id="cs-mt-seed",
            changes=[
                Change(
                    change_type=ChangeType.CREATE,
                    object_type="dcim.cable",
                    ref_id="new_object:dcim.cable:seed",
                    data={
                        "status": "connected",
                        "label": "MT Cable",
                        "a_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_a0.pk}],
                        "b_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_b0.pk}],
                    },
                    new_refs=[],
                )
            ],
        )
        apply_changeset(cs, request=None)
        cable = Cable.objects.get(label="MT Cable")

        # UPDATE via netbox_id: end A gains a NEW (not-yet-existing) interface.
        # The new termination is listed FIRST so its pre-sort index (0) differs
        # from its post-sort index (ints sort before "new_object:" strings) --
        # the exact desync scenario.
        entity = {
            "status": "connected",
            "label": "MT Cable",
            "a_terminations": [
                self._iface_payload(self.dev_a, "mt-eth2-new"),
                self._iface_payload(self.dev_a, "mt-eth0"),
            ],
            "b_terminations": [self._iface_payload(self.dev_b, "mt-eth1")],
            "metadata": {"source_match": {"netbox_id": cable.pk}},
        }
        result = generate_changeset(entity, "dcim.cable")
        self.assertIsNotNone(result.change_set)
        # must not raise (pre-fix: the unresolved new-object ref reached the
        # serializer as a string and failed the update)
        apply_changeset(result.change_set, request=None)

        new_iface = Interface.objects.get(device=self.dev_a, name="mt-eth2-new")
        terms = CableTermination.objects.filter(cable=cable)
        self.assertEqual(terms.count(), 3)
        term_ids = {(t.cable_end, t.termination_id) for t in terms}
        self.assertIn(("A", self.iface_a0.pk), term_ids)
        self.assertIn(("A", new_iface.pk), term_ids)
        self.assertIn(("B", self.iface_b0.pk), term_ids)


class CableEndSwapNoopTestCase(TestCase):
    """
    Re-ingesting the same cable with A/B ends swapped is a NOOP.

    Cable identity is A/B-insensitive; without end alignment in the differ,
    alternating feeds that flip endpoint order keep generating UPDATEs that
    only toggle cable_end instead of converging.
    """

    @classmethod
    def setUpTestData(cls):
        """Seed a cabled interface pair."""
        mfr = Manufacturer.objects.create(name="MFR-Swap", slug="mfr-swap")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="DT-Swap", slug="dt-swap")
        cls.role = DeviceRole.objects.create(name="Role-Swap", slug="role-swap")
        cls.site = Site.objects.create(name="Site-Swap", slug="site-swap")
        cls.dev_a = Device.objects.create(name="Swap Device A", device_type=cls.dt, role=cls.role, site=cls.site)
        cls.dev_b = Device.objects.create(name="Swap Device B", device_type=cls.dt, role=cls.role, site=cls.site)
        cls.iface_a = Interface.objects.create(device=cls.dev_a, name="sw-eth0", type="1000base-t")
        cls.iface_b = Interface.objects.create(device=cls.dev_b, name="sw-eth1", type="1000base-t")

    def _iface(self, device, name):
        return {
            "object_interface": {
                "name": name,
                "type": "1000base-t",
                "device": {
                    "name": device.name,
                    "device_type": {"manufacturer": {"name": "MFR-Swap"}, "model": "DT-Swap"},
                    "role": {"name": "Role-Swap"},
                    "site": {"name": "Site-Swap"},
                },
            }
        }

    def test_swapped_ends_rediff_is_noop(self):
        """Swapped-end re-ingest matches the cable and produces no changes."""
        cs = ChangeSet(
            id="cs-swap-seed",
            changes=[
                Change(
                    change_type=ChangeType.CREATE,
                    object_type="dcim.cable",
                    ref_id="new_object:dcim.cable:swap",
                    data={
                        "status": "connected",
                        "a_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_a.pk}],
                        "b_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_b.pk}],
                    },
                    new_refs=[],
                )
            ],
        )
        apply_changeset(cs, request=None)

        # same cable, ends flipped
        entity = {
            "status": "connected",
            "a_terminations": [self._iface(self.dev_b, "sw-eth1")],
            "b_terminations": [self._iface(self.dev_a, "sw-eth0")],
        }
        result = generate_changeset(entity, "dcim.cable")
        self.assertEqual(result.change_set.changes, [])

    def test_stale_swapped_create_preserves_cable_ends(self):
        """
        A stale CREATE for an existing cable must not toggle cable_end.

        Applying a CREATE change (as if planned before the cable existed) whose
        ends are swapped relative to the existing cable hits the applier
        pre-save-match path (dcim.cable in _REQUIRES_PRE_SAVE_MATCH). The match
        is by A/B-insensitive set, so terminations must be left intact — only
        attributes update — otherwise cable_end flips and alternating stale
        creates toggle the physical assignment forever.
        """
        seed = ChangeSet(
            id="cs-stale-seed",
            changes=[
                Change(
                    change_type=ChangeType.CREATE,
                    object_type="dcim.cable",
                    ref_id="new_object:dcim.cable:stale-seed",
                    data={
                        "status": "connected",
                        "label": "Stale Cable",
                        "a_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_a.pk}],
                        "b_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_b.pk}],
                    },
                    new_refs=[],
                )
            ],
        )
        apply_changeset(seed, request=None)
        cable = Cable.objects.get(label="Stale Cable")
        ends_before = {(t.termination_id, t.cable_end) for t in CableTermination.objects.filter(cable=cable)}
        self.assertEqual(ends_before, {(self.iface_a.pk, "A"), (self.iface_b.pk, "B")})

        # stale CREATE for the SAME cable, ends swapped + a changed attribute
        stale = ChangeSet(
            id="cs-stale-swap",
            changes=[
                Change(
                    change_type=ChangeType.CREATE,
                    object_type="dcim.cable",
                    ref_id="new_object:dcim.cable:stale-swap",
                    data={
                        "status": "connected",
                        "label": "Stale Cable Relabeled",
                        "a_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_b.pk}],
                        "b_terminations": [{"object_type": "dcim.interface", "object_id": self.iface_a.pk}],
                    },
                    new_refs=[],
                )
            ],
        )
        apply_changeset(stale, request=None)

        # still exactly one cable, ends UNCHANGED (no cable_end toggle)
        self.assertEqual(Cable.objects.filter(label__startswith="Stale Cable").count(), 1)
        cable.refresh_from_db()
        ends_after = {(t.termination_id, t.cable_end) for t in CableTermination.objects.filter(cable=cable)}
        self.assertEqual(ends_after, ends_before)
        # non-termination attributes still update through the pre-save-match path
        self.assertEqual(cable.label, "Stale Cable Relabeled")


class CablePartitionTestCase(TestCase):
    """
    Pre-save-match strip is A/B-grouping aware, not a blanket drop.

    The set matcher finds a cable by its A/B-insensitive termination set, so a
    stale CREATE for the same set matches it. Stripping terminations is correct
    only when the submitted grouping equals the existing one up to a whole-end
    swap; a genuine repartition (same set, different grouping) must be applied,
    consistent with the differ UPDATE path — not silently dropped.
    """

    @classmethod
    def setUpTestData(cls):
        """Seed a device with three interfaces for multi-termination cables."""
        mfr = Manufacturer.objects.create(name="MFR-Part", slug="mfr-part")
        dt = DeviceType.objects.create(manufacturer=mfr, model="DT-Part", slug="dt-part")
        role = DeviceRole.objects.create(name="Role-Part", slug="role-part")
        site = Site.objects.create(name="Site-Part", slug="site-part")
        cls.dev_a = Device.objects.create(name="Part Device A", device_type=dt, role=role, site=site)
        cls.dev_b = Device.objects.create(name="Part Device B", device_type=dt, role=role, site=site)
        cls.if1 = Interface.objects.create(device=cls.dev_a, name="p-eth0", type="1000base-t")
        cls.if2 = Interface.objects.create(device=cls.dev_a, name="p-eth1", type="1000base-t")
        cls.if3 = Interface.objects.create(device=cls.dev_b, name="p-eth2", type="1000base-t")

    def _terms(self, *ifaces):
        return [{"object_type": "dcim.interface", "object_id": i.pk} for i in ifaces]

    def _seed(self, label, a_ifaces, b_ifaces):
        cable = Cable(a_terminations=list(a_ifaces), b_terminations=list(b_ifaces))
        cable.label = label
        cable.status = "connected"
        cable.save()
        return cable

    def _apply_create(self, cs_id, label, a_ifaces, b_ifaces):
        cs = ChangeSet(
            id=cs_id,
            changes=[
                Change(
                    change_type=ChangeType.CREATE,
                    object_type="dcim.cable",
                    ref_id=f"new_object:dcim.cable:{cs_id}",
                    data={
                        "status": "connected",
                        "label": label,
                        "a_terminations": self._terms(*a_ifaces),
                        "b_terminations": self._terms(*b_ifaces),
                    },
                    new_refs=[],
                )
            ],
        )
        apply_changeset(cs, request=None)

    def _grouping(self, cable):
        cable.refresh_from_db()
        return {(t.termination_id, t.cable_end) for t in CableTermination.objects.filter(cable=cable)}

    def test_genuine_repartition_is_applied(self):
        """A:[if1,if2]/B:[if3] re-fed as A:[if1]/B:[if2,if3] moves if2 A->B."""
        cable = self._seed("Repart", [self.if1, self.if2], [self.if3])
        self.assertEqual(
            self._grouping(cable),
            {(self.if1.pk, "A"), (self.if2.pk, "A"), (self.if3.pk, "B")},
        )
        # stale CREATE with the same set but if2 moved to end B (+ relabel)
        self._apply_create("cs-repart", "Repart-Relabeled", [self.if1], [self.if2, self.if3])
        self.assertEqual(Cable.objects.filter(label__startswith="Repart").count(), 1)
        self.assertEqual(
            self._grouping(cable),
            {(self.if1.pk, "A"), (self.if2.pk, "B"), (self.if3.pk, "B")},
        )
        cable.refresh_from_db()
        self.assertEqual(cable.label, "Repart-Relabeled")

    def test_whole_end_swap_stays_noop(self):
        """A whole-end swap of a multi-termination cable does not toggle cable_end."""
        cable = self._seed("Swap", [self.if1, self.if2], [self.if3])
        before = self._grouping(cable)
        # swap ends: A<->B (same grouping, opposite labels)
        self._apply_create("cs-swap", "Swap", [self.if3], [self.if1, self.if2])
        self.assertEqual(self._grouping(cable), before)

    def test_within_end_reorder_stays_noop(self):
        """Reordering terminations within an end changes nothing (same grouping)."""
        cable = self._seed("Reorder", [self.if1, self.if2], [self.if3])
        before = self._grouping(cable)
        self._apply_create("cs-reorder", "Reorder", [self.if2, self.if1], [self.if3])
        self.assertEqual(self._grouping(cable), before)


class ApplierKeyErrorScopeTestCase(TestCase):
    """Only missing new_object references become clean per-entity errors."""

    def _change(self, data, new_refs):
        return Change(
            change_type=ChangeType.CREATE,
            object_type="dcim.cable",
            ref_id="new_object:dcim.cable:k1",
            data=data,
            new_refs=new_refs,
        )

    def test_dangling_new_object_ref_is_clean_error(self):
        """A missing new_object ref surfaces as ChangeSetException, not a 500."""
        cs = ChangeSet(
            id="cs-keyerr-1",
            changes=[self._change({"status": "connected", "label": "new_object:dcim.interface:missing"}, ["label"])],
        )
        with self.assertRaises(ChangeSetException):
            apply_changeset(cs, request=None)

    def test_unrelated_keyerror_propagates(self):
        """A KeyError with a non-reference key is a real bug and propagates."""
        cs = ChangeSet(
            id="cs-keyerr-2",
            changes=[self._change({"status": "connected", "label": "not-a-ref"}, ["label"])],
        )
        with self.assertRaises(KeyError):
            apply_changeset(cs, request=None)
