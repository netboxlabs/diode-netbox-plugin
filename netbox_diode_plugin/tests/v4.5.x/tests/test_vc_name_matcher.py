"""Unit tests for masterless VirtualChassis name matching."""
from types import SimpleNamespace

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import TestCase

from netbox_diode_plugin.api.applier import _is_auto_created_component, apply_changeset
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType
from netbox_diode_plugin.api.matcher import (
    _REQUIRES_PRE_SAVE_MATCH,
    find_existing_object,
    fingerprints,
    get_model_matchers,
    pre_save_match_binds_only,
    requires_pre_save_match,
)


def _apply_vc_create(data):
    """Apply a single dcim.virtualchassis CREATE, exactly as a changeset would."""
    cs = ChangeSet(changes=[Change(
        change_type=ChangeType.CREATE,
        object_type="dcim.virtualchassis",
        ref_id="vc1",
        data=data,
    )])
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
        found = find_existing_object({"name": "vcm-stack"}, "dcim.virtualchassis")
        self.assertEqual(found, self.vc_old)  # oldest pk wins over the newer duplicate

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


class VirtualChassisPreSaveMatchScopeTests(TestCase):
    """
    What the find-first CREATE route does, and to which payloads it applies.

    The route is an UPDATE path -- it applies a CREATE's payload to whatever
    find_existing_object returns -- so its scope is a behavioural contract with
    two halves, and both are asserted here through a real apply. (The test this
    replaced asserted only that "dcim.virtualchassis" was a member of the set
    literal that defines the route, which is true by construction and cannot
    fail for any reason a reader would care about.)
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

    def _apply_create(self, data):
        return _apply_vc_create(data)

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

    def test_adoption_defers_master_for_a_device_in_another_chassis(self):
        """
        A master already in ANOTHER chassis is not relocated, so master stays unset.

        This is the only case adoption still defers, and the only one where
        deferring converges: membership is the DEVICE payload's to assert, and
        once something else makes the device a member an identical re-apply
        binds master. (A chassis-less master is attached by adoption itself --
        nothing else would ever do it for a standalone VC payload.)
        """
        elsewhere = VirtualChassis.objects.create(name="vca-elsewhere")
        Device.objects.filter(pk=self.master.pk).update(
            virtual_chassis=elsewhere, vc_position=2
        )
        self._apply_create({"name": "vca-stack", "master": self.master.pk})
        self.vc.refresh_from_db()
        self.assertIsNone(self.vc.master)  # deferred, not forced
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
