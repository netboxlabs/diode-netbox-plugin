"""Two same-named VirtualChassis nodes in ONE entity graph: merged, or kept apart."""
from itertools import permutations
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers
from utilities.testing import APITestCase

from netbox_diode_plugin.api import transformer
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    asserted_vc_identity,
    partition_vc_identities,
    vc_identities_conflict,
)
from netbox_diode_plugin.plugin_config import get_diode_user


class VirtualChassisIdentityPartitionTests(APITestCase):
    """
    Same name, one graph: what the payload asserts decides one chassis or two.

    VirtualChassisNameMatcher.fingerprint is keyed on the name ALONE, and
    deliberately, so that a member's name-only chassis node and the
    master-bearing one merge into a single create (issue #183). The cost, not
    measured until this file existed, is that two same-named nodes asserting
    DIFFERENT identity merged too -- and _merge_nodes then rejected the whole
    entity, identically on every retry, so the payload could never be ingested
    at all.

    Scope, measured rather than assumed: it only ever happened INSIDE one entity
    graph. Separate entities of one bulk request are transformed separately and
    were never affected (test_separate_entities_were_never_affected).

    The cross-device graphs here are built with a CABLE between two switches,
    which is how two unrelated stacks legitimately end up in one entity graph
    and is the one device-to-device edge NetBox does not second-guess. (A device
    reaching another through primary_ip4 plans fine and then fails NetBox's own
    "the specified IP address is not assigned to this device" on apply, so it
    cannot show what happens after the plan.)
    """

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
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

        self.site = Site.objects.create(name="fp-site", slug="fp-site")
        mfr = Manufacturer.objects.create(name="fp-mfr", slug="fp-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="fp-dt", slug="fp-dt")
        self.role = DeviceRole.objects.create(name="fp-role", slug="fp-role")

    # ---- payload builders -------------------------------------------------

    def _device(self, name, **extra):
        """A complete dcim.device entity, so every nested device can be created."""
        return dict({
            "name": name,
            "site": {"name": "fp-site"},
            "role": {"name": "fp-role"},
            "device_type": {"manufacturer": {"name": "fp-mfr"}, "model": "fp-dt"},
        }, **extra)

    def _cabled(self, a, b, iface="Gi0/1"):
        """One dcim.cable entity whose two ends land on two different devices."""
        def termination(device):
            return [{"object_interface": {
                "device": device, "name": iface, "type": "1000base-t",
            }}]

        return {"timestamp": 1, "object_type": "dcim.cable", "entity": {"cable": {
            "a_terminations": termination(a),
            "b_terminations": termination(b),
            "status": "connected", "type": "cat6",
        }}}

    def _seed_labelled_stack(self, name, domain, member):
        """ORM-seed a converged one-member stack carrying a domain."""
        vc = VirtualChassis.objects.create(name=name, domain=domain)
        device = Device.objects.create(
            name=member, site=self.site, device_type=self.dt, role=self.role
        )
        Device.objects.filter(pk=device.pk).update(virtual_chassis=vc, vc_position=2)
        vc.refresh_from_db()
        vc.master = device
        vc.save()
        return vc

    # ---- helpers ----------------------------------------------------------

    def _plan(self, payload):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["change_set"]

    def _apply(self, change_set):
        r = self.client.post(self.apply_url, data=change_set, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"))

    def _refused(self, payload):
        """The whole entity rejected, with the merge conflict as the reason."""
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        return str(r.json()["errors"])

    def _vc_changes(self, change_set):
        return [c for c in change_set["changes"]
                if c["object_type"] == "dcim.virtualchassis"]

    def _non_noop(self, payload):
        return [c for c in self._plan(payload)["changes"] if c["change_type"] != "noop"]

    def _assert_noop_rediff(self, payload):
        self.assertEqual(self._non_noop(payload), [])

    # ---- the payload that could never be ingested -------------------------

    def test_different_masters_in_one_graph_plan_two_chassis_and_apply(self):
        """
        The strong case: two switches in different stacks that share a chassis name.

        VirtualChassis.master is a DB UNIQUE constraint, so two nodes naming
        DIFFERENT masters cannot be one row: they are two stacks, and since
        VirtualChassis.name is not unique in NetBox, two rows is a state NetBox
        permits and an operator can already be in. Merging them produced
        "Conflicting values for 'master' merging duplicate dcim.virtualchassis"
        and rejected the entity -- cable, interfaces, devices and all -- on
        every attempt.

        Measured here end to end: two creates planned, applied, each member in
        the stack its OWN reference named, and an empty re-diff. A merge would
        have put both members in one stack; a fingerprint keyed on master
        instead of name would have split the intended merge below.
        """
        near = self._device("fpm-d1", vc_position=2, virtual_chassis={
            "name": "fpm-stack", "master": self._device("fpm-a1"),
        })
        far = self._device("fpm-d2", vc_position=2, virtual_chassis={
            "name": "fpm-stack", "master": self._device("fpm-b1"),
        })
        payload = self._cabled(near, far)

        cs = self._plan(payload)
        chassis = self._vc_changes(cs)
        self.assertEqual([c["change_type"] for c in chassis], ["create", "create"], cs)
        self.assertEqual({c["data"]["name"] for c in chassis}, {"fpm-stack"})
        self._apply(cs)

        rows = VirtualChassis.objects.filter(name="fpm-stack")
        self.assertEqual(rows.count(), 2)
        by_master = {row.master.name: row for row in rows}
        self.assertEqual(set(by_master), {"fpm-a1", "fpm-b1"},
                         "the two stacks did not keep their own masters")
        self.assertEqual(
            {d.name for d in by_master["fpm-a1"].members.all()}, {"fpm-a1", "fpm-d1"})
        self.assertEqual(
            {d.name for d in by_master["fpm-b1"].members.all()}, {"fpm-b1", "fpm-d2"})
        self._assert_noop_rediff(payload)

    def test_two_labelled_stacks_that_exist_are_each_matched_by_their_domain(self):
        """
        Same name, different domains, and both rows already in NetBox: two noops.

        This is the reading the fix takes on domain, and the measurement that
        chose it. matcher._VC_DISCRIMINATORS already commits to a domain telling
        same-named chassis APART -- narrow_vc_candidates uses exactly these two
        rows to resolve exactly this reference. Merging the nodes first denied
        the matcher the chance: the entity was rejected with "Conflicting values
        for 'domain'" before any lookup ran, even though NetBox held an
        unambiguous answer for each node. Treating the two domains as ONE
        contradictory description would have kept it that way, and would leave
        the plugin with two notions of VC identity that disagree.
        """
        row_a = self._seed_labelled_stack("fpl-stack", "building-a", "fpl-a1")
        row_b = self._seed_labelled_stack("fpl-stack", "building-b", "fpl-b1")
        near = self._device("fpl-a1", vc_position=2, virtual_chassis={
            "name": "fpl-stack", "domain": "building-a",
        })
        far = self._device("fpl-b1", vc_position=2, virtual_chassis={
            "name": "fpl-stack", "domain": "building-b",
        })
        payload = self._cabled(near, far, iface="Gi0/2")

        cs = self._plan(payload)
        planned = {c["object_id"] for c in self._vc_changes(cs)}
        self.assertEqual(planned, {row_a.pk, row_b.pk},
                         "each node must match its own row")
        self.assertEqual(
            [c["change_type"] for c in self._vc_changes(cs)], ["noop", "noop"], cs)
        self._apply(cs)
        self._assert_noop_rediff(payload)
        for row, domain, member in ((row_a, "building-a", "fpl-a1"),
                                    (row_b, "building-b", "fpl-b1")):
            row.refresh_from_db()
            self.assertEqual(row.domain, domain)
            self.assertEqual([d.name for d in row.members.all()], [member])

    def test_the_reported_domain_payload_is_rejected_not_half_applied(self):
        """
        The reported repro: planned in full, then refused, and nothing written.

        Shape: a member names its chassis with a domain and a master, and the
        MASTER's own chassis reference names the same chassis with a DIFFERENT
        domain. The partition does its job -- two nodes, two groups, two creates
        previewed, where the whole entity used to be rejected at the merge.

        The apply then refuses, and it should. The payload asserts both "fpd-m1
        masters the building-a stack" and "fpd-m1 belongs to the building-b
        stack of the same name", and a device is in one chassis at a time, so
        there is no state that satisfies it. Contradictory input may be
        rejected; what may NOT happen -- and what happened here before -- is a
        200 that reports success while re-planning the same domain change on
        every later pass, forever. That is the whole of the second requirement
        this change exists to meet: a discriminator is identity, so a payload
        contradicting the row it names is told so instead of being applied to it
        and quietly re-proposed.

        Nothing is left behind: the apply is one transaction, so the two rows it
        planned are rolled back and a producer that fixes its payload starts
        clean.

        The rough edge, recorded rather than hidden: the message is NetBox's own
        constraint ("currently designated as its master") rather than a
        statement about the identity contradiction, so it names a symptom and
        not the cause. A structured per-entity conflict naming both domains
        would be the better error; this test pins the refusal, not the wording.
        """
        payload = {"timestamp": 1, "object_type": "dcim.device", "entity": {"device":
            self._device("fpd-d1", vc_position=2, virtual_chassis={
                "name": "fpd-stack",
                "domain": "building-a",
                "master": self._device("fpd-m1", virtual_chassis={
                    "name": "fpd-stack", "domain": "building-b",
                }),
            })}}

        cs = self._plan(payload)
        self.assertEqual([c["change_type"] for c in self._vc_changes(cs)],
                         ["create", "create"], cs)

        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("master", str(r.json().get("errors")).lower())
        self.assertEqual(
            VirtualChassis.objects.filter(name="fpd-stack").count(), 0,
            "a refused apply left rows behind",
        )

    def test_two_domains_arriving_in_sequence_end_as_two_stable_rows(self):
        """
        A then B, in separate requests, and BOTH plan nothing afterwards.

        This is the sequential case the domain contract has to answer, and the
        one the old rule-0 exemption got wrong. Step 1 ingests fps-stack /
        building-a. Step 2 ingests the same NAME with building-b. A non-empty
        domain is identity, so step 2 does not bind step 1's row on the strength
        of the name; it describes a chassis that does not exist yet and gets it.

        Step 3 is the part that used to fail: re-ingesting either payload plans
        NOTHING. Before, step 2 bound the single existing row and then proposed
        `domain: building-b` on it again on every pass -- a successful apply that
        never converged, which is not a contract worth having. Two rows, each
        holding its own domain and its own master, is.
        """
        def stack(device, domain):
            return {"timestamp": 1, "object_type": "dcim.device", "entity": {"device":
                self._device(device, vc_position=1, virtual_chassis={
                    "name": "fps-stack",
                    "domain": domain,
                    "master": self._device(device),
                })}}

        first, second = stack("fps-a1", "building-a"), stack("fps-b1", "building-b")
        for payload in (first, second):
            self._apply(self._plan(payload))

        rows = VirtualChassis.objects.filter(name="fps-stack")
        self.assertEqual(rows.count(), 2, list(rows.values_list("pk", "domain")))
        self.assertEqual({r.domain for r in rows}, {"building-a", "building-b"})
        self.assertEqual({r.master.name for r in rows}, {"fps-a1", "fps-b1"})
        for row in rows:
            self.assertEqual([d.name for d in row.members.all()], [row.master.name])

        self._assert_noop_rediff(first)
        self._assert_noop_rediff(second)

    # ---- what must keep merging -------------------------------------------

    def test_the_name_only_node_still_merges_into_the_master_bearing_one(self):
        """Issue #183's merge, inside one graph: one chassis, not a split one."""
        payload = {"timestamp": 1, "object_type": "dcim.device", "entity": {"device":
            self._device("fpi-d1", vc_position=2, virtual_chassis={
                "name": "fpi-stack",
                "master": self._device("fpi-m1", virtual_chassis={"name": "fpi-stack"}),
            })}}

        cs = self._plan(payload)
        self.assertEqual(len(self._vc_changes(cs)), 1, cs)
        self._apply(cs)
        rows = VirtualChassis.objects.filter(name="fpi-stack")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().master.name, "fpi-m1")
        self._assert_noop_rediff(payload)

    def test_the_same_master_stub_twice_still_plans_one_chassis(self):
        """
        The orb shape: every reference carries the SAME master stub, one create.

        This is what forces the partition to compare CANONICAL references
        (transformer._canonical_uuids). One device mentioned twice in a graph is
        two nodes with two uuids until dedupe merges them, so comparing the
        references as they arrive would read two identical stubs as two
        different masters and split the very chassis this branch exists to keep
        whole.
        """
        stub = self._device("fpo-m1")
        near = self._device("fpo-d1", vc_position=2, virtual_chassis={
            "name": "fpo-stack", "master": dict(stub),
        })
        far = self._device("fpo-d2", vc_position=3, virtual_chassis={
            "name": "fpo-stack", "master": dict(stub),
        })
        payload = self._cabled(near, far, iface="Gi0/3")

        cs = self._plan(payload)
        self.assertEqual(len(self._vc_changes(cs)), 1, cs)
        self._apply(cs)
        rows = VirtualChassis.objects.filter(name="fpo-stack")
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.master.name, "fpo-m1")
        self.assertEqual(
            {d.name for d in row.members.all()}, {"fpo-m1", "fpo-d1", "fpo-d2"},
            "the one stack lost a member",
        )
        self._assert_noop_rediff(payload)

    # ---- conflicts that are still conflicts -------------------------------

    def test_one_master_and_two_domains_is_a_conflict_not_two_rows(self):
        """
        The unique key outranks the discriminator, in BOTH directions.

        Two nodes naming the SAME master are one row -- the constraint says so
        -- so their disagreement about domain is a field conflict to report,
        not licence to plan a second row whose insert could not succeed.
        """
        stub = self._device("fpc-m1")
        near = self._device("fpc-d1", vc_position=2, virtual_chassis={
            "name": "fpc-stack", "domain": "building-a", "master": dict(stub),
        })
        far = self._device("fpc-d2", vc_position=3, virtual_chassis={
            "name": "fpc-stack", "domain": "building-b", "master": dict(stub),
        })

        errors = self._refused(self._cabled(near, far, iface="Gi0/4"))
        self.assertIn("Conflicting values for 'domain'", errors)
        self.assertEqual(VirtualChassis.objects.count(), 0)

    def test_a_non_identity_field_disagreement_is_still_a_conflict(self):
        """
        The control: the partition splits on IDENTITY, never on disagreement.

        Neither node asserts a master or a domain, so both describe the one
        unidentified chassis, and their descriptions genuinely disagree. That is
        two sources contradicting each other about real data, which nothing can
        discard -- it must stay a reported conflict rather than quietly becoming
        two rows.
        """
        near = self._device("fpn-d1", vc_position=2, virtual_chassis={
            "name": "fpn-stack", "description": "from the near device",
        })
        far = self._device("fpn-d2", vc_position=3, virtual_chassis={
            "name": "fpn-stack", "description": "from the far device",
        })

        errors = self._refused(self._cabled(near, far, iface="Gi0/5"))
        self.assertIn("Conflicting values for 'description'", errors)
        self.assertEqual(VirtualChassis.objects.count(), 0)

    # ---- the bound of the finding ------------------------------------------

    def test_separate_entities_were_never_affected(self):
        """
        Two entities, same chassis name, different masters: always planned fine.

        Each entity of a bulk request is transformed on its own, so these two
        nodes never met in one graph and neither plan ever saw a conflict. It is
        pinned so the fix is not read as having rescued this, and so the
        single-entity and bulk paths cannot silently diverge.
        """
        bulk = {"entities": [
            {"id": "a", "object_type": "dcim.device", "entity": {"device": self._device(
                "fps-d1", vc_position=2, virtual_chassis={
                    "name": "fps-stack", "master": self._device("fps-a1")})}},
            {"id": "b", "object_type": "dcim.device", "entity": {"device": self._device(
                "fps-d2", vc_position=2, virtual_chassis={
                    "name": "fps-stack", "master": self._device("fps-b1")})}},
        ]}
        r = self.client.post(self.bulk_plan_apply_url, data=bulk, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        for result in r.json()["results"]:
            self.assertIsNone(result.get("errors"), result)
        self.assertEqual(
            {row.master.name for row in VirtualChassis.objects.filter(name="fps-stack")},
            {"fps-a1", "fps-b1"},
        )


class VCIdentityPartitionRuleTests(SimpleTestCase):
    """
    The partitioning rule itself, as a table. No database, no graph.

    Written against payload dicts rather than identity dicts so
    asserted_vc_identity's own reading of "asserts" is covered too: an explicit
    ``domain: ""`` is a claim, and ``master: None`` -- what the transformer
    emits for a member-only payload -- is not.
    """

    M1 = UnresolvedReference("dcim.device", "u-m1")
    M2 = UnresolvedReference("dcim.device", "u-m2")

    def _partition(self, *payloads):
        return partition_vc_identities([asserted_vc_identity(p) for p in payloads])

    def test_the_rule_table(self):
        """One row per shape, each with the reason it answers what it answers."""
        cases = [
            (
                "a node asserting nothing joins the one asserting group (issue #183)",
                [{"name": "s"}, {"name": "s", "master": self.M1}],
                [0, 0],
            ),
            (
                "an explicit master null asserts nothing, so it joins too",
                [{"name": "s", "master": None}, {"name": "s", "master": self.M1}],
                [0, 0],
            ),
            (
                "different masters are different stacks: master is a unique key",
                [{"name": "s", "master": self.M1}, {"name": "s", "master": self.M2}],
                [0, 1],
            ),
            (
                "the same master is one stack however many nodes name it",
                [{"name": "s", "master": self.M1}, {"name": "s", "master": self.M1},
                 {"name": "s"}],
                [0, 0, 0],
            ),
            (
                "one master and two domains is ONE group: the unique key outranks "
                "the discriminator, and the domains are then a field conflict",
                [{"name": "s", "master": self.M1, "domain": "a"},
                 {"name": "s", "master": self.M1, "domain": "b"}],
                [0, 0],
            ),
            (
                "asserting nothing with SEVERAL groups to choose from stays its own: "
                "guessing would put a member device in a stack nothing identified",
                [{"name": "s", "master": self.M1}, {"name": "s", "master": self.M2},
                 {"name": "s"}],
                [0, 1, 2],
            ),
            (
                "...but two such nodes asserting the SAME nothing stay together, so "
                "one unidentifiable chassis named twice is still planned once",
                [{"name": "s", "master": self.M1}, {"name": "s", "master": self.M2},
                 {"name": "s"}, {"name": "s"}],
                [0, 1, 2, 2],
            ),
            (
                "a domain places a masterless node onto the one stack that matches",
                [{"name": "s", "master": self.M1, "domain": "a"},
                 {"name": "s", "master": self.M2, "domain": "b"},
                 {"name": "s", "domain": "b"}, {"name": "s", "domain": "a"}],
                [0, 1, 1, 0],
            ),
            (
                "a domain no group carries places nothing, and stands alone",
                [{"name": "s", "master": self.M1, "domain": "a"},
                 {"name": "s", "master": self.M2, "domain": "b"},
                 {"name": "s", "domain": "c"}],
                [0, 1, 2],
            ),
            (
                "with no master anywhere the domains seed the groups themselves",
                [{"name": "s", "domain": "a"}, {"name": "s", "domain": "b"},
                 {"name": "s", "domain": "a"}],
                [0, 1, 0],
            ),
            (
                "one domain and a silent node, no master: one stack, that somebody "
                "labelled",
                [{"name": "s", "domain": "a"}, {"name": "s"}],
                [0, 0],
            ),
            (
                "an explicitly EMPTY domain is an assertion like any other",
                [{"name": "s", "domain": ""}, {"name": "s", "domain": "a"}],
                [0, 1],
            ),
            (
                "nodes asserting nothing at all are one stack, always",
                [{"name": "s"}, {"name": "s"}, {"name": "s"}],
                [0, 0, 0],
            ),
            (
                "a field a group's own members contradict tells nothing apart any "
                "more, so it cannot attract a node by whichever value came first",
                [{"name": "s", "master": self.M1, "domain": "a"},
                 {"name": "s", "master": self.M1, "domain": "b"},
                 {"name": "s", "domain": "a"}],
                [0, 0, 1],
            ),
        ]
        for reason, payloads, expected in cases:
            with self.subTest(reason):
                self.assertEqual(self._partition(*payloads), expected, reason)

    def test_the_partition_does_not_depend_on_arrival_order(self):
        """
        Every ordering of the same nodes groups them the same way.

        Order independence is why this is a bucket-wide partition resolved in
        one go rather than a first-fit merge taken as each duplicate arrives:
        which stack a member device ends up in must not depend on which
        reference the producer happened to emit first. All 24 orderings are
        checked, not just the reverse, because first-fit only misbehaves for
        SOME of them.
        """
        payloads = [
            {"name": "s", "master": self.M1, "domain": "a"},
            {"name": "s", "master": self.M2, "domain": "b"},
            {"name": "s", "domain": "b"},
            {"name": "s"},
        ]

        def grouping(order):
            """Who is with whom, in ORIGINAL positions, group numbering dropped."""
            groups = self._partition(*(payloads[i] for i in order))
            return sorted(
                sorted(order[i] for i, g in enumerate(groups) if g == group)
                for group in set(groups)
            )

        # 0 is a stack, 1 is another, 2's domain places it on 1, and 3 identifies
        # neither so it stands alone.
        self.assertEqual(grouping([0, 1, 2, 3]), [[0], [1, 2], [3]])
        for order in permutations(range(len(payloads))):
            with self.subTest(order=order):
                self.assertEqual(grouping(list(order)), [[0], [1, 2], [3]])

    def test_two_different_claims_on_one_silent_group_are_both_refused(self):
        """
        The price of order independence, stated rather than discovered later.

        One group, asserting only its master, and two nodes claiming different
        domains: nothing says which of them is that stack, so both stand alone.
        Placing whichever arrived first is precisely the order-dependence the
        one-pass placement exists to avoid.
        """
        self.assertEqual(
            self._partition(
                {"name": "s", "master": self.M1},
                {"name": "s", "domain": "a"},
                {"name": "s", "domain": "b"},
            ),
            [0, 1, 2],
        )
        # A silent node alongside them is still placed: it contradicts neither,
        # so the refusal above is about the contradiction, not about company.
        self.assertEqual(
            self._partition(
                {"name": "s", "master": self.M1},
                {"name": "s", "domain": "a"},
                {"name": "s"},
            ),
            [0, 0, 0],
        )

    def test_conflict_is_symmetric_and_silence_conflicts_with_nothing(self):
        """vc_identities_conflict's own contract, which the partition rests on."""
        pairs = [
            ({}, {"master": self.M1}, False),
            ({}, {"domain": "a"}, False),
            ({"master": self.M1}, {"master": self.M2}, True),
            ({"master": self.M1}, {"master": self.M1}, False),
            # master decides alone when both carry one, in both directions...
            ({"master": self.M1, "domain": "a"}, {"master": self.M1, "domain": "b"}, False),
            ({"master": self.M1, "domain": "a"}, {"master": self.M2, "domain": "a"}, True),
            # ...and when only one carries a master, the discriminator answers.
            ({"master": self.M1, "domain": "a"}, {"domain": "b"}, True),
            ({"master": self.M1, "domain": "a"}, {"domain": "a"}, False),
            ({"domain": ""}, {"domain": "a"}, True),
        ]
        for a, b, expected in pairs:
            with self.subTest(f"{a} vs {b}"):
                self.assertEqual(vc_identities_conflict(a, b), expected)
                self.assertEqual(vc_identities_conflict(b, a), expected, "not symmetric")


class VCPartitionKeyScopeTests(TestCase):
    """The group qualifier must not reach the keys that are not the name."""

    @staticmethod
    def _distinct(result):
        """
        How many distinct nodes survived, which is NOT len(result).

        _fingerprint_dedupe appends to its output once per INPUT entity, and a
        merge appends the survivor's uuid again -- so a merged pair comes back
        as a two-element list holding the same node twice. Counting the list
        was the vacuous assertion this helper exists to stop: it reports the
        input count either way.
        """
        return len({entity["_uuid"] for entity in result})

    @staticmethod
    def _vc(uuid, name, master_uuid):
        return {
            "_uuid": uuid,
            "_object_type": "dcim.virtualchassis",
            "_refs": set(),
            "name": name,
            "master": UnresolvedReference(object_type="dcim.device", uuid=master_uuid),
        }

    def test_two_names_one_master_still_meet_on_the_unique_master_key(self):
        """
        A partitioned node must still dedupe against an UNPARTITIONED one.

        The group qualifier is bucket-local: a node is given one only inside a
        name bucket that actually splits. Qualifying EVERY fingerprint with it
        therefore put partitioned nodes in a different key space from every
        other node, and two chassis nodes naming ONE master under DIFFERENT
        names stopped meeting on the auto-derived unique_master key -- two
        creates, both claiming a master the database holds unique, so the
        second bound the first row instead of reporting anything.

        Here "fpr-stack" splits (two masters) so its nodes carry a qualifier,
        while "fpr-other" is alone and carries none. It names the same master
        as one of them, so the two are the same row by the unique constraint
        and must meet. Meeting them surfaces the real disagreement -- one row
        cannot be called two things -- which is a diagnosable refusal rather
        than a silent wrong write. Measured with the qualifier on every key:
        three entities out, no error.
        """
        nodes = [
            self._vc("vc-a", "fpr-stack", "dev-m1"),
            self._vc("vc-b", "fpr-stack", "dev-m2"),
            self._vc("vc-c", "fpr-other", "dev-m1"),
        ]
        with self.assertRaises(serializers.ValidationError) as caught:
            transformer._fingerprint_dedupe(nodes)
        message = str(caught.exception)
        self.assertIn("Conflicting values for 'name'", message)
        self.assertIn("fpr-stack", message)
        self.assertIn("fpr-other", message)

    @staticmethod
    def _addressed(uuid, name, netbox_id):
        return {
            "_uuid": uuid,
            "_object_type": "dcim.virtualchassis",
            "_refs": set(),
            "name": name,
            "_netbox_id": netbox_id,
        }

    def test_two_nodes_addressing_different_rows_do_not_merge(self):
        """
        An explicit row id is identity, and dedupe is where it has to be read.

        _resolve_existing_references is the only other place that consults
        _netbox_id, and it runs AFTER _fingerprint_dedupe. So two same-named
        nodes explicitly addressing two DIFFERENT rows were merged into one
        before anything looked at their ids, and one addressed row was silently
        dropped. An id outranks even master here: it names the row itself.
        """
        nodes = [
            self._addressed("vc-1", "fpi-stack", 4001),
            self._addressed("vc-2", "fpi-stack", 4002),
        ]
        result, _ = transformer._fingerprint_dedupe(nodes)
        self.assertEqual(self._distinct(result), 2, "two separately addressed rows merged")
        self.assertEqual({e["_netbox_id"] for e in result}, {4001, 4002})

    def test_two_nodes_addressing_the_same_row_still_merge(self):
        """The other direction: one id named twice is one node, as before."""
        nodes = [
            self._addressed("vc-1", "fpi-same", 4003),
            self._addressed("vc-2", "fpi-same", 4003),
        ]
        result, _ = transformer._fingerprint_dedupe(nodes)
        self.assertEqual(self._distinct(result), 1, "one addressed row became two nodes")
        self.assertEqual(result[0]["_netbox_id"], 4003)

    def test_the_split_itself_still_holds_under_the_narrowed_qualifier(self):
        """Narrowing the qualifier to the name key must not un-split a bucket."""
        nodes = [
            self._vc("vc-a", "fpr-two", "dev-m1"),
            self._vc("vc-b", "fpr-two", "dev-m2"),
        ]
        result, _ = transformer._fingerprint_dedupe(nodes)
        self.assertEqual(self._distinct(result), 2,
                         "the two masters were merged into one chassis")
        self.assertEqual({e["name"] for e in result}, {"fpr-two"})
