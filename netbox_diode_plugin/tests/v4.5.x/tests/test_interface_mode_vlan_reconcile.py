"""Unit tests for the interface mode/VLAN changeset normalizer."""
from types import SimpleNamespace
from unittest import mock

from dcim.models import Interface
from django.test import SimpleTestCase
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.differ import normalize_changeset
from netbox_diode_plugin.plugin_config import get_diode_user


class NormalizeChangesetTests(SimpleTestCase):
    """Pure-logic tests: no DB, operate on plain dicts."""

    def _run(self, object_type, prechange, entity):
        normalize_changeset(object_type, prechange, entity)
        return entity

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

    def test_explicit_value_is_respected_not_overwritten(self):
        """An explicitly submitted dependent field value is never overwritten."""
        # producer explicitly sent tagged_vlans with an incompatible mode -> leave it
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "access", "tagged_vlans": [200]}
        out = self._run("dcim.interface", prechange, entity)
        self.assertEqual(out["tagged_vlans"], [200])

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

    def test_create_no_prechange_is_noop(self):
        """Create flows (empty prechange, no existing row) are a no-op."""
        entity = {"mode": "access"}
        out = self._run("dcim.interface", {}, entity)
        self.assertNotIn("tagged_vlans", out)

    def test_unknown_mode_fails_open(self):
        """An unknown/unlisted mode value fails open and clears nothing."""
        prechange = {"mode": "tagged", "tagged_vlans": [101]}
        entity = {"mode": "future-mode"}
        out = self._run("dcim.interface", prechange, entity)
        self.assertNotIn("tagged_vlans", out)  # unknown mode -> clear nothing


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

    def test_explicit_incompatible_tagged_vlans_still_fails(self):
        """Guard: an explicit incompatible mode/tagged_vlans combo must still be rejected."""
        # Guard: a producer that explicitly sends mode=access WITH tagged_vlans is a
        # producer-side error — the normalizer must NOT silently clear it; apply must
        # fail and the DB must be unchanged.
        create = {
            "name": "EthNeg", "type": "1000base-t", "device": self._device(),
            "mode": "tagged",
            "tagged_vlans": [{"vid": 111, "name": "v111"}],
        }
        _, apply1 = self._diff_apply(create)
        self.assertEqual(apply1.status_code, 200)
        iface = Interface.objects.get(name="EthNeg")
        before = set(iface.tagged_vlans.values_list("vid", flat=True))

        bad = {
            "name": "EthNeg", "type": "1000base-t", "device": self._device(),
            "mode": "access",
            "tagged_vlans": [{"vid": 111, "name": "v111"}],  # explicit + incompatible
        }
        payload = {"timestamp": 1, "object_type": "dcim.interface", "entity": {"interface": bad}}
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        cs = r1.json().get("change_set", {})
        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        # NetBox rejects the incompatible combo: non-200, or 200 with an errors payload.
        self.assertTrue(r2.status_code != 200 or r2.json().get("errors"))
        iface.refresh_from_db()
        self.assertEqual(set(iface.tagged_vlans.values_list("vid", flat=True)), before)  # unchanged

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
