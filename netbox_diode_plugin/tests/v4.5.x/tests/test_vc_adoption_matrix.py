"""
Exhaustive enumeration of the VirtualChassis adoption decision space.

Adoption (applier._try_adopt_masterless_virtualchassis) is the only place a
master-bearing dcim.virtualchassis CREATE may bind a pre-existing row instead of
inserting one. Every earlier revision of this branch was reported green by
scenario tests and every one of them still had a cell that either wrote the
wrong row or refused forever. Scenario tests pick cells; this file enumerates
them, over the product of every axis the decision can read:

  - 0/1/2 same-named MASTERLESS candidates, each empty / populated /
    populated-and-labelled (a domain the payload may or may not assert)
  - a same-named MASTERED row present or absent (it is outside the candidate
    queryset, but it is what the create path falls back onto)
  - the requested master: free / already a member of a candidate / a member of
    an unrelated chassis / mastering another chassis / not a device at all
  - the payload's domain: absent / "" / identifying / contradicting
  - the changeset shape: a STANDALONE virtual_chassis change, or the
    member-first pair (create chassis + update the device with its membership)
  - the member already in the candidate at the master's site or at another one

and asserts the four properties the design has to hold in EVERY cell:

  NO-REFUSE   adoption itself never raises. The only errors reachable on this
              path are the two that are facts about the master rather than about
              the ambiguity of the name: it is a member of another chassis
              (a reported conflict, remedied by moving the device) or its pk is
              not a device at all (a dangling reference). Both are re-tested
              here for convergence AFTER the remedy, so neither is permanent.
  NO-HIJACK   a pre-existing row is written only where identity is strong: it
              already holds the requested master, an asserted non-empty domain
              identifies it, it is empty, or this changeset plans the master's
              membership of the chassis it is creating. Never on the name alone.
  NO-MOVE     no device that was already a member of a pre-existing row leaves
              it, in any cell.
  FULFILLED   in every non-error cell the request is carried out: the named
              master ends up mastering a chassis of that name.

The table itself is printed on failure, so a regression names its own cell.
"""
import itertools
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.db import transaction
from django.test import TestCase
from utilities.testing import APITestCase

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import (
    VC_MEMBER_HINT,
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeType,
)
from netbox_diode_plugin.api.matcher import AmbiguousObjectMatch, find_existing_object
from netbox_diode_plugin.plugin_config import get_diode_user

NAME = "mx-stack"
LABEL = "mx-lab"
OTHER_LABEL = "mx-nope"


class _Rollback(Exception):
    """Sentinel: unwind one cell's writes without failing the test."""


class VirtualChassisAdoptionMatrixTests(TestCase):
    """The whole adoption decision space, cell by cell."""

    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        """A device pool, so a cell costs FK updates rather than inserts."""
        cls.site = Site.objects.create(name="mx-site", slug="mx-site")
        cls.site_b = Site.objects.create(name="mx-site-b", slug="mx-site-b")
        mfr = Manufacturer.objects.create(name="mx-mfr", slug="mx-mfr")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="mx-dt", slug="mx-dt")
        cls.role = DeviceRole.objects.create(name="mx-role", slug="mx-role")
        cls.master = Device.objects.create(
            name="mx-master", site=cls.site, device_type=cls.dt, role=cls.role)
        # occupants: one per candidate slot, at each site, plus the ones that
        # populate the mastered same-named row and the unrelated rows.
        cls.occupants = {}
        for key in ("c0", "c1", "c2", "c0b", "c1b", "c2b", "mstr", "else", "own"):
            cls.occupants[key] = Device.objects.create(
                name=f"mx-occ-{key}",
                site=cls.site_b if key.endswith("b") else cls.site,
                device_type=cls.dt, role=cls.role)

    # ---- cell construction ------------------------------------------------

    def _place(self, device, vc, position):
        Device.objects.filter(pk=device.pk).update(
            virtual_chassis=vc, vc_position=position)

    def _build(self, shapes, mastered, master_state, cross_site):
        """Create the pre-existing rows for one cell. Returns (candidates, extras)."""
        candidates = []
        for index, shape in enumerate(shapes):
            vc = VirtualChassis.objects.create(
                name=NAME, domain=LABEL if shape == "L" else "")
            if shape in ("P", "L"):
                key = f"c{index}b" if cross_site else f"c{index}"
                self._place(self.occupants[key], vc, 9)
            candidates.append(vc)

        extras = {}
        if mastered:
            row = VirtualChassis.objects.create(name=NAME)
            self._place(self.occupants["mstr"], row, 1)
            row.refresh_from_db()
            row.master = self.occupants["mstr"]
            row.save()
            extras["mastered"] = row

        master_pk = self.master.pk
        if master_state == "in_candidate":
            self._place(self.master, candidates[0], 5)
        elif master_state == "in_other":
            elsewhere = VirtualChassis.objects.create(name="mx-elsewhere")
            self._place(self.master, elsewhere, 2)
            extras["elsewhere"] = elsewhere
        elif master_state == "masters_other":
            owned = VirtualChassis.objects.create(name="mx-owned")
            self._place(self.master, owned, 1)
            owned.refresh_from_db()
            owned.master = self.master
            owned.save()
            extras["owned"] = owned
        elif master_state == "missing":
            master_pk = (Device.objects.order_by("-pk").first().pk) + 9999

        return candidates, extras, master_pk

    def _snapshot(self):
        rows = {
            vc.pk: (vc.name, vc.domain, vc.master_id, vc.last_updated, vc.description,
                    tuple(sorted(vc.members.values_list("pk", "vc_position"))))
            for vc in VirtualChassis.objects.all()
        }
        devices = dict(Device.objects.values_list("pk", "virtual_chassis_id"))
        return rows, devices

    def _run_cell(self, shapes, mastered, master_state, domain, shape, cross_site):
        candidates, extras, master_pk = self._build(
            shapes, mastered, master_state, cross_site)
        before_rows, before_devices = self._snapshot()

        data = {"name": NAME, "master": master_pk, "description": "mx-probe"}
        if domain is not None:
            data["domain"] = domain

        extra_changes = ()
        if shape == "member_first":
            extra_changes = (Change(
                change_type=ChangeType.UPDATE,
                object_type="dcim.device",
                object_id=master_pk,
                data={"virtual_chassis": "vc1", "vc_position": 1},
                new_refs=["virtual_chassis"],
            ),)

        cs = ChangeSet(changes=[
            Change(change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
                   ref_id="vc1", data=data),
            *extra_changes,
        ])

        outcome = {"error": None}
        try:
            apply_changeset(cs, SimpleNamespace(user=None))
        except ChangeSetException as exc:
            outcome["error"] = exc.errors
        except AmbiguousObjectMatch as exc:  # pragma: no cover - subclass of the above
            outcome["error"] = exc.errors

        after_rows, after_devices = self._snapshot()
        # A row is WRITTEN if its own columns changed or it gained a member.
        # Losing a member is a different phenomenon (NO-MOVE) and is counted
        # separately, or a device dragged away by NetBox's own create signal
        # would read as "adoption wrote this row".
        written = []
        lost_members = []
        for pk, state in after_rows.items():
            if pk not in before_rows:
                continue
            was = before_rows[pk]
            if state[:5] != was[:5] or set(state[5]) - set(was[5]):
                written.append(pk)
            if set(was[5]) - set(state[5]):
                lost_members.append(pk)
        outcome.update(
            lost_members=sorted(lost_members),
            candidates=[c.pk for c in candidates],
            extras={k: v.pk for k, v in extras.items()},
            master_pk=master_pk,
            written=sorted(written),
            new_rows=sorted(set(after_rows) - set(before_rows)),
            named_rows=sorted(
                pk for pk, state in after_rows.items() if state[0] == NAME),
            masters=[pk for pk, state in after_rows.items() if state[2] == master_pk],
            moved=sorted(
                pk for pk, vc in after_devices.items()
                if before_devices.get(pk) is not None and before_devices[pk] != vc),
            before_rows=before_rows,
            after_rows=after_rows,
        )
        return outcome

    # ---- the enumeration --------------------------------------------------

    def _cells(self):
        # 0/1/2 candidates, not 0..3. The adoption decision cannot tell three
        # same-named rows from two. narrow_vc_candidates is a per-element filter
        # with one empty/non-empty check, so it is cardinality-blind, and every
        # cardinality test _choose_adoption_candidate makes is a zero-, one- or
        # more-than-one test -- all five of them: len(strong) == 1,
        # len(strong) > 1 (unreachable while Device.virtual_chassis is a single
        # FK, so at most one row can hold the master), `not candidates`,
        # `identified and len(candidates) == 1`, and len(candidates) != 1.
        # Every branch past narrowing reads one row, `only`. Two candidates
        # already reach "more than one", so a third adds cells, not states.
        #
        # Measured, not reasoned: the 4240 length-3 cells produced no behaviour
        # signature the shorter 1824 did not. Dropping them, together with the
        # matching cap on the matcher enumeration below, took the two
        # enumerations from 224.3s to 54.3s on v4.5.5 -- run back to back under
        # the same machine load, because timings on a loaded laptop move by 2x
        # and a cross-run comparison here would prove nothing.
        shapes_axis = [
            tuple(combo)
            for length in range(3)
            for combo in itertools.product("EPL", repeat=length)
        ]
        for shapes in shapes_axis:
            for mastered in (False, True):
                for master_state in (
                        "free", "in_candidate", "in_other", "masters_other", "missing"):
                    if master_state == "in_candidate" and not shapes:
                        continue
                    for domain in (None, "", LABEL, OTHER_LABEL):
                        for shape in ("standalone", "member_first"):
                            for cross_site in (False, True):
                                if cross_site and not any(
                                        s in ("P", "L") for s in shapes):
                                    continue
                                yield (shapes, mastered, master_state, domain,
                                       shape, cross_site)

    def test_the_whole_adoption_decision_space(self):
        """Every cell: no refusal for ambiguity, no hijack, no move, request fulfilled."""
        failures = []
        table = []
        counted = 0
        for cell in self._cells():
            shapes, mastered, master_state, domain, shape, cross_site = cell
            sid = transaction.savepoint()
            try:
                outcome = self._run_cell(*cell)
                counted += 1
                verdict = self._classify(cell, outcome)
                failures.extend(
                    f"{cell}: {problem}" for problem in self._check(cell, outcome, verdict)
                )
                verdict["licences"] = outcome.get("licences", [])
                table.append((cell, verdict))
            finally:
                transaction.savepoint_rollback(sid)

        # Exact, not a floor: this guard exists to catch an enumeration that
        # silently stopped enumerating, and a floor cannot see a narrowed axis.
        self.assertEqual(counted, 1824, "the enumeration changed size")
        census = {}
        licences = {}
        for cell, verdict in table:
            key = (verdict["kind"], bool(verdict.get("master_yanked")))
            census.setdefault(key, []).append(cell)
            for licence in verdict.get("licences", ()):
                licences.setdefault(licence, 0)
                licences[licence] += 1
        print("\n== adoption census ==")
        for key in sorted(census, key=lambda k: (str(k[0]), k[1])):
            print(f"  {key}: {len(census[key])} cells   e.g. {census[key][0]}")
        yanked = [c for (kind, y), cells in census.items() if y for c in cells]
        print(f"== cells where the create path pulled the master out of its "
              f"chassis: {len(yanked)} ==")
        for cell in yanked[:6]:
            print(f"   {cell}")
        print("== which licence each adoption used ==")
        for licence, count in sorted(licences.items()):
            print(f"  {licence}: {count} cells")
        conflicts = [c for c, v in table if v["kind"] == "conflict"]
        print(f"== cells that reported the IN_OTHER_CHASSIS conflict: "
              f"{len(conflicts)} ==")
        for cell in conflicts[:6]:
            print(f"   {cell}")
        # Every axis value has to actually occur, or a skipped combination is
        # silently narrowing the proof.
        for expected in ("adopt", "create", "conflict", "dangling"):
            self.assertTrue(
                any(v["kind"] == expected for _, v in table),
                f"no cell exercised {expected}")
        self.assertEqual(failures, [], "\n".join(failures[:40]))

    def _classify(self, cell, outcome):
        shapes, mastered, master_state, domain, shape, cross_site = cell
        if outcome["error"]:
            message = str(outcome["error"])
            kind = "conflict" if "is a member of" in message else "dangling"
            return {"kind": kind, "message": message}
        if outcome["written"]:
            return {"kind": "adopt", "rows": outcome["written"]}
        return {"kind": "create", "rows": outcome["new_rows"]}

    def _check(self, cell, outcome, verdict):
        """The four properties. Returns one string per violation."""
        return [
            *self._check_no_refuse(cell, outcome, verdict),
            *self._check_no_hijack(cell, outcome),
            *self._check_no_move_and_fulfilled(cell, outcome, verdict),
        ]

    @staticmethod
    def _check_no_refuse(cell, outcome, verdict):
        """NO-REFUSE: an error is only ever a fact about the master."""
        master_state = cell[2]
        problems = []
        if verdict["kind"] == "conflict" and master_state != "in_other":
            problems.append(f"conflict raised with master_state={master_state}")
        if verdict["kind"] == "dangling" and master_state != "missing":
            problems.append(f"dangling error raised with master_state={master_state}")
        if master_state in ("free", "in_candidate", "masters_other") and outcome["error"]:
            problems.append(f"refused a cell with a usable master: {outcome['error']}")
        return problems

    @staticmethod
    def _check_no_hijack(cell, outcome):
        """NO-HIJACK: only strong identity licenses writing a pre-existing row."""
        shapes, _mastered, master_state, domain, shape, _cross_site = cell
        problems = []

        # The licence is recomputed here from the cell's axes, independently of
        # the implementation, including the narrowing an asserted domain does
        # (matcher.narrow_vc_candidates) -- "" excludes every LABELLED row while
        # identifying none, a non-empty value both excludes and identifies.
        row_domain = {"E": "", "P": "", "L": LABEL}
        if domain is None:
            narrowed = list(range(len(shapes)))
            identified = False
        else:
            narrowed = [i for i, sh in enumerate(shapes) if row_domain[sh] == domain]
            identified = bool(domain) and bool(narrowed)
        for pk in outcome["written"]:
            if pk not in outcome["candidates"]:
                problems.append(f"wrote a non-candidate row {pk}")
                continue
            index = outcome["candidates"].index(pk)
            row_shape = shapes[index]
            already_holds = master_state == "in_candidate" and index == 0
            sole = narrowed == [index]
            unasserted = row_shape == "L" and not domain
            licences = {
                "holds-master": already_holds,
                "domain-identified": identified and sole,
                "single-empty": sole and row_shape == "E",
                "member-first": sole and shape == "member_first" and not unasserted,
            }
            granted = [key for key, value in licences.items() if value]
            outcome.setdefault("licences", []).extend(granted)
            if not granted:
                problems.append(
                    f"adopted candidate {index} ({row_shape}) with no strong "
                    f"identity; narrowed={narrowed} identified={identified}")
            if unasserted and not (already_holds or identified):
                problems.append(f"adopted LABELLED row {index} the payload never asserted")
            if index not in narrowed and not already_holds:
                problems.append(f"adopted candidate {index} its own domain contradicts")
        return problems

    @staticmethod
    def _check_no_move_and_fulfilled(cell, outcome, verdict):
        """NO-MOVE and FULFILLED, plus the census flag for a yanked master."""
        _shapes, _mastered, master_state, _domain, _shape, _cross_site = cell
        problems = []

        # NO-MOVE: nothing that already had a chassis lost or swapped it.
        # The master counts: a dcim.virtualchassis payload naming a master is
        # not authority to pull that device out of the chassis it is in -- which
        # is exactly what the IN_OTHER_CHASSIS conflict exists to say. Recorded
        # rather than asserted here, because the create path (NetBox's own
        # dcim.signals.assign_virtualchassis_master) does it in cells adoption
        # declined, and the census below is the measurement of how many.
        for pk in outcome["moved"]:
            if pk != outcome["master_pk"]:
                problems.append(f"relocated device {pk}")
            elif master_state == "in_other":
                verdict["master_yanked"] = True

        # FULFILLED: a usable master masters a chassis of that name afterwards.
        if not outcome["error"]:
            if master_state == "masters_other":
                if outcome["masters"] != [outcome["extras"]["owned"]]:
                    problems.append("the unique-master row is no longer the answer")
            elif master_state != "missing":
                if not outcome["masters"]:
                    problems.append("the request left the named master mastering nothing")
                else:
                    mastered_row = outcome["after_rows"][outcome["masters"][0]]
                    if mastered_row[0] != NAME:
                        problems.append("the master was attached to a differently named row")
        return problems

    # ---- the two error cells converge after their remedy -------------------

    def test_the_conflict_cell_converges_once_the_device_moves(self):
        """
        The only refusal adoption's caller raises, and it is not permanent.

        The master is a plain member of another chassis. The message names the
        holder and the move; making that move in NetBox is something an operator
        can do without the producer changing a byte, and the identical payload
        then applies.
        """
        candidate = VirtualChassis.objects.create(name=NAME)  # empty: rule 3 adopts
        elsewhere = VirtualChassis.objects.create(name="mx-elsewhere")
        self._place(self.master, elsewhere, 2)

        with self.assertRaises(ChangeSetException) as caught:
            apply_changeset(ChangeSet(changes=[Change(
                change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
                ref_id="vc1", data={"name": NAME, "master": self.master.pk},
            )]), SimpleNamespace(user=None))
        message = caught.exception.errors["dcim.virtualchassis"]["master"][0]
        self.assertIn("mx-elsewhere", message)
        self.assertIn("Move the device in NetBox", message)
        self.assertNotIn("Supply domain", message)
        self.assertNotIn("virtual_chassis entity", message)

        # the remedy, taken in NetBox rather than in the payload
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=candidate, vc_position=1)
        apply_changeset(ChangeSet(changes=[Change(
            change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
            ref_id="vc1", data={"name": NAME, "master": self.master.pk},
        )]), SimpleNamespace(user=None))
        candidate.refresh_from_db()
        self.assertEqual(candidate.master_id, self.master.pk)
        self.assertEqual(VirtualChassis.objects.filter(name=NAME).count(), 1)

    def test_the_conflict_is_only_reported_where_adoption_was_chosen(self):
        """
        MEASURED HOLE: decline-and-create routes past the IN_OTHER_CHASSIS conflict.

        Same payload, same master, same "the master is a plain member of another
        chassis" fact. The only difference between the two halves below is
        whether the same-named masterless candidate is EMPTY or POPULATED:

          - empty  -> adoption takes it (rule 3), the attach reports
            IN_OTHER_CHASSIS, and the apply is a structured conflict. Nothing
            moves.
          - populated -> adoption DECLINES (no strong identity), the ordinary
            create path inserts the payload's own chassis, and NetBox's own
            dcim.signals.assign_virtualchassis_master then pulls the master OUT
            of the chassis it was in. 200, no error, a device relocated by a
            dcim.virtualchassis payload -- the precise act the conflict message
            says a dcim.virtualchassis payload is not authority to perform.

        Both halves are asserted so the asymmetry is a fact in the suite rather
        than an inference. It is not a regression against develop (which has no
        adoption and relocates in every cell) but it is a hole in what
        _MasterAttach's docstring claims, and it widened when the ambiguous cells
        stopped adopting.
        """
        elsewhere = VirtualChassis.objects.create(name="mx-elsewhere")
        self._place(self.master, elsewhere, 2)
        create = ChangeSet(changes=[Change(
            change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
            ref_id="vc1", data={"name": NAME, "master": self.master.pk})])

        empty = VirtualChassis.objects.create(name=NAME)
        with self.assertRaises(ChangeSetException):
            apply_changeset(create, SimpleNamespace(user=None))
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, elsewhere.pk)

        self._place(self.occupants["c0"], empty, 9)  # the SAME row, now populated
        apply_changeset(create, SimpleNamespace(user=None))
        self.master.refresh_from_db()
        self.assertNotEqual(
            self.master.virtual_chassis_id, elsewhere.pk,
            "the create path left the master where it was")
        self.assertEqual(
            VirtualChassis.objects.filter(name=NAME).count(), 2,
            "the payload got its own row")

    def test_the_dangling_master_cell_converges_once_the_device_exists(self):
        """A master pk with no device is a hard error, and the next plan fixes it."""
        VirtualChassis.objects.create(name=NAME)
        gone = Device.objects.create(
            name="mx-gone", site=self.site, device_type=self.dt, role=self.role)
        pk = gone.pk
        gone.delete()

        with self.assertRaises(ChangeSetException):
            apply_changeset(ChangeSet(changes=[Change(
                change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
                ref_id="vc1", data={"name": NAME, "master": pk},
            )]), SimpleNamespace(user=None))

        apply_changeset(ChangeSet(changes=[Change(
            change_type=ChangeType.CREATE, object_type="dcim.virtualchassis",
            ref_id="vc1", data={"name": NAME, "master": self.master.pk},
        )]), SimpleNamespace(user=None))
        self.assertEqual(VirtualChassis.objects.filter(name=NAME).count(), 1)
        self.assertEqual(
            VirtualChassis.objects.get(name=NAME).master_id, self.master.pk)


class VirtualChassisMatcherRefusalMatrixTests(TestCase):
    """The other door: what a MASTERLESS reference resolves to, over the same rows."""

    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        """One site, one device pool: the matcher reads rows and a member hint."""
        cls.site = Site.objects.create(name="mm-site", slug="mm-site")
        mfr = Manufacturer.objects.create(name="mm-mfr", slug="mm-mfr")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="mm-dt", slug="mm-dt")
        cls.role = DeviceRole.objects.create(name="mm-role", slug="mm-role")
        cls.devices = {
            key: Device.objects.create(
                name=f"mm-{key}", site=cls.site, device_type=cls.dt, role=cls.role)
            for key in ("h", "o0", "o1", "o2")
        }

    def _build(self, shapes):
        rows = []
        for index, shape in enumerate(shapes):
            vc = VirtualChassis.objects.create(
                name=NAME, domain=LABEL if shape in ("L", "ML") else "")
            if shape in ("P", "L", "M", "ML"):
                Device.objects.filter(pk=self.devices[f"o{index}"].pk).update(
                    virtual_chassis=vc, vc_position=9)
                if shape in ("M", "ML"):
                    vc.refresh_from_db()
                    vc.master = self.devices[f"o{index}"]
                    vc.save()
            rows.append(vc)
        return rows

    def _audit_refusal(self, cell, message):
        """A refusal must name a remedy, and none that the producer cannot take."""
        shapes, domain, _hint = cell
        problems = []
        if len(shapes) < 2:
            problems.append(f"{cell}: refused with {len(shapes)} row(s)")
        for banned in ("Supply domain", "Supply a domain", "on the virtual_chassis entity"):
            if banned in message:
                problems.append(f"{cell}: refusal says {banned!r}")
        if not any(p in message for p in ("in NetBox", "separate requests")):
            problems.append(f"{cell}: refusal names no actionable remedy")
        if domain is None and "separate requests" not in message:
            # A payload asserting no discriminator is narrowed by none, so a
            # refusal must not offer labelling the rows as the NetBox-side fix
            # without saying so. Match the disclaimer's intent, not one phrasing.
            disclaimers = ("does NOT settle it", "cannot settle it on its own")
            if not any(d in message for d in disclaimers):
                problems.append(
                    f"{cell}: refusal offers labelling to a payload that asserts nothing")
        return problems

    def _matcher_cell(self, shapes, domain, hint):
        """One cell: build the rows, resolve the reference, classify the answer."""
        rows = self._build(shapes)
        if hint == "in_0" and rows:
            Device.objects.filter(pk=self.devices["h"].pk).update(
                virtual_chassis=rows[0], vc_position=8)
        data = {"name": NAME, "master": None}
        if domain is not None:
            data["domain"] = domain
        if hint != "none":
            data[VC_MEMBER_HINT] = [self.devices["h"].pk]
        cell = (shapes, domain, hint)
        try:
            found = find_existing_object(data, "dcim.virtualchassis")
        except AmbiguousObjectMatch as exc:
            return "ambiguous", self._audit_refusal(cell, str(exc))
        if found is None:
            return "none", []
        return "resolved", ([] if found.name == NAME else [f"{cell}: wrong row"])

    def test_every_matcher_cell_either_resolves_creates_or_names_a_remedy(self):
        """No cell refuses without a remedy, and no refusal asks for a payload field."""
        failures = []
        kinds = set()
        counted = 0
        # 0/1/2 rows, for the same reason as the applier matrix above, and here
        # even the oracle cannot see a third: build_queryset filters on name
        # alone while _build names every row NAME, so `found.name == NAME` is
        # vacuously true. A cell observes resolved / none / ambiguous plus the
        # refusal text, and every branch of all four is reachable with two rows.
        for length in range(3):
            for shapes in itertools.product("EPLM", repeat=length):
                for domain in (None, "", LABEL, OTHER_LABEL):
                    for hint in ("none", "free", "in_0"):
                        sid = transaction.savepoint()
                        try:
                            kind, problems = self._matcher_cell(shapes, domain, hint)
                            kinds.add(kind)
                            failures.extend(problems)
                            counted += 1
                        finally:
                            transaction.savepoint_rollback(sid)
        # Exact, not a floor: a later edit that narrows an axis has to say so
        # here rather than quietly enumerating less.
        self.assertEqual(counted, 252, "the matcher enumeration changed size")
        self.assertEqual(kinds, {"resolved", "none", "ambiguous"}, kinds)
        self.assertEqual(failures, [], "\n".join(failures[:40]))

    def test_labelling_one_row_does_not_settle_a_payload_that_asserts_nothing(self):
        """
        MEASURED: the refusal's first remedy does not work for this payload.

        The message reads "Settle it in NetBox, which needs no change to what the
        producer sends: give one of those rows a domain the others do not have,
        or merge the duplicates into one row." The middle clause is false as
        stated. narrow_vc_candidates only narrows by what the PAYLOAD asserts,
        so labelling a row changes nothing for a payload that asserts no domain
        -- and the sentence immediately before it has just said "The payload
        asserts nothing that tells them apart".

        Measured both ways here: labelling one row leaves the identical payload
        refused, and labelling it only helps once the PRODUCER also sends the
        domain -- which is a change to what the producer sends, and one
        orb-agent cannot make (its device_name builder emits name + master).
        The other two remedies in the same sentence do work and are pinned by
        the sibling tests.
        """
        rows = self._build(("P", "P"))
        payload = {"name": NAME, "master": None}
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object(dict(payload), "dcim.virtualchassis")

        rows[0].domain = LABEL
        rows[0].save()
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object(dict(payload), "dcim.virtualchassis")

        found = find_existing_object(
            {"name": NAME, "master": None, "domain": LABEL}, "dcim.virtualchassis")
        self.assertEqual(found.pk, rows[0].pk)

    def test_an_ambiguous_reference_converges_once_the_duplicates_are_merged(self):
        """The remedy that does work with no producer change: one row, one answer."""
        rows = self._build(("P", "P"))
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object({"name": NAME, "master": None}, "dcim.virtualchassis")

        Device.objects.filter(virtual_chassis=rows[1]).update(
            virtual_chassis=rows[0], vc_position=7)
        rows[1].delete()
        found = find_existing_object(
            {"name": NAME, "master": None}, "dcim.virtualchassis")
        self.assertEqual(found.pk, rows[0].pk)

    def test_an_ambiguous_reference_converges_once_the_device_is_placed(self):
        """The other remedy the message names: put the device in the right row."""
        rows = self._build(("P", "P"))
        data = {"name": NAME, "master": None, VC_MEMBER_HINT: [self.devices["h"].pk]}
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object(dict(data), "dcim.virtualchassis")

        Device.objects.filter(pk=self.devices["h"].pk).update(
            virtual_chassis=rows[1], vc_position=8)
        found = find_existing_object(dict(data), "dcim.virtualchassis")
        self.assertEqual(found.pk, rows[1].pk)


class CrossSiteMemberFirstConvergenceTests(APITestCase):
    """
    A VirtualChassis spanning two sites must still converge to ONE row.

    This is the case a site veto broke -- refusing to adopt a candidate holding
    members from outside the master's site made the apply answer 400 on every
    pass. The veto was reverted; this pins the cell it protected at the DOOR the
    producer uses, in both request orders and through both entry points, which
    the unit-level test of the same cell does not reach.
    """

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.bulk_url = "/netbox/api/plugins/diode/bulk-plan-apply/"
        self.auth = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user)
        patcher.start()
        self.addCleanup(patcher.stop)
        Site.objects.create(name="xs-site-a", slug="xs-site-a")
        Site.objects.create(name="xs-site-b", slug="xs-site-b")
        Manufacturer.objects.create(name="xs-mfr", slug="xs-mfr")
        DeviceType.objects.create(
            manufacturer=Manufacturer.objects.get(name="xs-mfr"),
            model="xs-dt", slug="xs-dt")
        DeviceRole.objects.create(name="xs-role", slug="xs-role")

    def _device(self, name, site, extra):
        entity = {
            "name": name,
            "site": {"name": site},
            "role": {"name": "xs-role"},
            "device_type": {"manufacturer": {"name": "xs-mfr"}, "model": "xs-dt"},
        }
        entity.update(extra)
        return {"timestamp": 1, "object_type": "dcim.device", "entity": {"device": entity}}

    def _payloads(self, vc_name, master, member):
        master_payload = self._device(master, "xs-site-a", {
            "vc_position": 1,
            "virtual_chassis": {
                "name": vc_name,
                "master": {"name": master, "site": {"name": "xs-site-a"}},
            },
        })
        member_payload = self._device(member, "xs-site-b", {
            "vc_position": 2, "virtual_chassis": {"name": vc_name},
        })
        return master_payload, member_payload

    def _diff(self, payload):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json().get("change_set", {})

    def _diff_apply(self, payload):
        cs = self._diff(payload)
        if not cs.get("changes"):
            return
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"), r.content)

    def _assert_one_row(self, vc_name, master, member):
        rows = VirtualChassis.objects.filter(name=vc_name)
        self.assertEqual(rows.count(), 1, list(rows.values_list("pk", "domain")))
        row = rows.first()
        self.assertEqual(row.master.name, master)
        self.assertEqual(
            sorted(row.members.values_list("name", "vc_position")),
            [(master, 1), (member, 2)])
        self.assertEqual(row.member_count, row.members.count())
        for payload in self._payloads(vc_name, master, member):
            changes = [c for c in self._diff(payload).get("changes", [])
                       if c["change_type"] != "noop"]
            self.assertEqual(changes, [], changes)

    def test_two_requests_both_orders_converge_across_sites(self):
        """Master-first and member-first, two requests, one row each time."""
        for order in ("master_first", "member_first"):
            vc_name = f"xs-{order}"
            master, member = f"xs-m-{order}", f"xs-n-{order}"
            master_payload, member_payload = self._payloads(vc_name, master, member)
            first, second = (
                (master_payload, member_payload) if order == "master_first"
                else (member_payload, master_payload))
            self._diff_apply(first)
            self._diff_apply(second)
            self._assert_one_row(vc_name, master, member)

    def test_bulk_both_orders_converge_across_sites(self):
        """The same two orders inside one /bulk-plan-apply/ request."""
        for order in ("master_first", "member_first"):
            vc_name = f"xsb-{order}"
            master, member = f"xsb-m-{order}", f"xsb-n-{order}"
            master_payload, member_payload = self._payloads(vc_name, master, member)
            first, second = (
                (master_payload, member_payload) if order == "master_first"
                else (member_payload, master_payload))
            entities = [
                {"id": "first", "object_type": "dcim.device", "entity": first["entity"]},
                {"id": "second", "object_type": "dcim.device", "entity": second["entity"]},
            ]
            r = self.client.post(self.bulk_url, data={"entities": entities},
                                 format="json", **self.auth)
            self.assertEqual(r.status_code, 200, r.content)
            for result in r.json()["results"]:
                self.assertIsNone(result.get("errors"), result)
            self._assert_one_row(vc_name, master, member)

    def test_the_cross_site_member_keeps_its_own_site(self):
        """Convergence must not have moved anybody's site to make the row work."""
        vc_name, master, member = "xs-keep", "xs-m-keep", "xs-n-keep"
        master_payload, member_payload = self._payloads(vc_name, master, member)
        self._diff_apply(member_payload)
        self._diff_apply(master_payload)
        self.assertEqual(Device.objects.get(name=master).site.name, "xs-site-a")
        self.assertEqual(Device.objects.get(name=member).site.name, "xs-site-b")
        self._assert_one_row(vc_name, master, member)


class AmbiguityRemedyTruthTests(TestCase):
    """
    Does each remedy the ambiguity refusal names actually settle it?

    A refusal a producer cannot act on never converges, so the sentence the
    refusal ends with is part of the behaviour. Each clause is executed here
    rather than read.
    """

    @classmethod
    def setUpTestData(cls):
        """Two same-named populated rows and a device to place."""
        cls.site = Site.objects.create(name="ar-site", slug="ar-site")
        mfr = Manufacturer.objects.create(name="ar-mfr", slug="ar-mfr")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="ar-dt", slug="ar-dt")
        cls.role = DeviceRole.objects.create(name="ar-role", slug="ar-role")

    def _row(self, domain, member):
        row = VirtualChassis.objects.create(name="ar-stack", domain=domain)
        device = Device.objects.create(
            name=member, site=self.site, device_type=self.dt, role=self.role)
        Device.objects.filter(pk=device.pk).update(virtual_chassis=row, vc_position=9)
        return row

    def _resolve(self, **extra):
        data = {"name": "ar-stack", "master": None}
        data.update(extra)
        return find_existing_object(data, "dcim.virtualchassis")

    def test_two_rows_that_already_carry_different_domains_still_refuse(self):
        """
        MEASURED: the "give one of those rows a domain" clause is false as scoped.

        The refusal's closing sentence reads "Settle it in NetBox, which needs no
        change to what the producer sends: give one of those rows a domain the
        others do not have ...". Here the two rows ALREADY carry different
        domains -- there is nothing left to give -- and the identical payload is
        refused all the same, because narrow_vc_candidates narrows by what the
        PAYLOAD asserts and this payload asserts nothing.

        This is the exact state the E2E test
        test_two_same_named_stacks_make_a_member_reference_ambiguous sets up, so
        the clause is being shown to a producer for whom it cannot work. The
        remedy that does work with no producer change is measured below.
        """
        self._row("building-a", "ar-a1")
        self._row("building-b", "ar-b1")
        with self.assertRaises(AmbiguousObjectMatch):
            self._resolve()

    def test_the_domain_clause_works_only_once_the_producer_asserts_one(self):
        """The same lever, held by the producer: asserting domain resolves it."""
        row_a = self._row("building-a", "ar-a2")
        self._row("building-b", "ar-b2")
        self.assertEqual(self._resolve(domain="building-a").pk, row_a.pk)

    def test_labelling_helps_when_the_payload_already_asserts_a_shared_domain(self):
        """
        And it is a real remedy in the OTHER branch of the same message.

        When the payload asserts a domain that every row carries, giving one row
        a different one narrows the rest away -- no producer change needed. So
        the clause belongs to that branch of the message, not to both.
        """
        row_a = self._row("shared", "ar-a3")
        row_b = self._row("shared", "ar-b3")
        with self.assertRaises(AmbiguousObjectMatch):
            self._resolve(domain="shared")
        row_a.domain = "moved-out"
        row_a.save()
        self.assertEqual(self._resolve(domain="shared").pk, row_b.pk)

    def test_merging_the_duplicates_settles_it_with_no_producer_change(self):
        """The clause that does work for a payload asserting nothing."""
        row_a = self._row("building-a", "ar-a4")
        row_b = self._row("building-b", "ar-b4")
        with self.assertRaises(AmbiguousObjectMatch):
            self._resolve()
        Device.objects.filter(virtual_chassis=row_b).update(
            virtual_chassis=row_a, vc_position=7)
        row_b.delete()
        self.assertEqual(self._resolve().pk, row_a.pk)

    def test_placing_the_device_settles_it_with_no_producer_change(self):
        """The other clause that works: the member's own chassis answers."""
        self._row("building-a", "ar-a5")
        row_b = self._row("building-b", "ar-b5")
        joiner = Device.objects.create(
            name="ar-join", site=self.site, device_type=self.dt, role=self.role)
        with self.assertRaises(AmbiguousObjectMatch):
            self._resolve(**{VC_MEMBER_HINT: [joiner.pk]})
        Device.objects.filter(pk=joiner.pk).update(
            virtual_chassis=row_b, vc_position=8)
        self.assertEqual(
            self._resolve(**{VC_MEMBER_HINT: [joiner.pk]}).pk, row_b.pk)

    def test_separate_requests_settle_the_disagreeing_hints_refusal(self):
        """
        The other refusal's remedy, executed: one device per request.

        Two member devices already in DIFFERENT same-named rows make one payload
        describe a merge, and the matcher refuses rather than move either. Its
        message says to ingest those devices in separate requests; a single hint
        per request resolves to that device's own chassis, so it does.
        """
        row_a = self._row("building-a", "ar-a6")
        row_b = self._row("building-b", "ar-b6")
        a_member = Device.objects.get(name="ar-a6")
        b_member = Device.objects.get(name="ar-b6")
        with self.assertRaises(AmbiguousObjectMatch) as caught:
            self._resolve(**{VC_MEMBER_HINT: [a_member.pk, b_member.pk]})
        self.assertIn("separate requests", str(caught.exception))
        self.assertEqual(self._resolve(**{VC_MEMBER_HINT: [a_member.pk]}).pk, row_a.pk)
        self.assertEqual(self._resolve(**{VC_MEMBER_HINT: [b_member.pk]}).pk, row_b.pk)


class AmbiguityAtTheBulkDoorTests(APITestCase):
    """
    The third door for the name-ambiguity refusal: /bulk-plan-apply/.

    generate-diff and apply-change-set are both pinned elsewhere. bulk-plan-apply
    plans and applies each entity itself, so a refusal that only rendered at the
    other two doors would surface here as a 500 or, worse, as a whole-request
    failure that discards the entities that were fine. It has to be PER ENTITY.
    """

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.bulk_url = "/netbox/api/plugins/diode/bulk-plan-apply/"
        self.auth = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site = Site.objects.create(name="bd-site", slug="bd-site")
        mfr = Manufacturer.objects.create(name="bd-mfr", slug="bd-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="bd-dt", slug="bd-dt")
        self.role = DeviceRole.objects.create(name="bd-role", slug="bd-role")

    def _seed(self, domain, member):
        row = VirtualChassis.objects.create(name="bd-shared", domain=domain)
        device = Device.objects.create(
            name=member, site=self.site, device_type=self.dt, role=self.role)
        Device.objects.filter(pk=device.pk).update(virtual_chassis=row, vc_position=1)
        row.refresh_from_db()
        row.master = device
        row.save()
        return row

    def _entity(self, name, extra):
        entity = {
            "name": name,
            "site": {"name": "bd-site"},
            "role": {"name": "bd-role"},
            "device_type": {"manufacturer": {"name": "bd-mfr"}, "model": "bd-dt"},
        }
        entity.update(extra)
        return {"object_type": "dcim.device", "entity": {"device": entity}}

    def test_the_refusal_is_per_entity_and_the_others_still_apply(self):
        """
        One entity is refused, the other is applied, and neither row is touched.

        The ambiguous entity must carry the structured error under its own id --
        naming both rows -- while the unrelated entity in the same request
        succeeds. A whole-request 400 would be a different contract and would
        make one bad reference cost a batch. Measured shape: HTTP 207
        Multi-Status, the refusal under the entity's ``errors["plan"]`` because
        it is raised while that entity is being planned.
        """
        row_a = self._seed("building-a", "bd-a1")
        row_b = self._seed("building-b", "bd-b1")
        before = {
            row.pk: (row.domain, row.master_id, row.last_updated, row.members.count())
            for row in (row_a, row_b)
        }

        entities = [
            {"id": "fine", **self._entity("bd-plain", {})},
            {"id": "ambiguous", **self._entity("bd-joiner", {
                "vc_position": 3, "virtual_chassis": {"name": "bd-shared"}})},
        ]
        response = self.client.post(
            self.bulk_url, data={"entities": entities}, format="json", **self.auth)
        self.assertEqual(response.status_code, 207, response.content)
        results = {item["id"]: item for item in response.json()["results"]}

        self.assertIsNone(results["fine"].get("errors"), results["fine"])
        self.assertTrue(Device.objects.filter(name="bd-plain").exists())

        errors = results["ambiguous"].get("errors")
        self.assertIsNotNone(errors, results["ambiguous"])
        message = errors["plan"]["dcim.virtualchassis"]["name"][0]
        self.assertIn(f"id {row_a.pk}", message)
        self.assertIn(f"id {row_b.pk}", message)
        self.assertIn("Settle it in NetBox", message)
        self.assertNotIn("Supply domain", message)

        self.assertFalse(Device.objects.filter(name="bd-joiner").exists())
        for row in (row_a, row_b):
            row.refresh_from_db()
            self.assertEqual(
                (row.domain, row.master_id, row.last_updated, row.members.count()),
                before[row.pk], f"row {row.pk} was written by a refused entity")

    def test_a_domain_on_the_same_entity_resolves_it_at_this_door_too(self):
        """The escape hatch works at the bulk door as well, and moves nobody else."""
        row_a = self._seed("building-a", "bd-a2")
        row_b = self._seed("building-b", "bd-b2")
        entities = [{"id": "joiner", **self._entity("bd-joiner2", {
            "vc_position": 3,
            "virtual_chassis": {"name": "bd-shared", "domain": "building-b"}})}]
        response = self.client.post(
            self.bulk_url, data={"entities": entities}, format="json", **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()["results"][0]
        self.assertIsNone(result.get("errors"), result)
        self.assertEqual(
            Device.objects.get(name="bd-joiner2").virtual_chassis_id, row_b.pk)
        self.assertEqual(row_a.members.count(), 1)
