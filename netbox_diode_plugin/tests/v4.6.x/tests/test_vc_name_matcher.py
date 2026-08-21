"""Unit tests for masterless VirtualChassis name matching."""
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api.applier import _is_auto_created_component, apply_changeset
from netbox_diode_plugin.api.common import (
    VC_MEMBER_HINT,
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeType,
    UnresolvedReference,
)
from netbox_diode_plugin.api.matcher import (
    _REQUIRES_PRE_SAVE_MATCH,
    AmbiguousObjectMatch,
    find_existing_object,
    fingerprints,
    get_model_matchers,
    pre_save_match_binds_only,
    requires_pre_save_match,
    vc_hint_pks,
)


def _apply_vc_create(data, extra_changes=()):
    """Apply a single dcim.virtualchassis CREATE, exactly as a changeset would."""
    cs = ChangeSet(changes=[Change(
        change_type=ChangeType.CREATE,
        object_type="dcim.virtualchassis",
        ref_id="vc1",
        data=data,
    ), *extra_changes])
    return apply_changeset(cs, SimpleNamespace(user=None))


class VirtualChassisNameMatcherTests(TestCase):
    """Name-only VC payloads must bind existing VCs; master-bearing ones must not."""

    @classmethod
    def setUpTestData(cls):
        """Seed two same-named VCs (older mastered, newer empty) plus a distinct one."""
        site = Site.objects.create(name="vcm-site", slug="vcm-site")
        mfr = Manufacturer.objects.create(name="vcm-mfr", slug="vcm-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vcm-dt", slug="vcm-dt")
        role = DeviceRole.objects.create(name="vcm-role", slug="vcm-role")
        cls.master = Device.objects.create(
            name="vcm-sw1", site=site, device_type=dt, role=role
        )
        cls.vc_old = VirtualChassis.objects.create(name="vcm-stack", master=cls.master)
        Device.objects.filter(pk=cls.master.pk).update(
            virtual_chassis=cls.vc_old, vc_position=1
        )
        cls.vc_dup = VirtualChassis.objects.create(name="vcm-stack")  # newer, masterless
        cls.vc_other = VirtualChassis.objects.create(name="vcm-other")

    def test_matcher_registration_and_order(self):
        """The logical name matcher precedes the auto-derived unique_master."""
        names = [m.name for m in get_model_matchers(VirtualChassis)]
        self.assertEqual(names[0], "logical_vc_name_no_master")
        self.assertIn("unique_master", names)

    def test_name_only_payload_matches_mastered_vc(self):
        """A masterless payload binds by name even when the DB row HAS a master."""
        found = find_existing_object({"name": "vcm-other"}, "dcim.virtualchassis")
        self.assertEqual(found, self.vc_other)

    def test_a_populated_row_beats_an_empty_same_named_duplicate(self):
        """
        Two rows, one real: resolve the real one -- NOT because it is older.

        THE OLD EXPECTATION HERE WAS UNSAFE: this assertion used to read
        "oldest pk wins over the newer duplicate", which is the policy, not the
        reason. The seed happens to make the mastered row the older one, so the
        two rules were indistinguishable in this fixture and the assertion
        passed under a rule that also picks an arbitrary row when BOTH
        candidates are real stacks (see
        VirtualChassisAmbiguityTests.test_two_populated_rows_refuse_to_resolve).

        What holds now is a fact about the rows: vc_dup has no master and no
        members, so it is not a stack anyone owns, and exactly one candidate is.
        """
        found = find_existing_object({"name": "vcm-stack"}, "dcim.virtualchassis")
        self.assertEqual(found, self.vc_old)
        self.assertIsNone(self.vc_dup.master_id)
        self.assertEqual(self.vc_dup.members.count(), 0)

    def test_explicit_null_master_counts_as_masterless(self):
        """master: None gates the same as an absent master key."""
        found = find_existing_object(
            {"name": "vcm-other", "master": None}, "dcim.virtualchassis"
        )
        self.assertEqual(found, self.vc_other)

    def test_master_bearing_payload_ignores_name_matcher(self):
        """With master present, unique_master is authoritative."""
        found = find_existing_object(
            {"name": "totally-different-name", "master": self.master.pk},
            "dcim.virtualchassis",
        )
        self.assertEqual(found, self.vc_old)

    def test_no_hit_returns_none(self):
        """Unknown name with no master creates (returns None)."""
        self.assertIsNone(
            find_existing_object({"name": "vcm-missing"}, "dcim.virtualchassis")
        )

    def test_non_string_name_never_reaches_filter(self):
        """A malformed name is ignored by the matcher, not raised."""
        self.assertIsNone(
            find_existing_object({"name": ["vcm-other"]}, "dcim.virtualchassis")
        )

    def test_fingerprint_emitted_regardless_of_master(self):
        """Name-only and master-bearing entities share the name fingerprint."""
        fp_no_master = fingerprints({"name": "x-stack"}, "dcim.virtualchassis")
        fp_master = fingerprints(
            {"name": "x-stack", "master": 1}, "dcim.virtualchassis"
        )
        shared = set(fp_no_master) & set(fp_master)
        self.assertTrue(shared, "expected a shared name-keyed fingerprint")


class VirtualChassisHintPkTests(SimpleTestCase):
    """
    The member-hint type filter, pinned without a database.

    Through find_existing_object this guard is invisible: a hint of [True] means
    pk 1, and whether pk 1 happens to be a member of one of the candidates
    depends on a sequence the test cannot control -- so the behavioural test
    passes either way and the guard is unpinned. Asserting the extraction
    directly is the only way to fail when the bool exclusion is removed.
    """

    def test_only_real_pks_survive(self):
        """Every non-pk form the wire or the transformer can put in the hint."""
        self.assertEqual(vc_hint_pks([3, 7]), [3, 7])
        self.assertEqual(vc_hint_pks([True, False]), [], "bool read as pk 1/0")
        self.assertEqual(vc_hint_pks([3, True, None, "4", [5], 7.5]), [3])
        self.assertEqual(vc_hint_pks(UnresolvedReference("dcim.device", "u")), [])
        self.assertEqual(
            vc_hint_pks([UnresolvedReference("dcim.device", "u"), 9]), [9],
            "a device this batch is still creating carries no evidence",
        )

    def test_absent_and_scalar_forms(self):
        """None means no hint; a bare pk is accepted as a one-item hint."""
        self.assertEqual(vc_hint_pks(None), [])
        self.assertEqual(vc_hint_pks([]), [])
        self.assertEqual(vc_hint_pks(5), [5])


class VirtualChassisAmbiguityTests(TestCase):
    """
    What a name that matches SEVERAL real chassis resolves to: nothing.

    find_existing_object's framework default is order_by('pk').first(), and for
    a name-keyed match on a model with no uniqueness on name that is a policy
    rather than a lookup. The rows here are two legitimately distinct stacks
    that happen to share a name -- the state the default silently picks a winner
    from, and (because the reference being resolved is usually a member device's
    virtual_chassis) plans a device move out of.

    These are unit tests on find_existing_object because that is the single door
    both the plan path and the direct-apply path go through; the end-to-end
    consequences are in test_virtualchassis_ingest.
    """

    @classmethod
    def setUpTestData(cls):
        """Two same-named, differently-domained, both-populated chassis."""
        site = Site.objects.create(name="vcx-site", slug="vcx-site")
        mfr = Manufacturer.objects.create(name="vcx-mfr", slug="vcx-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vcx-dt", slug="vcx-dt")
        role = DeviceRole.objects.create(name="vcx-role", slug="vcx-role")
        cls.kw = {"site": site, "device_type": dt, "role": role}

        cls.a1 = Device.objects.create(name="vcx-a1", **cls.kw)
        cls.vc_a = VirtualChassis.objects.create(name="vcx-shared", domain="building-a")
        Device.objects.filter(pk=cls.a1.pk).update(virtual_chassis=cls.vc_a, vc_position=1)
        cls.vc_a.refresh_from_db()
        cls.vc_a.master = cls.a1
        cls.vc_a.save()

        cls.b1 = Device.objects.create(name="vcx-b1", **cls.kw)
        cls.vc_b = VirtualChassis.objects.create(name="vcx-shared", domain="building-b")
        Device.objects.filter(pk=cls.b1.pk).update(virtual_chassis=cls.vc_b, vc_position=1)
        cls.vc_b.refresh_from_db()
        cls.vc_b.master = cls.b1
        cls.vc_b.save()

        cls.loose = Device.objects.create(name="vcx-loose", **cls.kw)

    def test_two_populated_rows_refuse_to_resolve(self):
        """
        The core refusal, with an error that names both rows and the way out.

        The way out has to be one somebody can take. This refusal is reached by
        a payload that carries a name and nothing else, so "supply a domain on
        the reference" is advice for a producer that has a domain to supply --
        orb-agent does not emit one at all. Every remedy here is therefore a
        NetBox-side action that leaves the payload untouched.

        And it must not offer a NetBox-side action that does not work either.
        These two rows already carry DIFFERENT domains, and the payload still
        cannot be resolved, because narrowing looks at the values the PAYLOAD
        asserts: labelling rows changes nothing while it asserts none. The
        message used to end "give one of those rows a domain the others do not
        have", under the heading "needs no change to what the producer sends" --
        advice already satisfied by this very fixture. It now says so, and the
        domain appears only as the pair it really is (producer sends it, row
        carries it). test_the_remedy_the_refusal_names_actually_resolves_it
        walks the remedies it does name.
        """
        self.assertNotEqual(self.vc_a.domain, self.vc_b.domain)
        with self.assertRaises(AmbiguousObjectMatch) as caught:
            find_existing_object({"name": "vcx-shared"}, "dcim.virtualchassis")
        message = str(caught.exception)
        self.assertIn("vcx-shared", message)
        self.assertIn(f"id {self.vc_a.pk}", message)
        self.assertIn(f"id {self.vc_b.pk}", message)
        self.assertIn("Settle it in NetBox", message)
        self.assertIn("merge the duplicates", message)
        self.assertNotIn("Supply domain", message)
        self.assertIn("labelling these rows cannot settle it on its own", message)
        self.assertNotIn("the others do not have", message)

    def test_the_remedy_the_refusal_names_actually_resolves_it(self):
        """
        Every remedy in the message, taken literally, and the outcome measured.

        A structured error is only worth its space if following it changes the
        answer. Three states, one payload ({"name": ...} and nothing else):

          - label one row: STILL ambiguous. This is the advice the message used
            to lead with, and it is inert here -- narrow_vc_candidates iterates
            the discriminators the payload asserts, and this payload asserts
            none, so no labelling of any row narrows anything.
          - place the referencing device in one of them: resolves, to that row,
            through rule 1 (the member hint).
          - merge the duplicates (here: delete the row nobody meant, the
            operator's other lever): resolves, because one candidate is left.
        """
        VirtualChassis.objects.filter(pk=self.vc_a.pk).update(domain="vcx-relabelled")
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object({"name": "vcx-shared"}, "dcim.virtualchassis")

        Device.objects.filter(pk=self.loose.pk).update(
            virtual_chassis=self.vc_b, vc_position=9)
        self.assertEqual(
            find_existing_object(
                {"name": "vcx-shared", VC_MEMBER_HINT: [self.loose.pk]},
                "dcim.virtualchassis"),
            self.vc_b,
        )

        Device.objects.filter(pk=self.loose.pk).update(
            virtual_chassis=None, vc_position=None)
        Device.objects.filter(pk=self.a1.pk).update(
            virtual_chassis=None, vc_position=None)
        VirtualChassis.objects.filter(pk=self.vc_a.pk).update(master=None)
        VirtualChassis.objects.filter(pk=self.vc_a.pk).delete()
        self.assertEqual(
            find_existing_object({"name": "vcx-shared"}, "dcim.virtualchassis"),
            self.vc_b,
        )

    def test_the_refusal_carries_the_per_entity_error_shape(self):
        """
        It has to be reportable, not just raised.

        Both API boundaries render a ChangeSetException's ``errors`` dict
        directly, so the shape is the contract: {object_type: {field: [msg]}}.
        A bare exception here would surface as a 500 at one door or the other.
        """
        with self.assertRaises(ChangeSetException) as caught:
            find_existing_object({"name": "vcx-shared"}, "dcim.virtualchassis")
        errors = caught.exception.errors
        self.assertIn("dcim.virtualchassis", errors)
        self.assertIn("name", errors["dcim.virtualchassis"])
        self.assertEqual(len(errors["dcim.virtualchassis"]["name"]), 1)

    def test_the_refusal_is_not_a_malformed_reference(self):
        """
        It must not be a ValueError or TypeError, and this is not pedantry.

        Three call sites swallow those two to turn a payload the ORM cannot
        query into "no match" (applier._find_existing_object_or_none,
        _try_find_and_update_existing_instance, and apply_changeset's own
        handler chain). An ambiguity that inherited from either would be
        swallowed the same way -- and "no match" for a name that matched twice
        means INSERT A THIRD ROW.
        """
        self.assertFalse(issubclass(AmbiguousObjectMatch, ValueError))
        self.assertFalse(issubclass(AmbiguousObjectMatch, TypeError))
        self.assertTrue(issubclass(AmbiguousObjectMatch, ChangeSetException))

    def test_a_discriminator_resolves_one_row(self):
        """A domain is a claim about which row, so it resolves -- to the named one."""
        self.assertEqual(
            find_existing_object(
                {"name": "vcx-shared", "domain": "building-b"}, "dcim.virtualchassis"),
            self.vc_b,
        )
        self.assertEqual(
            find_existing_object(
                {"name": "vcx-shared", "domain": "building-a"}, "dcim.virtualchassis"),
            self.vc_a,
        )

    def test_a_discriminator_no_row_carries_is_a_create_not_an_ambiguity(self):
        """
        A domain no candidate has means "not one of these rows".

        Returning None here is the difference between creating the chassis the
        payload describes and binding one whose own discriminator contradicts
        it. Raising instead would make a legitimate third stack unrepresentable.
        """
        self.assertIsNone(
            find_existing_object(
                {"name": "vcx-shared", "domain": "building-c"}, "dcim.virtualchassis")
        )

    def test_an_empty_domain_excludes_every_row_that_carries_one(self):
        """
        An explicitly submitted empty domain is a value, not an absence.

        Both rows here carry a domain, so a payload declaring the chassis has
        none describes NEITHER of them -- which is the create case, not the
        ambiguous one. The "" used to be dropped as if the key were missing, so
        this call refused as ambiguous and, wherever it did resolve, the ""
        was then written over the matched row's domain: the discriminator the
        refusal message asks the operator to set, destroyed by the payload that
        should have been excluded by it.
        """
        self.assertIsNone(
            find_existing_object(
                {"name": "vcx-shared", "domain": ""}, "dcim.virtualchassis")
        )

    def test_an_empty_domain_narrows_to_the_row_that_carries_none(self):
        """
        Exclusion is all "" does, and it is enough to resolve a third row.

        It excludes building-a and building-b and leaves one candidate, which
        resolves like any other single candidate. It never IDENTIFIES a row --
        every chassis that never set a domain carries "" -- which is why
        applier._choose_adoption_candidate will not let it authorise adopting a
        populated row the way a real domain does.
        """
        plain = VirtualChassis.objects.create(name="vcx-shared")
        member = Device.objects.create(name="vcx-c1", **self.kw)
        Device.objects.filter(pk=member.pk).update(virtual_chassis=plain, vc_position=1)
        self.assertEqual(
            find_existing_object(
                {"name": "vcx-shared", "domain": ""}, "dcim.virtualchassis"),
            plain,
        )

    def test_a_refusal_does_not_ask_for_what_the_payload_already_supplied(self):
        """
        The remedy has to be actionable, and this one used to be a loop.

        Two rows share the name AND the domain, so the payload's discriminator
        is asserted, matched, and still does not tell them apart. Every one of
        these refusals used to end "Supply domain ... to identify it" -- telling
        the operator to do the thing they just did, which is how a structured
        error stops being a way out and becomes noise.
        """
        twin_a = VirtualChassis.objects.create(name="vcx-twin", domain="vcx-both")
        twin_b = VirtualChassis.objects.create(name="vcx-twin", domain="vcx-both")
        for row, name in ((twin_a, "vcx-t1"), (twin_b, "vcx-t2")):
            member = Device.objects.create(name=name, **self.kw)
            Device.objects.filter(pk=member.pk).update(virtual_chassis=row, vc_position=1)

        with self.assertRaises(AmbiguousObjectMatch) as caught:
            find_existing_object(
                {"name": "vcx-twin", "domain": "vcx-both"}, "dcim.virtualchassis")
        message = str(caught.exception)
        self.assertIn(f"id {twin_a.pk}", message)
        self.assertIn(f"id {twin_b.pk}", message)
        self.assertIn("asserts domain 'vcx-both'", message)
        self.assertNotIn("Supply domain", message)
        self.assertIn("Settle it in NetBox", message)

    def test_the_member_a_device_is_already_in_wins(self):
        """
        The rule that guarantees no relocation, and it needs the member hint.

        The hint is what the transformer pushes onto a nested chassis node from
        the device that referenced it (common.VC_MEMBER_HINT); the matcher
        cannot see the device any other way. With it, a device already in the
        NEWER duplicate resolves to that one -- against creation order, which is
        the point.
        """
        Device.objects.filter(pk=self.loose.pk).update(
            virtual_chassis=self.vc_b, vc_position=2)
        found = find_existing_object(
            {"name": "vcx-shared", VC_MEMBER_HINT: [self.loose.pk]},
            "dcim.virtualchassis",
        )
        self.assertEqual(found, self.vc_b)

    def test_a_hint_for_a_chassis_less_device_carries_no_evidence(self):
        """A device in no chassis cannot break the tie, so the refusal stands."""
        with self.assertRaises(AmbiguousObjectMatch):
            find_existing_object(
                {"name": "vcx-shared", VC_MEMBER_HINT: [self.loose.pk]},
                "dcim.virtualchassis",
            )

    def test_hints_that_disagree_refuse_rather_than_pick_a_side(self):
        """
        Two referencing members already in DIFFERENT same-named chassis.

        Whichever row were chosen, the other member would be moved out of the
        stack it is in, so there is no answer to give. (Several members of one
        chassis in one batch dedupe into a single chassis node whose hint is the
        union -- transformer._merge_nodes -- which is how both pks arrive here.)
        """
        with self.assertRaises(AmbiguousObjectMatch) as caught:
            find_existing_object(
                {"name": "vcx-shared", VC_MEMBER_HINT: [self.a1.pk, self.b1.pk]},
                "dcim.virtualchassis",
            )
        self.assertIn("DIFFERENT", str(caught.exception))

    def test_a_bogus_hint_value_is_a_refusal_not_a_valueerror(self):
        """
        A malformed hint must not reach the ORM as a pk.

        Every form here carries no evidence, so the refusal is the right answer.
        What this pins is that it IS a refusal and not a ValueError/TypeError
        out of query construction -- the applier turns those into "no match",
        which for a name that matched twice means inserting a third row.

        It does NOT pin the bool exclusion: [True] would mean pk 1, and whether
        pk 1 is a member of one of these candidates depends on a sequence no
        test controls. VirtualChassisHintPkTests pins that directly.
        """
        for hint in ([True], [None], ["abc"], [[1]], [], None, self.loose.pk):
            with self.subTest(hint=hint):
                with self.assertRaises(AmbiguousObjectMatch):
                    find_existing_object(
                        {"name": "vcx-shared", VC_MEMBER_HINT: hint},
                        "dcim.virtualchassis",
                    )

    def test_a_row_resolved_before_the_duplicate_appeared_is_not_served_from_cache(self):
        """
        The find-object cache must not be able to answer this question at all.

        find_obj_cache_ttl defaults to 30s and the key is built from SCALAR
        fields only, so a name-keyed VirtualChassis lookup would cache one row
        under a key that cannot see the member hint OR the appearance of a
        second same-named row. Two consequences, both silent: a payload naming
        the chassis on behalf of a DIFFERENT member device would be served the
        first device's answer, and an ambiguity that must be reported would be
        answered with whatever row was cached before the duplicate existed.
        A cache hit also skips resolve() entirely, so the whole policy would be
        bypassed rather than merely stale.

        _find_obj_cache_key therefore declines to key dcim.virtualchassis at
        all, which disables both the django cache and the request-scoped one.
        """
        with mock.patch(
            "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl", return_value=30
        ):
            self.assertEqual(
                find_existing_object(
                    {"name": "vcx-cached", "domain": "building-a"}, "dcim.virtualchassis"),
                None,
            )
            first = VirtualChassis.objects.create(name="vcx-cached", domain="building-a")
            Device.objects.filter(pk=self.loose.pk).update(
                virtual_chassis=first, vc_position=1)
            self.assertEqual(
                find_existing_object({"name": "vcx-cached"}, "dcim.virtualchassis"), first)

            second = VirtualChassis.objects.create(name="vcx-cached", domain="building-b")
            other = Device.objects.create(name="vcx-cached-b1", **self.kw)
            Device.objects.filter(pk=other.pk).update(
                virtual_chassis=second, vc_position=1)

            with self.assertRaises(AmbiguousObjectMatch):
                find_existing_object({"name": "vcx-cached"}, "dcim.virtualchassis")

    def test_a_unique_candidate_still_resolves(self):
        """
        The floor: none of the above may cost the ordinary case.

        One row with the name, no hint, no discriminator -- this is what the
        whole feature is for, and every rule above is scoped to the multi-row
        case so that it stays a plain resolution.
        """
        only = VirtualChassis.objects.create(name="vcx-only", domain="building-z")
        self.assertEqual(
            find_existing_object({"name": "vcx-only"}, "dcim.virtualchassis"), only)
        self.assertIsNone(
            find_existing_object({"name": "vcx-nothing"}, "dcim.virtualchassis"))


class VirtualChassisPreSaveMatchScopeTests(TestCase):
    """
    What the find-first CREATE route does, and to which payloads it applies.

    For dcim.virtualchassis the route RESOLVES a CREATE onto an existing row
    and writes nothing to it (matcher._PRE_SAVE_MATCH_BIND_ONLY); for every
    other entry in matcher._REQUIRES_PRE_SAVE_MATCH it is an UPDATE path that
    applies the CREATE's payload to whatever find_existing_object returns. The
    scope of the routing is a behavioural contract with two halves either way,
    and both are asserted here through a real apply. (The test this replaced
    asserted only that "dcim.virtualchassis" was a member of the set literal
    that defines the route, which is true by construction and cannot fail for
    any reason a reader would care about.)
    """

    @classmethod
    def setUpTestData(cls):
        """A chassis-less device, plus a device that already masters a chassis."""
        site = Site.objects.create(name="vcs-site", slug="vcs-site")
        mfr = Manufacturer.objects.create(name="vcs-mfr", slug="vcs-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vcs-dt", slug="vcs-dt")
        role = DeviceRole.objects.create(name="vcs-role", slug="vcs-role")
        cls.free = Device.objects.create(
            name="vcs-free", site=site, device_type=dt, role=role
        )
        cls.owner = Device.objects.create(
            name="vcs-owner", site=site, device_type=dt, role=role
        )
        cls.owned = VirtualChassis.objects.create(name="vcs-owned", master=cls.owner)
        Device.objects.filter(pk=cls.owner.pk).update(
            virtual_chassis=cls.owned, vc_position=1
        )

    def test_masterless_create_binds_the_existing_row_without_writing_it(self):
        """
        The half the route exists for: a masterless CREATE binds the row by name.

        This is the plan-ahead race in miniature. VirtualChassis has no unique
        constraint on name, so without the pre-save lookup this CREATE inserts
        a second row and the chassis is split across two, half its members
        orphaned on each.

        Binding is the whole of what the race needs, and the payload is
        deliberately NOT applied. The same absent uniqueness that makes the
        lookup necessary also makes it insufficient as an identity claim: the
        row matched by name may be a different stack another source owns (see
        matcher._PRE_SAVE_MATCH_BIND_ONLY). last_updated carries the no-write
        assertion, because on a row that was already blank "untouched" and
        "saved with the payload minus its description" look alike.
        """
        existing = VirtualChassis.objects.create(name="vcs-race")
        before = existing.last_updated

        _apply_vc_create({"name": "vcs-race", "description": "from the second plan"})

        self.assertEqual(VirtualChassis.objects.filter(name="vcs-race").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.description, "")
        self.assertEqual(existing.last_updated, before, "the bound row was saved")

    def test_master_bearing_create_does_not_become_an_update(self):
        """
        The half the route must NOT cover: a CREATE may not rewrite another chassis.

        With master present the matcher that answers find_existing_object is
        the auto-derived unique_master one, so a master-bearing CREATE on the
        pre-save path resolves "the chassis named vcs-renamed, mastered by
        vcs-owner" onto the chassis vcs-owner already masters.

        What that resolution would DO depends on the other seam: before
        _PRE_SAVE_MATCH_BIND_ONLY it renamed that converged, unrelated row on a
        create; with bind-only in force it would bind to it silently instead,
        so the named chassis is never created and later references resolve to
        the wrong row. Both are worth excluding, but only the first was a
        write, and the assertions below now hold for either reason -- see
        test_route_is_scoped_to_masterless_payloads for what actually pins the
        gate.
        """
        _apply_vc_create({
            "name": "vcs-renamed",
            "master": self.owner.pk,
            "description": "must not land anywhere",
        })

        self.owned.refresh_from_db()
        self.assertEqual(self.owned.name, "vcs-owned")
        self.assertEqual(self.owned.description, "")
        self.assertFalse(VirtualChassis.objects.filter(name="vcs-renamed").exists())

    def test_route_is_scoped_to_masterless_payloads(self):
        """
        The seam itself: master present in any form keeps a CREATE off the UPDATE path.

        The malformed forms matter as much as the well-formed one. A matcher
        interpolates the payload value straight into an ORM pk filter, where
        bool and non-integral float coerce SILENTLY (True -> 1, 7.5 -> 7)
        instead of declining, so they would select an unrelated row. Excluding
        master-bearing payloads keeps that filter out of reach rather than
        guarding it -- a seam pin, not a claim about row state: with bind-only
        in force the gate changes no row's contents, so this test is the only
        thing that fails if the gate is removed.
        """
        self.assertTrue(requires_pre_save_match("dcim.virtualchassis", {"name": "x"}))
        self.assertTrue(
            requires_pre_save_match("dcim.virtualchassis", {"name": "x", "master": None}),
            "explicit null must gate as masterless, like VirtualChassisNameMatcher",
        )
        for master in (self.owner.pk, str(self.owner.pk), True, False, 7.5, 7.0, "abc", [1]):
            with self.subTest(master=master):
                self.assertFalse(
                    requires_pre_save_match(
                        "dcim.virtualchassis", {"name": "x", "master": master}
                    )
                )

    def test_ungated_types_are_unaffected_by_the_scoping(self):
        """Only dcim.virtualchassis is payload-scoped; the other entries are not."""
        for object_type in ("dcim.macaddress", "dcim.cable", "ipam.prefix"):
            with self.subTest(object_type=object_type):
                self.assertTrue(requires_pre_save_match(object_type, {"master": 1}))
        self.assertFalse(requires_pre_save_match("dcim.site", {}))


class PreSaveMatchBindOnlyTests(TestCase):
    """
    The second seam: what a pre-save-matched CREATE may do to the row it found.

    requires_pre_save_match decides whether to LOOK; this decides whether to
    WRITE. They are separate questions because the find-first path serves two
    populations with opposite needs. An auto-created component was instantiated
    by NetBox from the very device or module the payload names, so the match is
    an identity and the payload is the authority that must overwrite the
    template's defaults. A dcim.virtualchassis match is by name alone against a
    model with no uniqueness on name, so it is a guess -- good enough to dedupe
    an insert, not good enough to write another source's row.
    """

    #: applier._is_auto_created_component's own list, asserted against it below.
    AUTO_CREATED = (
        "dcim.consoleport",
        "dcim.consoleserverport",
        "dcim.powerport",
        "dcim.poweroutlet",
        "dcim.interface",
        "dcim.rearport",
        "dcim.frontport",
        "dcim.modulebay",
        "dcim.devicebay",
        "dcim.inventoryitem",
    )

    def test_virtualchassis_binds_without_writing(self):
        """The one bind-only type, and the reason the seam exists at all."""
        self.assertTrue(pre_save_match_binds_only("dcim.virtualchassis"))

    def test_every_other_pre_save_matched_type_still_applies_its_payload(self):
        """
        Naming them all: this is a behavioural change nobody else may inherit.

        dcim.module's find-first, for one, exists precisely to APPLY a payload
        that the IntegrityError recovery it replaced used to discard.
        """
        for object_type in sorted(_REQUIRES_PRE_SAVE_MATCH - {"dcim.virtualchassis"}):
            with self.subTest(object_type=object_type):
                self.assertFalse(pre_save_match_binds_only(object_type))

    def test_no_auto_created_component_is_bind_only(self):
        """
        The overlap that would be silent: both populations share one dispatch.

        applier._try_pre_save_match routes auto-created components through this
        same seam, so a component type added to _PRE_SAVE_MATCH_BIND_ONLY would
        stop ingest overwriting template defaults with no test failing for that
        reason -- the row would still be bound and no duplicate created.
        """
        for object_type in self.AUTO_CREATED:
            with self.subTest(object_type=object_type):
                self.assertTrue(
                    _is_auto_created_component(object_type),
                    "the list under test has drifted from the applier's",
                )
                self.assertFalse(pre_save_match_binds_only(object_type))

    def test_a_type_with_no_pre_save_match_at_all_is_not_bind_only(self):
        """Bind-only narrows the find-first route; it is not a route of its own."""
        for object_type in ("dcim.site", "dcim.device", "dcim.rack"):
            with self.subTest(object_type=object_type):
                self.assertFalse(pre_save_match_binds_only(object_type))
                self.assertFalse(requires_pre_save_match(object_type, {}))


class VirtualChassisAdoptionTests(TestCase):
    """Master-bearing VC creates must adopt same-named masterless VCs."""

    @classmethod
    def setUpTestData(cls):
        """Seed a masterless VC (bulk member-first aftermath) and its master-to-be."""
        site = Site.objects.create(name="vca-site", slug="vca-site")
        mfr = Manufacturer.objects.create(name="vca-mfr", slug="vca-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vca-dt", slug="vca-dt")
        role = DeviceRole.objects.create(name="vca-role", slug="vca-role")
        cls.vc = VirtualChassis.objects.create(name="vca-stack")
        cls.master = Device.objects.create(
            name="vca-sw1", site=site, device_type=dt, role=role
        )

    def _apply_create(self, data, extra_changes=()):
        return _apply_vc_create(data, extra_changes=extra_changes)

    def test_master_bearing_create_adopts_masterless_vc(self):
        """No duplicate VC; adoption attaches the chassis-less master and sets it."""
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, self.vc.pk)
        self.assertEqual(self.master.vc_position, 1)  # NetBox's own choice for a VC master

    def test_adoption_sets_master_once_member(self):
        """When the master device already belongs to the VC, adoption sets master."""
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=self.vc, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)

    def test_master_already_owning_a_chassis_declines_adoption_of_a_decoy(self):
        """
        A same-named masterless decoy must not receive a CREATE aimed at the owned row.

        The decoy is the row the adopter's own queryset picks (same name,
        master__isnull=True), and the master named by the payload cannot be
        attached to it -- VirtualChassis.master is unique and another chassis
        already holds it. Deferring master and saving the rest, which is how
        the adopter treats a master that merely sits in another chassis, writes
        the payload onto a row the payload never referred to and leaves the
        real one untouched, with nothing to converge it. So the adopter must
        decline outright, and the CREATE settles on the unique-master row
        without rewriting it.

        Applying the payload is the plan path's job, not a create's: an ingest
        of this entity plans an UPDATE of the unique-master row, because
        find_existing_object resolves master-bearing payloads to it at plan
        time. A create that also rewrote it is what let a create rename a
        chassis it did not name.
        """
        mastered = VirtualChassis.objects.create(name="vca-mastered", master=self.master)
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=mastered, vc_position=1
        )
        decoy = VirtualChassis.objects.create(name="vca-mastered")  # masterless, same name
        mastered.refresh_from_db()
        before = mastered.last_updated

        self._apply_create({
            "name": "vca-mastered",
            "master": self.master.pk,
            "description": "must not land anywhere",
        })

        self.assertEqual(VirtualChassis.objects.filter(name="vca-mastered").count(), 2)
        decoy.refresh_from_db()
        self.assertEqual(decoy.description, "")
        self.assertIsNone(decoy.master)
        mastered.refresh_from_db()
        self.assertEqual(mastered.description, "")
        self.assertEqual(mastered.master_id, self.master.pk)
        self.assertEqual(mastered.last_updated, before, "the CREATE wrote the owned row")

    def test_adoption_reports_a_conflict_for_a_device_in_another_chassis(self):
        """
        A master already in ANOTHER chassis is a reported conflict, then converges.

        THE OLD EXPECTATION HERE WAS UNSAFE: it asserted that the apply
        SUCCEEDS with master silently dropped. Not relocating the device was and
        is right; answering 200 was not. The payload asked for
        VirtualChassis(name=X, master=Y) and got a chassis without a master,
        reported as applied -- and since a standalone chassis payload carries
        nothing else, an identical re-ingest re-plans the identical CREATE
        forever. A reconciler cannot tell that apart from success.

        The second half of the test is unchanged and is what makes the conflict
        a conflict rather than a wall: once something else (the device's own
        payload) makes the device a member, the identical apply binds master.
        """
        elsewhere = VirtualChassis.objects.create(name="vca-elsewhere")
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=elsewhere, vc_position=2
        )
        with self.assertRaises(ChangeSetException) as caught:
            self._apply_create({"name": "vca-stack", "master": self.master.pk})
        errors = caught.exception.errors["dcim.virtualchassis"]
        self.assertIn("master", errors)
        self.assertIn("vca-elsewhere", errors["master"][0])

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master)  # not forced
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, elsewhere.pk)  # not relocated
        self.assertEqual(self.master.vc_position, 2)

        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=self.vc, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)  # second pass converges

    def test_adoption_prefers_row_with_membership_over_oldest(self):
        """Among several same-named masterless rows, adopt the one the master already belongs to."""
        older = self.vc  # from setUpTestData: masterless, empty, lowest pk
        newer = VirtualChassis.objects.create(name="vca-stack")  # masterless, higher pk
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=newer, vc_position=1
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 2)
        newer.refresh_from_db()
        self.assertEqual(newer.master_id, self.master.pk)
        older.refresh_from_db()
        self.assertIsNone(older.master)

    def test_a_populated_row_matched_only_by_name_is_left_alone_and_a_new_one_created(self):
        """
        The measured hazard, at the adopter's own door, and the answer to it.

        The candidate holds another device and does not hold the requested
        master, so binding it would designate this payload's device the master
        of somebody else's stack and drag it in. Nothing in the payload says
        that row is meant -- and nothing in the changeset asks for the
        membership either, because a standalone dcim.virtualchassis entity plans
        no device change at all. So adoption declines.

        Declining is not refusing. The payload's own CREATE is still applied, on
        a row of its own, which is the only answer that both leaves the foreign
        row alone AND converges: a refusal here repeats on every identical pass,
        and the entity that provokes it (orb-agent's standalone virtual_chassis
        entity) carries nothing that could be changed to satisfy it.
        """
        squatter = Device.objects.create(
            name="vca-squatter", site=self.master.site,
            device_type=self.master.device_type, role=self.master.role,
        )
        Device.objects.filter(pk=squatter.pk).update(
            virtual_chassis=self.vc, vc_position=4)
        self.vc.refresh_from_db()
        before = self.vc.last_updated

        self._apply_create({"name": "vca-stack", "master": self.master.pk})

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id, "the foreign row was mastered")
        self.assertEqual(self.vc.last_updated, before, "the foreign row was written")
        self.assertEqual(list(self.vc.members.values_list("name", flat=True)),
                         ["vca-squatter"], "a device was moved into the foreign row")

        mine = VirtualChassis.objects.exclude(pk=self.vc.pk).get(name="vca-stack")
        self.assertEqual(mine.master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, mine.pk)
        self.assertEqual(self.master.vc_position, 1)

    def test_several_masterless_rows_sharing_the_name_are_neither_adopted_nor_written(self):
        """
        Two empty candidates are indistinguishable, so neither may be chosen.

        A single empty row IS adopted (test_master_bearing_create_adopts_
        masterless_vc): nothing is relocated and no duplicate is left. Two of
        them are a different question -- picking either is creation order
        wearing a disguise. What follows from "cannot choose" is create, not
        refuse: the payload gets its own row, both duplicates are left exactly
        as they were, and the operator can still delete them. A refusal would
        have blocked the ingest on a mess the producer cannot clean up.
        """
        second = VirtualChassis.objects.create(name="vca-stack")  # a second empty duplicate
        self._apply_create({"name": "vca-stack", "master": self.master.pk})

        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 3)
        for row in (self.vc, second):
            row.refresh_from_db()
            self.assertIsNone(row.master_id)
            self.assertEqual(row.members.count(), 0)
        mine = VirtualChassis.objects.get(name="vca-stack", master=self.master)
        self.assertNotIn(mine.pk, {self.vc.pk, second.pk})

    def test_two_rows_sharing_the_asserted_domain_are_left_alone_and_a_third_created(self):
        """
        A discriminator that narrows to TWO rows identifies neither.

        Both masterless rows carry domain "vca-twin" and the payload asserts it,
        so narrowing keeps both and rule 2 does not fire (it needs exactly one).
        The old behaviour raised here and told the operator to "supply domain" --
        the value they had just supplied. There is nothing to add to this
        payload, so there is nothing to refuse it for: the chassis it describes
        is created, both twins are left byte-identical, and the duplicate names
        stay visible in NetBox where they can be merged.
        """
        VirtualChassis.objects.filter(pk=self.vc.pk).update(domain="vca-twin")
        twin = VirtualChassis.objects.create(name="vca-stack", domain="vca-twin")
        self.vc.refresh_from_db()
        before = self.vc.last_updated

        self._apply_create(
            {"name": "vca-stack", "domain": "vca-twin", "master": self.master.pk})

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id)
        self.assertEqual(self.vc.last_updated, before)
        twin.refresh_from_db()
        self.assertIsNone(twin.master_id)
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 3)
        mine = VirtualChassis.objects.get(name="vca-stack", master=self.master)
        self.assertEqual(mine.domain, "vca-twin")

    def test_adoption_accepts_a_domain_identified_populated_row(self):
        """A domain turns the refusal above into an identification, and it adopts."""
        squatter = Device.objects.create(
            name="vca-squatter2", site=self.master.site,
            device_type=self.master.device_type, role=self.master.role,
        )
        VirtualChassis.objects.filter(pk=self.vc.pk).update(domain="vca-dom")
        Device.objects.filter(pk=squatter.pk).update(
            virtual_chassis=self.vc, vc_position=4)

        self._apply_create({
            "name": "vca-stack", "domain": "vca-dom", "master": self.master.pk})
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, self.vc.pk)
        self.assertEqual(VirtualChassis.objects.filter(name="vca-stack").count(), 1)

    def test_adoption_declines_a_populated_row_a_planned_device_change_names(self):
        """
        A planned Device change says WHICH CHASSIS, never WHICH ROW, so it licenses nothing.

        This cell used to adopt. The Device change asserts
        virtual_chassis = <the CREATE's own ref>, which is the row the preview
        names, and asserts nothing about the pre-existing row that adoption
        would redirect that create onto. Within the member-first shape it
        therefore could not tell this producer's own earlier pass from another
        producer's identically named stack, and it took the row in both cases.

        Now it declines: the row nobody identified is left exactly as it was and
        the payload gets its own. The cost is real and is not hidden -- a
        name-only member-first producer no longer converges by itself, and
        accumulates a duplicate until an operator merges the rows or the
        producer starts sending identity. The gain is that a populated stack
        nobody identified is never written, and a duplicate is visible where a
        silent merge of two stacks is not.
        """
        squatter = Device.objects.create(
            name="vca-squatter3", site=self.master.site,
            device_type=self.master.device_type, role=self.master.role,
        )
        Device.objects.filter(pk=squatter.pk).update(
            virtual_chassis=self.vc, vc_position=4)

        self._apply_create(
            {"name": "vca-stack", "master": self.master.pk},
            extra_changes=[Change(
                change_type=ChangeType.UPDATE,
                object_type="dcim.device",
                object_id=self.master.pk,
                data={"virtual_chassis": "vc1", "vc_position": 1},
                new_refs=["virtual_chassis"],
            )],
        )

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id, "adopted a row nothing identified")
        self.assertEqual(
            {d.pk for d in self.vc.members.all()}, {squatter.pk},
            "the untouched row gained or lost a member",
        )
        own = VirtualChassis.objects.filter(name="vca-stack").exclude(pk=self.vc.pk)
        self.assertEqual(own.count(), 1, "the payload did not get its own row")
        self.assertEqual(own.first().master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, own.first().pk)
    def test_an_empty_domain_payload_creates_its_own_row_and_keeps_the_labelled_one(self):
        """
        Adoption declines where the matcher binds, and the asymmetry is the point.

        The single same-named masterless row carries domain "vca-dom" and the
        payload asserts "": an assertion that contradicts the row. Dropping it
        (the old behaviour) adopted that row and wrote "" over its domain --
        a discriminator destroyed by a payload it should have excluded. Adoption
        has a lossless alternative the matcher does not have, namely the CREATE
        its own plan already asked for, so it takes it.
        """
        VirtualChassis.objects.filter(pk=self.vc.pk).update(domain="vca-dom")
        self.vc.refresh_from_db()
        before = self.vc.last_updated

        self._apply_create(
            {"name": "vca-stack", "domain": "", "master": self.master.pk})

        self.vc.refresh_from_db()
        self.assertEqual(self.vc.domain, "vca-dom", "the payload's '' was written over it")
        self.assertIsNone(self.vc.master_id)
        self.assertEqual(self.vc.last_updated, before)
        fresh = VirtualChassis.objects.exclude(pk=self.vc.pk).get(name="vca-stack")
        self.assertEqual(fresh.domain, "")
        self.assertEqual(fresh.master_id, self.master.pk)

    def test_a_cross_site_member_first_row_is_declined_but_never_refused(self):
        """
        A VirtualChassis legitimately spans sites, so member sites are not identity.

        The device already in the row lives at another site. That fact was
        briefly a veto -- refuse to adopt a row holding members from outside the
        master's site -- and the veto had to be reverted because it made this
        apply answer 400 on every pass and never converge. This test still pins
        that half: whatever else happens here, it must not be an error, and it
        must not depend on anybody's site.

        The other half changed. This row is populated and identified by nothing
        but its name, so it is no longer adopted either -- the payload gets its
        own row. Declining is not refusing: the apply succeeds, the cross-site
        row is untouched, and nothing is permanent. The honest fix for the
        convergence this gives up is source-owned VirtualChassis identity, not
        a guess about sites.
        """
        other_site = Site.objects.create(name="vca-site-b", slug="vca-site-b")
        stranger = Device.objects.create(
            name="vca-elsewhere", site=other_site,
            device_type=self.master.device_type, role=self.master.role,
        )
        Device.objects.filter(pk=stranger.pk).update(
            virtual_chassis=self.vc, vc_position=4)

        self._apply_create(
            {"name": "vca-stack", "master": self.master.pk},
            extra_changes=[Change(
                change_type=ChangeType.UPDATE,
                object_type="dcim.device",
                object_id=self.master.pk,
                data={"virtual_chassis": "vc1", "vc_position": 1},
                new_refs=["virtual_chassis"],
            )],
        )

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id)
        self.assertEqual({d.pk for d in self.vc.members.all()}, {stranger.pk})
        own = VirtualChassis.objects.filter(name="vca-stack").exclude(pk=self.vc.pk)
        self.assertEqual(own.count(), 1)
        self.assertEqual(own.first().master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, own.first().pk)
    def test_an_empty_domain_is_not_an_identification_that_licenses_adoption(self):
        """
        "" narrows, and must not IDENTIFY -- tested at the door where it survives.

        apply-change-set applies changes nobody planned, so a domain of ""
        reaches _choose_adoption_candidate here, where the differ drops it from
        a planned CREATE (test_an_empty_domain_in_the_payload_is_not_a_licence_
        to_join measures that). The row carries no domain either, so "" is
        consistent with it and narrows to it -- and if that counted as an
        IDENTIFICATION it would collect rule 2's permission, which is the one
        rule that adopts a POPULATED row with no companion device change at all.
        Every chassis that never set a domain carries "", so it tells no two rows
        apart and identifies none: this standalone payload gets its own row.
        """
        squatter = Device.objects.create(
            name="vca-empty-dom", site=self.master.site,
            device_type=self.master.device_type, role=self.master.role,
        )
        Device.objects.filter(pk=squatter.pk).update(
            virtual_chassis=self.vc, vc_position=4)
        self.vc.refresh_from_db()
        before = self.vc.last_updated

        self._apply_create(
            {"name": "vca-stack", "domain": "", "master": self.master.pk})

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id, "'' identified the row and adopted it")
        self.assertEqual(self.vc.last_updated, before)
        self.assertEqual(list(self.vc.members.values_list("name", flat=True)),
                         ["vca-empty-dom"])
        mine = VirtualChassis.objects.exclude(pk=self.vc.pk).get(name="vca-stack")
        self.assertEqual(mine.master_id, self.master.pk)

    def test_a_row_whose_domain_the_payload_never_asserts_is_left_for_its_owner(self):
        """
        Somebody labelled that stack and this payload never mentions the label.

        The row carries domain "vca-dom"; the payload asserts no domain at all,
        so name plus a planned device change is all it has -- and a row bearing a
        discriminator the payload is silent about is a row the payload has not
        identified. Contrast test_adoption_accepts_a_domain_identified_populated_
        row, where the same value IS asserted and the same row is adopted.

        This steers between adopting and creating; it never rejects the payload.
        The labelled row keeps its label, its master slot and its members, and
        the payload lands on a row of its own.
        """
        labelled = Device.objects.create(
            name="vca-labelled", site=self.master.site,
            device_type=self.master.device_type, role=self.master.role,
        )
        VirtualChassis.objects.filter(pk=self.vc.pk).update(domain="vca-dom")
        Device.objects.filter(pk=labelled.pk).update(
            virtual_chassis=self.vc, vc_position=4)
        self.vc.refresh_from_db()
        before = self.vc.last_updated

        self._apply_create(
            {"name": "vca-stack", "master": self.master.pk},
            extra_changes=[Change(
                change_type=ChangeType.UPDATE,
                object_type="dcim.device",
                object_id=self.master.pk,
                data={"virtual_chassis": "vc1", "vc_position": 1},
                new_refs=["virtual_chassis"],
            )],
        )

        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master_id)
        self.assertEqual(self.vc.domain, "vca-dom")
        self.assertEqual(self.vc.last_updated, before)
        self.assertEqual(list(self.vc.members.values_list("name", flat=True)),
                         ["vca-labelled"])
        mine = VirtualChassis.objects.exclude(pk=self.vc.pk).get(name="vca-stack")
        self.assertEqual(mine.domain, "")
        self.assertEqual(mine.master_id, self.master.pk)
        self.master.refresh_from_db()
        self.assertEqual(self.master.virtual_chassis_id, mine.pk)
