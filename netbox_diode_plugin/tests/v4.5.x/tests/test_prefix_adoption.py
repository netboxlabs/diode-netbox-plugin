"""E2E: duplicate prefix creates adopt the existing prefix at apply time."""
from types import SimpleNamespace
from unittest import mock

from ipam.models import VRF, Prefix
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class PrefixAdoptionE2ETests(APITestCase):
    """Concurrently planned duplicate prefix CREATEs must adopt, not duplicate.

    NetBox permits duplicate prefixes: Prefix.Meta declares only ordering and
    indexes, and the duplicate check in Prefix.clean() is gated on
    ENFORCE_GLOBAL_UNIQUE or the VRF's enforce_unique flag, neither of which
    the applier reaches because it saves through DRF serializers without
    full_clean(). So nothing below the matcher stops a second insert, which is
    why ipam.prefix needs the pre-save match.
    """

    def setUp(self):
        """Mock OAuth2 introspection so the Diode API endpoints accept requests."""
        super().setUp()
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
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

        self.vrf = VRF.objects.create(name="pa-vrf", rd="64805:7")

    def _prefix_entity(self, prefix="10.9.16.0/22", vrf=True, extra=None):
        entity = {"prefix": prefix}
        if vrf:
            entity["vrf"] = {"name": "pa-vrf", "rd": "64805:7"}
        entity.update(extra or {})
        return {"timestamp": 1, "object_type": "ipam.prefix", "entity": {"prefix": entity}}

    def _diff(self, payload):
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("change_set", r.json(), r.content)
        return r.json().get("change_set", {})

    def _apply(self, cs, expect=200):
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def test_plan_ahead_duplicate_within_vrf_adopts(self):
        """Two changesets planned before either apply yield one prefix, not two.

        This is the reported shape: both plans see no existing prefix, so both
        emit CREATE, and without the pre-save match both inserts succeed.
        """
        cs_a = self._diff(self._prefix_entity())
        cs_b = self._diff(self._prefix_entity(extra={"description": "second planner"}))

        self._apply(cs_a)
        self._apply(cs_b)

        prefixes = Prefix.objects.filter(prefix="10.9.16.0/22", vrf=self.vrf)
        self.assertEqual(prefixes.count(), 1)
        self.assertEqual(prefixes.first().description, "second planner")

        # converged: re-planning the same entity produces no real change
        cs = self._diff(self._prefix_entity(extra={"description": "second planner"}))
        non_noop = [c for c in cs.get("changes", []) if c["change_type"] != "noop"]
        self.assertEqual(non_noop, [], non_noop)

    def test_plan_ahead_duplicate_in_global_table_adopts(self):
        """The vrf-is-NULL matcher needs the same protection as the VRF one.

        ipam.prefix has two logical matchers, split on whether vrf is NULL, so
        a global-table prefix takes a different path and is covered separately.
        """
        cs_a = self._diff(self._prefix_entity(prefix="10.9.20.0/22", vrf=False))
        cs_b = self._diff(self._prefix_entity(prefix="10.9.20.0/22", vrf=False))

        self._apply(cs_a)
        self._apply(cs_b)

        self.assertEqual(
            Prefix.objects.filter(prefix="10.9.20.0/22", vrf__isnull=True).count(), 1
        )

    def test_same_prefix_in_different_vrfs_stays_distinct(self):
        """The hazard pin: adoption must not collapse genuinely distinct rows.

        The same network in two different VRFs is two legitimate objects. If
        the pre-save match ignored the VRF it would adopt across them, which
        would be worse than the duplicate it exists to prevent.
        """
        other = VRF.objects.create(name="pa-vrf-b", rd="64805:8")
        self._apply(self._diff(self._prefix_entity()))
        self._apply(
            self._diff(
                {
                    "timestamp": 1,
                    "object_type": "ipam.prefix",
                    "entity": {
                        "prefix": {
                            "prefix": "10.9.16.0/22",
                            "vrf": {"name": "pa-vrf-b", "rd": "64805:8"},
                        }
                    },
                }
            )
        )

        self.assertEqual(Prefix.objects.filter(prefix="10.9.16.0/22").count(), 2)
        self.assertEqual(
            Prefix.objects.filter(prefix="10.9.16.0/22", vrf=self.vrf).count(), 1
        )
        self.assertEqual(
            Prefix.objects.filter(prefix="10.9.16.0/22", vrf=other).count(), 1
        )

    def test_global_and_vrf_prefix_stay_distinct(self):
        """A prefix in the global table and the same one in a VRF are distinct.

        The two matchers are conditioned on vrf being NULL or NOT NULL, so this
        pins that the global-table row is not adopted by the VRF-scoped one.
        """
        self._apply(self._diff(self._prefix_entity(prefix="10.9.24.0/22", vrf=False)))
        self._apply(self._diff(self._prefix_entity(prefix="10.9.24.0/22")))

        self.assertEqual(Prefix.objects.filter(prefix="10.9.24.0/22").count(), 2)

    def test_miss_then_create_still_works(self):
        """Find-first miss on an empty table falls through to a normal create."""
        self._apply(self._diff(self._prefix_entity(prefix="10.9.28.0/22")))
        self.assertEqual(Prefix.objects.filter(prefix="10.9.28.0/22").count(), 1)
