"""The rule that decides whether two same-named VirtualChassis nodes are one chassis."""
from itertools import permutations

from django.test import SimpleTestCase

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    asserted_vc_identity,
    partition_vc_identities,
    vc_identities_conflict,
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
