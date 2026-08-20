"""Unit tests for the driver-field policy (interface mode/type, VLAN qinq_role)."""
from types import SimpleNamespace
from unittest import mock

from dcim.models import Interface
from django.test import SimpleTestCase
from utilities.data import shallow_compare_dict
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.differ import normalize_changeset
from netbox_diode_plugin.api.field_policy import (
    _DRIVER_FIELD_RULES,
    match_participating_fields,
    submitted_driver_field_drops,
)
from netbox_diode_plugin.plugin_config import get_diode_user


class NormalizeChangesetTests(SimpleTestCase):
    """Pure-logic tests: no DB, operate on plain dicts."""

    def _plan(self, object_type, prechange, entity):
        """Run both policy phases in pipeline order and return (entity, dropped)."""
        # Phase 1 runs in the transformer, before any fingerprinting; phase 2 runs
        # in the differ once an existing row has been matched. Tests exercise them
        # in that order so a unit result means the same thing an ingest would.
        dropped = submitted_driver_field_drops(object_type, entity)
        normalize_changeset(object_type, prechange, entity)
        return entity, dropped

    def _run(self, object_type, prechange, entity):
        return self._plan(object_type, prechange, entity)[0]

    def _drop(self, object_type, prechange, entity):
        """Run both phases and return (entity, dropped-submitted-field map)."""
        return self._plan(object_type, prechange, entity)

    def test_tagged_to_access_clears_tagged_vlans_keeps_untagged(self):
        """Mode tagged -> access clears tagged_vlans but leaves untagged_vlan untouched."""
        prechange = {"mode": "tagged", "tagged_vlans": [101, 102], "untagged_vlan": 5}
        entity = {"mode": "access"}  # only mode submitted
        out = self._run("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])       # forbidden -> cleared
        self.assertNotIn("untagged_vlan", out)           # allowed for access -> untouched

    def test_tagged_to_empty_clears_tagged_and_untagged(self):
        """Mode tagged -> empty (routed) clears both tagged_vlans and untagged_vlan."""
        prechange = {"mode": "tagged", "tagged_vlans": [101], "untagged_vlan": 5}
        entity = {"mode": ""}
        out = self._run("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])
        self.assertIsNone(out["untagged_vlan"])

    def test_qinq_to_access_clears_tagged_and_qinq_svlan(self):
        """Mode q-in-q -> access clears tagged_vlans and qinq_svlan."""
        prechange = {"mode": "q-in-q", "tagged_vlans": [101], "qinq_svlan": 9}
        entity = {"mode": "access"}
        out = self._run("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])
        self.assertIsNone(out["qinq_svlan"])

    def test_explicit_forbidden_value_is_dropped_driver_wins(self):
        """The submitted driver value wins: an explicit but forbidden value is dropped."""
        # POLICY (reversed): a payload naming mode "access" AND naming tagged VLANs is
        # self-contradictory. NetBox rejects the WHOLE entity for it, so the mode wins
        # and the field it forbids is discarded — producer intent does NOT protect it.
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "access", "tagged_vlans": [200]}
        out, dropped = self._drop("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])
        self.assertIn("tagged_vlans", dropped)
        self.assertIn("mode", dropped["tagged_vlans"])
        self.assertIn("access", dropped["tagged_vlans"])

    def test_explicit_allowed_value_is_respected(self):
        """Producer intent survives for everything the driver value ALLOWS."""
        # mode "tagged" permits tagged_vlans, so an explicit list is kept verbatim and
        # is not reported as dropped. This is the half of producer intent that stands.
        out, dropped = self._drop(
            "dcim.interface",
            {"mode": "access", "tagged_vlans": []},
            {"mode": "tagged", "tagged_vlans": [200]},
        )
        self.assertEqual(out["tagged_vlans"], [200])
        self.assertEqual(dropped, {})

    def test_explicit_empty_forbidden_value_is_not_a_change(self):
        """A submitted value that is already empty is left exactly as submitted."""
        # Rewriting it would be pointless and could only manufacture a spurious
        # change (e.g. [] -> None); nothing is reported as dropped either.
        out, dropped = self._drop(
            "dcim.interface", {}, {"mode": "access", "tagged_vlans": []}
        )
        self.assertEqual(out["tagged_vlans"], [])
        self.assertEqual(dropped, {})

    def test_no_clear_when_prechange_field_already_empty(self):
        """Nothing is injected into entity when the prechange dependent field was already empty."""
        prechange = {"mode": "tagged", "tagged_vlans": []}
        entity = {"mode": "access"}
        out = self._run("dcim.interface", prechange, entity)
        self.assertNotIn("tagged_vlans", out)  # nothing stale -> nothing injected

    def test_effective_mode_from_prechange_when_mode_not_submitted(self):
        """Effective mode falls back to prechange when mode itself is not submitted."""
        # mode already access in DB, some other field updated, stale tagged_vlans present
        prechange = {"mode": "access", "tagged_vlans": [101], "description": "old"}
        entity = {"description": "new"}
        out = self._run("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])

    def test_vminterface_covered(self):
        """virtualization.vminterface is covered by the same normalization rules."""
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "access"}
        out = self._run("virtualization.vminterface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [])

    def test_unregistered_type_is_noop(self):
        """Object types not in the registry are left untouched."""
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "access"}
        out = self._run("dcim.device", prechange, entity)
        self.assertNotIn("tagged_vlans", out)

    def test_create_drops_forbidden_submitted_value(self):
        """A create is not exempt: mode "access" drops the tagged_vlans it forbids."""
        # This is the corpus shape (interface "WirelessGigabitEthernet1/0/1"): a CREATE
        # carrying mode "access" together with untagged_vlan AND tagged_vlans. Before
        # the reversed policy this was a no-op and NetBox 400'd the whole entity.
        entity = {
            "mode": "access",
            "untagged_vlan": 900,
            "tagged_vlans": [101, 102],
        }
        out, dropped = self._drop("dcim.interface", {}, entity)
        # DROPPED, not blanked: with no stored row there is nothing to clear, so the
        # payload must read as if the producer had never sent the field. Writing a
        # blank here is what made the rf_* fields re-diff against NetBox's "" forever.
        self.assertNotIn("tagged_vlans", out)           # forbidden -> dropped
        self.assertEqual(out["untagged_vlan"], 900)     # allowed for access -> kept
        self.assertEqual(list(dropped), ["tagged_vlans"])

    def test_create_injects_nothing_when_field_not_submitted(self):
        """A create with nothing forbidden submitted stays untouched (nothing injected)."""
        entity = {"mode": "access"}
        out, dropped = self._drop("dcim.interface", {}, entity)
        self.assertNotIn("tagged_vlans", out)  # no stored row, nothing to clear
        self.assertNotIn("qinq_svlan", out)
        self.assertEqual(dropped, {})

    def test_create_unknown_mode_fails_open(self):
        """An unrecognised driver value forbids nothing, on creates too."""
        entity = {"mode": "future-mode", "tagged_vlans": [101]}
        out, dropped = self._drop("dcim.interface", {}, entity)
        self.assertEqual(out["tagged_vlans"], [101])
        self.assertEqual(dropped, {})

    def test_stale_clear_is_not_reported_as_dropped(self):
        """Injecting a clear for a stale STORED value is not a drop of producer data."""
        out, dropped = self._drop(
            "dcim.interface", {"mode": "tagged", "tagged_vlans": [101]}, {"mode": "access"}
        )
        self.assertEqual(out["tagged_vlans"], [])
        self.assertEqual(dropped, {})  # nothing was submitted, so nothing was discarded

    def test_shared_policy_applies_to_interface_type_rf_fields(self):
        """The reversed policy is registry-wide: dcim.interface "type" behaves the same."""
        entity = {"type": "1000base-t", "rf_channel": "2.4g-1-2412-22", "rf_role": "ap"}
        out, dropped = self._drop("dcim.interface", {}, entity)
        self.assertNotIn("rf_channel", out)
        self.assertNotIn("rf_role", out)
        self.assertEqual(sorted(dropped), ["rf_channel", "rf_role"])

    def test_shared_policy_keeps_rf_fields_on_a_wireless_type(self):
        """A wireless type permits the rf fields, so an explicit create keeps them."""
        entity = {"type": "ieee802.11ac", "rf_channel": "2.4g-1-2412-22", "rf_role": "ap"}
        out, dropped = self._drop("dcim.interface", {}, entity)
        self.assertEqual(out["rf_channel"], "2.4g-1-2412-22")
        self.assertEqual(dropped, {})

    def test_a_match_participating_field_is_never_dropped(self):
        """ipam.vlan qinq_svlan is a match criterion, so the policy leaves it alone."""
        # The reversal is NOT registry-wide. qinq_svlan is read by four ipam.vlan
        # matchers, so dropping it changes which row the payload identifies: measured,
        # it adopted and renamed an unrelated VLAN sharing the vid, or inserted a
        # duplicate and converged onto it. NetBox's own 400 stands instead.
        self.assertIn("qinq_svlan", match_participating_fields("ipam.vlan"))
        entity = {"qinq_role": "svlan", "qinq_svlan": 7}
        out, dropped = self._drop("ipam.vlan", {}, entity)
        self.assertEqual(out["qinq_svlan"], 7)
        self.assertEqual(dropped, {})

    def test_the_interface_policy_fields_are_not_match_criteria(self):
        """The fields the policy does drop are not how any matcher identifies a row."""
        # This is what makes the motivating cases safe to drop, and it is asserted
        # rather than assumed: if a future matcher starts reading one of them, the
        # gate above will silently start exempting it and this test says so first.
        for object_type in ("dcim.interface", "virtualization.vminterface"):
            matched = match_participating_fields(object_type)
            for value_map in _DRIVER_FIELD_RULES[object_type].values():
                for dependents in value_map.values():
                    for dependent in dependents:
                        with self.subTest(object_type=object_type, dependent=dependent):
                            self.assertNotIn(dependent, matched)

    def test_shared_policy_keeps_qinq_svlan_on_a_customer_vlan(self):
        """qinq_role "cvlan" permits qinq_svlan, so an explicit create keeps it."""
        entity = {"qinq_role": "cvlan", "qinq_svlan": 7}
        out, dropped = self._drop("ipam.vlan", {}, entity)
        self.assertEqual(out["qinq_svlan"], 7)
        self.assertEqual(dropped, {})

    def test_unknown_mode_fails_open(self):
        """An unknown/unlisted mode value fails open and clears nothing."""
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "future-mode"}
        out = self._run("dcim.interface", prechange, entity)
        self.assertNotIn("tagged_vlans", out)  # unknown mode -> clear nothing

    def test_qinq_to_tagged_clears_qinq_svlan_keeps_tagged(self):
        """Mode 'tagged' forbids only qinq_svlan; tagged_vlans are allowed and kept."""
        out = self._run(
            "dcim.interface",
            {"mode": "q-in-q", "qinq_svlan": 5, "tagged_vlans": [101]},
            {"mode": "tagged"},
        )
        self.assertIsNone(out["qinq_svlan"])
        self.assertNotIn("tagged_vlans", out)

    def test_vlan_svlan_role_clears_stale_qinq_svlan(self):
        """qinq_role svlan (not customer) forbids qinq_svlan -> stale value cleared."""
        out = self._run("ipam.vlan", {"qinq_role": "cvlan", "qinq_svlan": 7}, {"qinq_role": "svlan"})
        self.assertIsNone(out["qinq_svlan"])

    def test_vlan_empty_role_clears_qinq_svlan(self):
        """No qinq role forbids qinq_svlan -> cleared."""
        out = self._run("ipam.vlan", {"qinq_role": "cvlan", "qinq_svlan": 7}, {"qinq_role": ""})
        self.assertIsNone(out["qinq_svlan"])

    def test_vlan_customer_role_keeps_qinq_svlan(self):
        """Customer (cvlan) role permits qinq_svlan -> not cleared."""
        out = self._run("ipam.vlan", {"qinq_role": "cvlan", "qinq_svlan": 7}, {"qinq_role": "cvlan", "name": "x"})
        self.assertNotIn("qinq_svlan", out)

    def test_interface_nonwireless_type_clears_rf_fields(self):
        """A non-wireless interface type forbids rf_channel/frequency/width/role -> cleared."""
        prechange = {"type": "ieee802.11ac", "rf_channel": "ch", "rf_channel_frequency": 2412,
                     "rf_channel_width": 22, "rf_role": "ap"}
        out = self._run("dcim.interface", prechange, {"type": "1000base-t"})
        self.assertIsNone(out["rf_channel"])
        self.assertIsNone(out["rf_channel_frequency"])
        self.assertIsNone(out["rf_channel_width"])
        self.assertIsNone(out["rf_role"])

    def test_interface_wireless_type_keeps_rf_fields(self):
        """A wireless interface type permits rf fields -> not cleared."""
        prechange = {"type": "ieee802.11ac", "rf_channel": "ch"}
        out = self._run("dcim.interface", prechange, {"description": "x"})  # type unchanged (wireless)
        self.assertNotIn("rf_channel", out)


    # --- a driver value must be SUBMITTED to win (N3) -------------------------

    def test_create_omitting_the_driver_field_keeps_its_dependent_fields(self):
        """A create that simply omits mode keeps the VLANs NetBox would have stored."""
        # The policy is that a SUBMITTED driver value wins over the fields it forbids.
        # With no submitted driver value there is nothing to win, so producer data is
        # left alone: mode absent must not read as mode "" and drop both VLAN fields.
        entity = {"name": "vmeth0", "untagged_vlan": 601, "tagged_vlans": [602]}
        out, dropped = self._drop("virtualization.vminterface", {}, entity)
        self.assertEqual(out["untagged_vlan"], 601)
        self.assertEqual(out["tagged_vlans"], [602])
        self.assertEqual(dropped, {})

    def test_explicitly_submitted_empty_driver_value_does_win(self):
        """An explicitly submitted empty mode IS a submitted value, and forbids the VLANs."""
        entity = {"name": "vmeth0", "mode": "", "untagged_vlan": 601, "tagged_vlans": [602]}
        out, dropped = self._drop("virtualization.vminterface", {}, entity)
        self.assertNotIn("untagged_vlan", out)
        self.assertNotIn("tagged_vlans", out)
        self.assertEqual(sorted(dropped), ["tagged_vlans", "untagged_vlan"])
        # the reason must not claim a value that was never submitted
        self.assertIn("empty", dropped["tagged_vlans"])
        self.assertNotIn("''", dropped["tagged_vlans"])

    def test_explicitly_submitted_null_driver_value_does_win(self):
        """A submitted null mode is submitted too, and reads as no 802.1Q."""
        out, dropped = self._drop("dcim.interface", {}, {"mode": None, "tagged_vlans": [602]})
        self.assertNotIn("tagged_vlans", out)
        self.assertEqual(list(dropped), ["tagged_vlans"])

    def test_stored_driver_value_never_drops_submitted_data(self):
        """A driver value that was only STORED does not drop submitted data."""
        # The stored mode forbids tagged_vlans, but the producer submitted no mode, so
        # there is no submitted driver value to win and the explicit list stands (NetBox
        # judges it, exactly as it does on develop).
        out, dropped = self._drop(
            "dcim.interface", {"mode": "access", "tagged_vlans": [101]}, {"tagged_vlans": [102]},
        )
        self.assertEqual(out["tagged_vlans"], [102])
        self.assertEqual(dropped, {})

    def test_non_string_driver_value_fails_open_in_both_phases(self):
        """A driver value that is not a string is not one we can reason about."""
        # A malformed payload must not turn into a TypeError on an unhashable rule key.
        out, dropped = self._drop(
            "dcim.interface",
            {"mode": "tagged", "tagged_vlans": [101]},
            {"mode": {"vid": 5}, "tagged_vlans": [102]},
        )
        self.assertEqual(out["tagged_vlans"], [102])
        self.assertEqual(dropped, {})

    # --- the clear must converge against what NetBox actually stores (N1) -----

    def test_submitted_forbidden_field_is_removed_not_blanked(self):
        """A dropped field leaves the payload entirely; no blank is invented for it."""
        out, dropped = self._drop("dcim.interface", {}, {"type": "1000base-t", "rf_role": "ap"})
        self.assertNotIn("rf_role", out)
        self.assertEqual(list(dropped), ["rf_role"])

    def test_type_rf_clear_converges_against_the_blank_string_netbox_stores(self):
        """The type/rf_* clear plans nothing once NetBox has stored '' for the rf fields."""
        # rf_channel/rf_role are CharFields with null=False: NetBox coerces the submitted
        # null to ''. Re-injecting None every round diffed '' vs None and emitted a
        # spurious UPDATE on every ingest cycle forever.
        payload = {"type": "1000base-t", "rf_role": "ap", "rf_channel": "2.4g-1-2412-22"}
        stored = {"type": "ieee802.11ac", "rf_role": "ap", "rf_channel": "2.4g-1-2412-22",
                  "rf_channel_frequency": 2412, "rf_channel_width": 22}
        round1, dropped = self._drop("dcim.interface", dict(stored), dict(payload))
        self.assertEqual(sorted(dropped), ["rf_channel", "rf_role"])
        self.assertIsNone(round1["rf_role"])                            # stale value cleared
        self.assertNotEqual(shallow_compare_dict(stored, round1), {})    # round 1 is a real change

        stored2 = {"type": "1000base-t", "rf_role": "", "rf_channel": "",
                   "rf_channel_frequency": None, "rf_channel_width": None}
        round2, dropped2 = self._drop("dcim.interface", dict(stored2), dict(payload))
        self.assertEqual(sorted(dropped2), ["rf_channel", "rf_role"])    # still reported
        self.assertNotIn("rf_role", round2)                             # but nothing written
        self.assertEqual(shallow_compare_dict(stored2, round2), {})      # -> empty plan

    # --- every (driver value, dependent field) pair in the registry -----------

    def _pair_sentinel(self, dependent):
        """A non-empty submitted value for a dependent field (only truthiness matters)."""
        return ["x"] if dependent == "tagged_vlans" else "x"

    def _assert_pair_converges(self, object_type, driver_field, driver_value, dependent):
        """Assert one (driver value, dependent field) pair drops, clears once, then converges."""
        sentinel = self._pair_sentinel(dependent)
        submitted = {driver_field: driver_value, dependent: sentinel}

        if dependent in match_participating_fields(object_type):
            # Exempt: the field identifies the row, so phase 1 must not touch it and
            # there is nothing for us to converge -- NetBox rejects the payload, which
            # is develop's behaviour and the safe one. Phase 2 must still clear a stale
            # STORED value the payload no longer carries.
            out, dropped = self._drop(object_type, {}, dict(submitted))
            self.assertEqual(out[dependent], sentinel)
            self.assertEqual(dropped, {})
            stored = {driver_field: driver_value, dependent: sentinel}
            cleared, _ = self._drop(object_type, dict(stored), {driver_field: driver_value})
            self.assertIn(dependent, cleared)
            self.assertFalse(cleared[dependent])
            return

        # 1. a submitted driver value drops the field it forbids, and writes no blank
        out, dropped = self._drop(object_type, {}, dict(submitted))
        self.assertNotIn(dependent, out)
        self.assertIn(dependent, dropped)

        # 2. with no submitted driver value, submitted data survives untouched
        out, dropped = self._drop(object_type, {}, {dependent: sentinel})
        self.assertEqual(out[dependent], sentinel)
        self.assertEqual(dropped, {})

        # 3. a non-empty STORED value is cleared exactly once...
        stored = {driver_field: driver_value, dependent: sentinel}
        round1, _ = self._drop(object_type, dict(stored), dict(submitted))
        self.assertNotEqual(shallow_compare_dict(stored, round1), {})

        # ...and whichever empty representation NetBox then stores, the next round
        # plans nothing at all for the field.
        for netbox_stored in ("", None, [], (), {}):
            with self.subTest(netbox_stored=netbox_stored):
                stored2 = {driver_field: driver_value, dependent: netbox_stored}
                round2, _ = self._drop(object_type, dict(stored2), dict(submitted))
                self.assertNotIn(dependent, round2)
                self.assertEqual(shallow_compare_dict(stored2, round2), {})

    def test_every_registered_driver_value_dependent_pair_converges(self):
        """Every (driver value, dependent field) pair in every rule map converges."""
        # Exhaustive over the registry, including all ~100 non-wireless interface types
        # in _INTERFACE_TYPE_RF_RULES, so a new rule cannot be added without meeting it.
        pairs = 0
        for object_type, rules in _DRIVER_FIELD_RULES.items():
            for driver_field, value_map in rules.items():
                for driver_value, dependents in value_map.items():
                    for dependent in dependents:
                        with self.subTest(object_type=object_type, driver_field=driver_field,
                                          driver_value=driver_value, dependent=dependent):
                            self._assert_pair_converges(
                                object_type, driver_field, driver_value, dependent,
                            )
                        pairs += 1
        self.assertGreater(pairs, 100)  # the map is generated; guard against an empty sweep


class InterfaceModeClearE2ETests(APITestCase):
    """End-to-end: seed an existing interface, ingest a mode change, assert clear."""

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

    def _device(self):
        return {
            "name": "obs3607-sw",
            "role": {"name": "obs3607-role"},
            "device_type": {"manufacturer": {"name": "obs3607-mfr"}, "model": "obs3607-model"},
            "site": {"name": "obs3607-site"},
        }

    def _diff_apply(self, entity):
        payload = {"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": entity}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        cs = r1.json().get("change_set", {})
        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        return r1, r2

    def _plan(self, payload):
        """Generate a diff for a payload and return its change set."""
        r = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json().get("change_set", {})

    def _apply(self, cs):
        """Apply a change set and assert it landed without errors."""
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json().get("errors"), r.content)
        return r

    def _converge(self, payload, label, max_applies=2, quiet_rounds=4):
        """
        Apply a payload until its plan is empty, then assert it stays empty.

        Returns the number of applies it took. An empty plan is the strongest
        available statement of convergence: the change set carries no changes at
        all, so re-ingest performs no write and logs no object change.
        """
        applies = 0
        for _ in range(max_applies + 1):
            cs = self._plan(payload)
            if not cs.get("changes", []):
                break
            self._apply(cs)
            applies += 1
        else:
            self.fail(f"{label} did not converge within {max_applies} applies")
        self.assertLessEqual(applies, max_applies, f"{label} needed {applies} applies")
        for quiet in range(1, quiet_rounds + 1):
            self.assertEqual(
                self._plan(payload).get("changes", []), [],
                f"{label} re-planned on quiet round {quiet}",
            )
        return applies

    def _iface_payload(self, **fields):
        """Build a dcim.interface generate-diff payload for this test's device."""
        entity = {"device": self._device()}
        entity.update(fields)
        return {"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": entity}}

    def _vm_payload(self, **fields):
        """Build a virtualization.vminterface generate-diff payload."""
        entity = {"virtual_machine": {"name": "obs3607-vm",
                                      "cluster": {"name": "obs3607-cl", "type": {"name": "obs3607-ct"}}}}
        entity.update(fields)
        return {"timestamp": 1, "object_type": "virtualization.vminterface",
                "entity": {"vm_interface": entity}}

    def test_tagged_to_access_clears_tagged_vlans_and_is_idempotent(self):
        """E2E: tagged -> access clears tagged_vlans, keeps untagged_vlan, and re-diffs as NOOP."""
        create = {
            "name": "Eth1", "type": "1000base-t", "device": self._device(),
            "mode": "tagged",
            "untagged_vlan": {"vid": 100, "name": "v100"},
            "tagged_vlans": [{"vid": 101, "name": "v101"}, {"vid": 102, "name": "v102"}],
        }
        _, apply1 = self._diff_apply(create)
        self.assertEqual(apply1.status_code, 200)
        iface = Interface.objects.get(name="Eth1")
        self.assertEqual(iface.tagged_vlans.count(), 2)

        # change mode to access, do NOT send tagged_vlans
        update = {"name": "Eth1", "type": "1000base-t", "device": self._device(), "mode": "access"}
        diff2, apply2 = self._diff_apply(update)
        # The injected clear must survive into change.data — this is exactly what
        # #162's preserve_empty enables; assert it so a missing base cannot regress silently.
        iface_updates = [c for c in diff2.json()["change_set"]["changes"]
                         if c["object_type"] == "dcim.interface"]
        self.assertTrue(
            any(c.get("data", {}).get("tagged_vlans") == [] for c in iface_updates),
            "expected tagged_vlans:[] in the UPDATE change.data (requires #162 preserve_empty)",
        )
        self.assertEqual(apply2.status_code, 200)
        self.assertIsNone(apply2.json().get("errors"))
        iface.refresh_from_db()
        self.assertEqual(iface.mode, "access")
        self.assertEqual(iface.tagged_vlans.count(), 0)          # cleared
        self.assertIsNotNone(iface.untagged_vlan)                # preserved

        # re-diff of the same access payload must be a NOOP (idempotency)
        r3 = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": update}},
            format="json", **self.auth,
        )
        changes = [c for c in r3.json().get("change_set", {}).get("changes", [])
                   if c["object_type"] == "dcim.interface"]
        self.assertTrue(all(c["change_type"] == "noop" for c in changes))

    def test_tagged_to_taggedall_clears_tagged_keeps_untagged(self):
        """E2E: tagged -> tagged-all clears tagged_vlans but keeps untagged_vlan."""
        create = {
            "name": "EthTA", "type": "1000base-t", "device": self._device(),
            "mode": "tagged",
            "untagged_vlan": {"vid": 200, "name": "v200"},
            "tagged_vlans": [{"vid": 201, "name": "v201"}],
        }
        _, a1 = self._diff_apply(create)
        self.assertEqual(a1.status_code, 200)
        update = {"name": "EthTA", "type": "1000base-t", "device": self._device(), "mode": "tagged-all"}
        _, a2 = self._diff_apply(update)
        self.assertEqual(a2.status_code, 200)
        iface = Interface.objects.get(name="EthTA")
        self.assertEqual(iface.tagged_vlans.count(), 0)
        self.assertIsNotNone(iface.untagged_vlan)  # untagged allowed for tagged-all
        r = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": update}},
            format="json", **self.auth,
        )
        ch = [c for c in r.json()["change_set"]["changes"] if c["object_type"] == "dcim.interface"]
        self.assertTrue(all(c["change_type"] == "noop" for c in ch))  # post-update idempotency

    def test_tagged_to_routed_clears_both_and_is_idempotent(self):
        """E2E: tagged -> empty (routed) clears both tagged_vlans and untagged_vlan, idempotently."""
        create = {
            "name": "EthR", "type": "1000base-t", "device": self._device(),
            "mode": "tagged",
            "untagged_vlan": {"vid": 300, "name": "v300"},
            "tagged_vlans": [{"vid": 301, "name": "v301"}],
        }
        _, a1 = self._diff_apply(create)
        self.assertEqual(a1.status_code, 200)
        update = {"name": "EthR", "type": "1000base-t", "device": self._device(), "mode": ""}
        _, a2 = self._diff_apply(update)
        self.assertEqual(a2.status_code, 200)
        self.assertIsNone(a2.json().get("errors"))
        iface = Interface.objects.get(name="EthR")
        self.assertEqual(iface.mode, "")
        self.assertEqual(iface.tagged_vlans.count(), 0)
        self.assertIsNone(iface.untagged_vlan)  # empty mode forbids untagged too
        # post-update idempotency for the empty-mode FK-clear path (mode:"" vs NOOP detection)
        r = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": update}},
            format="json", **self.auth,
        )
        ch = [c for c in r.json()["change_set"]["changes"] if c["object_type"] == "dcim.interface"]
        self.assertTrue(all(c["change_type"] == "noop" for c in ch))

    def test_explicit_incompatible_tagged_vlans_is_dropped_not_rejected(self):
        """An explicit incompatible mode/tagged_vlans update applies with the field dropped."""
        # POLICY (reversed): this combination used to be left alone so NetBox could
        # reject it, which cost the whole entity. The submitted mode now wins: the
        # update applies, tagged_vlans is dropped, and the drop is surfaced as a
        # warning on the change set rather than swallowed silently.
        create = {
            "name": "EthNeg", "type": "1000base-t", "device": self._device(),
            "mode": "tagged",
            "tagged_vlans": [{"vid": 111, "name": "v111"}],
        }
        _, apply1 = self._diff_apply(create)
        self.assertEqual(apply1.status_code, 200)
        iface = Interface.objects.get(name="EthNeg")
        self.assertEqual(iface.tagged_vlans.count(), 1)

        bad = {
            "name": "EthNeg", "type": "1000base-t", "device": self._device(),
            "mode": "access",
            "tagged_vlans": [{"vid": 111, "name": "v111"}],  # explicit + incompatible
        }
        payload = {"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": bad}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        cs = r1.json().get("change_set", {})
        self.assertIn("tagged_vlans", cs.get("warnings", {}).get("dcim.interface", {}))
        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNone(r2.json().get("errors"))
        iface.refresh_from_db()
        self.assertEqual(iface.mode, "access")
        self.assertEqual(iface.tagged_vlans.count(), 0)  # the mode won; the field was dropped

        # and the very same self-contradictory payload re-diffs as a NOOP
        r3 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        ch = [c for c in r3.json()["change_set"]["changes"] if c["object_type"] == "dcim.interface"]
        self.assertTrue(all(c["change_type"] == "noop" for c in ch))

    def test_corpus_create_access_mode_with_tagged_vlans_applies(self):
        """A CREATE naming mode access, untagged_vlan AND tagged_vlans applies and is idempotent."""
        # Reproduction of the corpus entity "WirelessGigabitEthernet1/0/1", which used to
        # lose the whole interface to a 400 {"tagged_vlans": ["Interface mode does not
        # support tagged vlans"]}. The create must now land with the mode honoured, the
        # untagged VLAN attached and the forbidden tagged VLANs dropped.
        create = {
            "name": "WlAccess", "type": "other-wireless", "device": self._device(),
            "mode": "access",
            "rf_role": "ap", "rf_channel": "2.4g-1-2412-22",
            "untagged_vlan": {"vid": 900, "name": "v900"},
            "tagged_vlans": [{"vid": 101, "name": "v101"}, {"vid": 102, "name": "v102"}],
        }
        payload = {"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": create}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        cs = r1.json().get("change_set", {})
        # the create's CREATE change must not carry tagged_vlans at all, and must not
        # leave a dangling unresolved-reference path for the field it no longer carries
        iface_creates = [c for c in cs["changes"] if c["object_type"] == "dcim.interface"]
        self.assertTrue(iface_creates)
        for c in iface_creates:
            self.assertNotIn("tagged_vlans", c.get("data", {}))
            self.assertNotIn("tagged_vlans", c.get("new_refs", []))
        self.assertIn("tagged_vlans", cs.get("warnings", {}).get("dcim.interface", {}))

        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNone(r2.json().get("errors"))
        iface = Interface.objects.get(name="WlAccess")
        self.assertEqual(iface.mode, "access")
        self.assertIsNotNone(iface.untagged_vlan)
        self.assertEqual(iface.untagged_vlan.vid, 900)
        self.assertEqual(iface.tagged_vlans.count(), 0)
        self.assertEqual(iface.rf_channel, "2.4g-1-2412-22")  # wireless type keeps rf fields

        # re-ingesting the identical payload is a no-op
        r3 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r3.json().get("change_set", {}).get("changes", []), [])

        # and submitting the forbidden field EMPTY under the same mode is a no-op too:
        # clearing an already-empty submitted value must never plan an update
        empty = dict(create, tagged_vlans=[])
        r4 = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": empty}},
            format="json", **self.auth,
        )
        self.assertEqual(r4.json().get("change_set", {}).get("changes", []), [])

    def test_qinq_to_access_clears_qinq_svlan_orm_seed(self):
        """E2E: an ORM-seeded q-in-q interface moving to access has qinq_svlan cleared."""
        # ORM-seed a q-in-q interface with a qinq_svlan (ORM .create bypasses full_clean,
        # so no S-VLAN role setup is needed), then ingest mode=access via Diode and assert
        # qinq_svlan is cleared. This is the reliable integration cover for the FK->None path.
        from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
        from ipam.models import VLAN

        site = Site.objects.create(name="qinq-site", slug="qinq-site")
        mfr = Manufacturer.objects.create(name="qinq-mfr", slug="qinq-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="qinq-model", slug="qinq-model")
        role = DeviceRole.objects.create(name="qinq-role", slug="qinq-role")
        dev = Device.objects.create(name="qinq-dev", device_type=dt, role=role, site=site)
        svlan = VLAN.objects.create(vid=602, name="qinq-svlan", site=site)
        cvlan = VLAN.objects.create(vid=603, name="qinq-cvlan", site=site)
        iface = Interface.objects.create(
            device=dev, name="EthQ", type="1000base-t", mode="q-in-q", qinq_svlan=svlan
        )
        iface.tagged_vlans.set([cvlan])  # q-in-q also carries tagged (customer) VLANs

        update = {
            "name": "EthQ", "type": "1000base-t",
            "device": {
                "name": "qinq-dev", "role": {"name": "qinq-role"},
                "device_type": {"manufacturer": {"name": "qinq-mfr"}, "model": "qinq-model"},
                "site": {"name": "qinq-site"},
            },
            "mode": "access",
        }
        _, apply = self._diff_apply(update)
        self.assertEqual(apply.status_code, 200)
        self.assertIsNone(apply.json().get("errors"))
        iface.refresh_from_db()
        self.assertEqual(iface.mode, "access")
        self.assertIsNone(iface.qinq_svlan)              # save() never clears this; the hook must
        self.assertEqual(iface.tagged_vlans.count(), 0)  # q-in-q -> access clears tagged too

    def test_vminterface_mode_change_emits_clear(self):
        """E2E: vminterface mode change emits tagged_vlans:[] in change.data (non-vacuous)."""
        # VMInterface's serializer does NO mode->VLAN validation and BaseInterface.save()
        # auto-clears tagged_vlans on non-tagged modes, so a DB-only assertion would pass
        # even without the hook (vacuous). Assert the emitted change.data instead: the hook
        # must inject tagged_vlans:[] for the vminterface object_type too. (Scopeless cluster
        # + siteless VLAN avoids the serializer's site-consistency check.)
        vm = {"name": "obs3607-vm", "cluster": {"name": "obs3607-cl", "type": {"name": "obs3607-ct"}}}
        create = {"name": "vmeth0", "virtual_machine": vm, "mode": "tagged",
                  "tagged_vlans": [{"vid": 701, "name": "v701"}]}
        cs = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "virtualization.vminterface", "entity": {"vm_interface": create}},
            format="json", **self.auth,
        ).json().get("change_set", {})
        self.assertEqual(
            self.client.post(self.apply_url, data=cs, format="json", **self.auth).status_code, 200
        )

        update = {"name": "vmeth0", "virtual_machine": vm, "mode": "access"}
        r = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "virtualization.vminterface", "entity": {"vm_interface": update}},
            format="json", **self.auth,
        )
        vm_updates = [c for c in r.json()["change_set"]["changes"]
                      if c["object_type"] == "virtualization.vminterface"]
        self.assertTrue(
            any(c.get("data", {}).get("tagged_vlans") == [] for c in vm_updates),
            "hook must emit tagged_vlans:[] for vminterface (DB-only assertion is vacuous here)",
        )

    def test_vminterface_create_drops_tagged_vlans_netbox_would_have_kept(self):
        """The uniform policy costs a vminterface its tagged VLANs, deliberately."""
        # virtualization.vminterface is the one registry entry whose serializer does NO
        # mode/VLAN validation: NetBox accepts mode "access" WITH tagged VLANs on a create
        # and stores them (BaseInterface.save() only clears on a later save, guarded by
        # `not self._state.adding`). The shared registry means the driver value wins here
        # too, so the tagged VLANs are dropped. Pinned so the cost stays a decision.
        vm = {"name": "drop-vm", "cluster": {"name": "drop-cl", "type": {"name": "drop-ct"}}}
        create = {"name": "vmeth1", "virtual_machine": vm, "mode": "access",
                  "tagged_vlans": [{"vid": 711, "name": "v711"}]}
        payload = {"timestamp": 1, "object_type": "virtualization.vminterface",
                   "entity": {"vm_interface": create}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        cs = r1.json()["change_set"]
        self.assertIn(
            "tagged_vlans", cs.get("warnings", {}).get("virtualization.vminterface", {})
        )
        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNone(r2.json().get("errors"))

        from virtualization.models import VMInterface
        vmi = VMInterface.objects.get(name="vmeth1")
        self.assertEqual(vmi.mode, "access")
        self.assertEqual(vmi.tagged_vlans.count(), 0)

    def test_interface_wireless_to_ethernet_clears_rf(self):
        """Type change wireless->ethernet clears stale rf_channel and rf_role."""
        from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
        site = Site.objects.create(name="rf-site", slug="rf-site")
        mfr = Manufacturer.objects.create(name="rf-mfr", slug="rf-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="rf-model", slug="rf-model")
        role = DeviceRole.objects.create(name="rf-role", slug="rf-role")
        dev = Device.objects.create(name="rf-dev", device_type=dt, role=role, site=site)
        iface = Interface.objects.create(device=dev, name="Wl0", type="ieee802.11ac")
        # Seed stale wireless fields via queryset.update() to bypass save()'s frequency/width
        # derivation. Use a valid WirelessChannelChoices value so field-level validation
        # accepts it; the forbidding rule keys on presence. rf_role is load-bearing: without
        # clearing it, Interface.clean() rejects the non-wireless interface with a wireless role.
        Interface.objects.filter(pk=iface.pk).update(rf_channel="2.4g-1-2412-22", rf_role="ap")
        update = {
            "name": "Wl0", "type": "1000base-t",
            "device": {"name": "rf-dev", "role": {"name": "rf-role"},
                       "device_type": {"manufacturer": {"name": "rf-mfr"}, "model": "rf-model"},
                       "site": {"name": "rf-site"}},
        }
        _, apply = self._diff_apply(update)
        self.assertEqual(apply.status_code, 200)
        self.assertIsNone(apply.json().get("errors"))
        iface.refresh_from_db()
        self.assertEqual(iface.type, "1000base-t")
        self.assertFalse(iface.rf_channel)  # stale rf_channel cleared
        self.assertFalse(iface.rf_role)     # stale rf_role cleared (Codex P2)

    def test_vlan_qinq_role_change_clears_qinq_svlan(self):
        """qinq_role customer->service clears the stale qinq_svlan (ORM-seed + netbox_id match)."""
        from ipam.models import VLAN
        svlan = VLAN.objects.create(vid=990, name="qinq-svc")
        cvlan = VLAN.objects.create(vid=991, name="qinq-cust", qinq_role="cvlan", qinq_svlan=svlan)
        update = {
            "vid": 991, "name": "qinq-cust", "qinq_role": "svlan",
            "metadata": {"source_match": {"netbox_id": cvlan.pk}},
        }
        payload = {"timestamp": 1, "object_type": "ipam.vlan", "entity": {"vlan": update}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(self.apply_url, data=r1.json().get("change_set", {}), format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNone(r2.json().get("errors"))
        cvlan.refresh_from_db()
        self.assertEqual(cvlan.qinq_role, "svlan")
        self.assertIsNone(cvlan.qinq_svlan)

    # --- N1: the type/rf_* clear converges instead of writing every cycle -----

    def test_type_change_rf_clear_converges_and_stops_writing(self):
        """A non-wireless type reported with rf_* fields clears once, then plans nothing."""
        # An interface exists as ieee802.11ac with rf_role/rf_channel set; the producer
        # now reports it as 1000base-t while STILL reporting the rf fields. Round 1 must
        # clear the stale values; every round after it must plan nothing. Blanking the
        # submitted value instead of dropping it made rounds 2+ diff the injected None
        # against the "" NetBox stores and emit a spurious UPDATE forever.
        _, a1 = self._diff_apply({
            "name": "Wl1", "type": "ieee802.11ac", "device": self._device(),
            "rf_role": "ap", "rf_channel": "2.4g-1-2412-22",
        })
        self.assertEqual(a1.status_code, 200)
        iface = Interface.objects.get(name="Wl1")
        self.assertEqual(iface.rf_role, "ap")
        self.assertIsNotNone(iface.rf_channel_frequency)  # derived by Interface.save()

        payload = self._iface_payload(
            name="Wl1", type="1000base-t", rf_role="ap", rf_channel="2.4g-1-2412-22",
        )
        cs = self._plan(payload)
        self.assertTrue([c for c in cs["changes"] if c["object_type"] == "dcim.interface"])
        self.assertEqual(
            sorted(cs.get("warnings", {}).get("dcim.interface", {})), ["rf_channel", "rf_role"],
        )
        self._apply(cs)
        iface.refresh_from_db()
        self.assertEqual(iface.type, "1000base-t")
        self.assertEqual(iface.rf_role, "")        # what NetBox stores for these CharFields
        self.assertEqual(iface.rf_channel, "")
        self.assertIsNone(iface.rf_channel_frequency)
        self.assertIsNone(iface.rf_channel_width)
        last_updated = iface.last_updated

        for quiet in range(1, 6):
            cs = self._plan(payload)
            self.assertEqual(cs.get("changes", []), [], f"re-planned on quiet round {quiet}")
            # still reported by the producer, still dropped, never written again
            self.assertEqual(
                sorted(cs.get("warnings", {}).get("dcim.interface", {})),
                ["rf_channel", "rf_role"],
            )
        iface.refresh_from_db()
        self.assertEqual(iface.last_updated, last_updated)  # no write after round 1

    # --- N3: a create that omits the driver field keeps its data --------------

    def test_vminterface_create_without_mode_keeps_the_vlans_netbox_stores(self):
        """A vminterface create omitting mode keeps its tagged VLANs, exactly as NetBox does."""
        # NetBox nulls untagged_vlan itself in BaseInterface.save() but KEEPS tagged_vlans
        # on a create (_state.adding is True), and the vminterface serializer validates
        # neither. Nothing may be dropped here: no mode was submitted, so no driver value
        # won anything, and the payload contradicted nothing.
        payload = self._vm_payload(
            name="vmeth0",
            untagged_vlan={"vid": 3601, "name": "v3601"},
            tagged_vlans=[{"vid": 3602, "name": "v3602"}],
        )
        cs = self._plan(payload)
        self.assertNotIn("virtualization.vminterface", cs.get("warnings", {}))
        self._apply(cs)

        from virtualization.models import VMInterface
        vmi = VMInterface.objects.get(name="vmeth0")
        self.assertFalse(vmi.mode)
        self.assertIsNone(vmi.untagged_vlan)                                # NetBox's own save()
        self.assertEqual([v.vid for v in vmi.tagged_vlans.all()], [3602])   # kept, not dropped

    def test_modeless_untagged_vlan_replans_exactly_as_on_develop(self):
        """A mode-less interface still reporting an untagged VLAN re-plans, as on develop."""
        # Documented residual, NOT introduced by the driver-field policy: NetBox nulls
        # untagged_vlan on EVERY save of a mode-less interface, so a producer that keeps
        # reporting one keeps planning an update. Closing it would mean dropping submitted
        # data with no submitted driver value to justify it, which is exactly the loss this
        # scoping exists to prevent, so develop's behaviour is kept and pinned here.
        payload = self._vm_payload(name="vmeth1", untagged_vlan={"vid": 3611, "name": "v3611"})
        self._apply(self._plan(payload))
        from virtualization.models import VMInterface
        vmi = VMInterface.objects.get(name="vmeth1")
        self.assertIsNone(vmi.untagged_vlan)
        vm_changes = [c for c in self._plan(payload).get("changes", [])
                      if c["object_type"] == "virtualization.vminterface"]
        self.assertTrue(vm_changes)  # re-plans; the policy deliberately does not silence it

    # --- N2: a dropped match criterion must be dropped BEFORE matching --------

    def test_a_contradictory_vlan_payload_never_touches_a_same_vid_row(self):
        """ipam.vlan is exempt from the drop, so a same-vid VLAN is left alone."""
        # The regression this pins: dropping qinq_svlan before matching made the entity
        # satisfy logical_vlan_vid_no_group_or_svlan_or_site ("vid where group IS NULL
        # AND qinq_svlan IS NULL AND site IS NULL"), i.e. vid alone -- so it adopted
        # whatever VLAN held that vid and renamed it. Measured: a seeded VLAN was
        # silently renamed and re-roled where develop wrote nothing at all.
        from ipam.models import VLAN
        seeded = VLAN.objects.create(
            vid=4091, name="lq-someone-elses-vlan", description="OWNED BY ANOTHER SOURCE",
        )
        payload = {"timestamp": 1, "object_type": "ipam.vlan", "entity": {"vlan": {
            "vid": 4091, "name": "lq-brand-new-svlan", "qinq_role": "svlan",
            "qinq_svlan": {"vid": 4090, "name": "lq-parent"},
        }}}
        cs = self._plan(payload)
        # nothing is dropped and nothing is warned about: the field identifies the row
        self.assertNotIn("ipam.vlan", cs.get("warnings", {}))

        # NetBox rejects the contradiction, which is exactly develop's answer
        r = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertNotEqual(r.status_code, 200, r.content)

        seeded.refresh_from_db()
        self.assertEqual(seeded.name, "lq-someone-elses-vlan")
        self.assertEqual(seeded.description, "OWNED BY ANOTHER SOURCE")
        self.assertFalse(seeded.qinq_role)  # '' or None; both mean 'not a Q-in-Q VLAN'
        self.assertEqual(VLAN.objects.filter(vid=4091).count(), 1)  # and no duplicate

    def test_vlan_customer_role_keeps_its_service_vlan_and_converges(self):
        """qinq_role 'cvlan' permits qinq_svlan: it is kept, applied and converges."""
        payload = {"timestamp": 1, "object_type": "ipam.vlan", "entity": {"vlan": {
            "vid": 3973, "name": "n2-cust", "qinq_role": "cvlan",
            "qinq_svlan": {"vid": 3972, "name": "n2-svc"},
        }}}
        cs = self._plan(payload)
        self.assertNotIn("ipam.vlan", cs.get("warnings", {}))
        self._apply(cs)
        from ipam.models import VLAN
        vlan = VLAN.objects.get(vid=3973)
        self.assertEqual(vlan.qinq_svlan.vid, 3972)   # allowed -> kept verbatim
        for quiet in range(1, 5):
            self.assertEqual(self._plan(payload).get("changes", []), [],
                             f"re-planned on quiet round {quiet}")

    # --- the whole mode rule map, end to end, over four quiet rounds ----------

    def test_interface_mode_matrix_applies_and_converges(self):
        """Every mode in the rule map applies with all three VLAN fields submitted, then converges."""
        modes = list(_DRIVER_FIELD_RULES["dcim.interface"]["mode"])
        self.assertEqual(len(modes), 5)
        for i, mode in enumerate(modes):
            with self.subTest(mode=mode):
                payload = self._iface_payload(
                    name=f"EthM{i}", type="1000base-t", mode=mode,
                    untagged_vlan={"vid": 3700 + i, "name": f"u{i}"},
                    tagged_vlans=[{"vid": 3710 + i, "name": f"t{i}"}],
                    qinq_svlan={"vid": 3720 + i, "name": f"s{i}"},
                )
                self._converge(payload, f"dcim.interface mode {mode!r}")
                iface = Interface.objects.get(name=f"EthM{i}")
                forbidden = _DRIVER_FIELD_RULES["dcim.interface"]["mode"][mode]
                if "tagged_vlans" in forbidden:
                    self.assertEqual(iface.tagged_vlans.count(), 0)
                else:
                    self.assertEqual([v.vid for v in iface.tagged_vlans.all()], [3710 + i])
                if "untagged_vlan" in forbidden:
                    self.assertIsNone(iface.untagged_vlan)
                else:
                    self.assertEqual(iface.untagged_vlan.vid, 3700 + i)
                if "qinq_svlan" in forbidden:
                    self.assertIsNone(iface.qinq_svlan)
                else:
                    self.assertEqual(iface.qinq_svlan.vid, 3720 + i)

    def test_vminterface_mode_matrix_applies_and_converges(self):
        """Every mode in the rule map converges for vminterface too, where no serializer polices it."""
        modes = list(_DRIVER_FIELD_RULES["virtualization.vminterface"]["mode"])
        for i, mode in enumerate(modes):
            with self.subTest(mode=mode):
                payload = self._vm_payload(
                    name=f"vmethM{i}", mode=mode,
                    untagged_vlan={"vid": 3730 + i, "name": f"vu{i}"},
                    tagged_vlans=[{"vid": 3740 + i, "name": f"vt{i}"}],
                    qinq_svlan={"vid": 3750 + i, "name": f"vs{i}"},
                )
                self._converge(payload, f"virtualization.vminterface mode {mode!r}")
                from virtualization.models import VMInterface
                vmi = VMInterface.objects.get(name=f"vmethM{i}")
                forbidden = _DRIVER_FIELD_RULES["virtualization.vminterface"]["mode"][mode]
                if "tagged_vlans" in forbidden:
                    self.assertEqual(vmi.tagged_vlans.count(), 0)
                if "qinq_svlan" in forbidden:
                    self.assertIsNone(vmi.qinq_svlan)

    def test_interface_type_rf_matrix_applies_and_converges(self):
        """Representative non-wireless types with all four rf_* fields submitted converge."""
        # The type rule map is generated as the complement of the wireless allow-set, so
        # every non-wireless value shares one code path; the exhaustive sweep over all of
        # them is the unit test above. These are the shapes a producer actually sends.
        for i, iface_type in enumerate(("1000base-t", "virtual", "lag")):
            with self.subTest(type=iface_type):
                payload = self._iface_payload(
                    name=f"EthT{i}", type=iface_type, rf_role="ap",
                    rf_channel="2.4g-1-2412-22", rf_channel_frequency=2412, rf_channel_width=22,
                )
                self._converge(payload, f"dcim.interface type {iface_type!r}")
                iface = Interface.objects.get(name=f"EthT{i}")
                self.assertEqual(iface.type, iface_type)
                # Measured: a field the payload never carries lands as NULL, while a
                # field submitted as null lands as '' (see the update case above) — the
                # SAME CharField, two empty representations. No single blank a planner
                # could write matches both, which is why the drop removes the field.
                self.assertFalse(iface.rf_role)
                self.assertFalse(iface.rf_channel)
                self.assertIsNone(iface.rf_channel_frequency)
                self.assertIsNone(iface.rf_channel_width)

    def test_wireless_type_keeps_all_four_rf_fields_and_converges(self):
        """A wireless type permits the rf fields: they are kept, applied and converge."""
        payload = self._iface_payload(
            name="EthW", type="ieee802.11ac", rf_role="ap", rf_channel="2.4g-1-2412-22",
        )
        cs = self._plan(payload)
        self.assertNotIn("dcim.interface", cs.get("warnings", {}))
        self._apply(cs)
        iface = Interface.objects.get(name="EthW")
        self.assertEqual(iface.rf_role, "ap")
        self.assertEqual(iface.rf_channel, "2.4g-1-2412-22")
        for quiet in range(1, 5):
            self.assertEqual(self._plan(payload).get("changes", []), [],
                             f"re-planned on quiet round {quiet}")
