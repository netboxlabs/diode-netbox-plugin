"""E2E: moduletype attributes ingest, apply, and re-diff convergence."""
import json
from types import SimpleNamespace
from unittest import mock

from dcim.models import Manufacturer, ModuleType, ModuleTypeProfile
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class ModuleTypeAttributesE2ETests(APITestCase):
    """Aliased 'attributes' must ingest, persist raw, and re-diff as NOOP."""

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

        mfr = Manufacturer.objects.create(name="attr-mfr", slug="attr-mfr")
        profile = ModuleTypeProfile.objects.create(
            name="attr-profile",
            schema={"properties": {"ram": {"type": "integer", "title": "RAM (GB)"}}},
        )
        self.mt = ModuleType.objects.create(
            manufacturer=mfr, model="attr-mt", profile=profile,
            attribute_data={"ram": 32},
        )

    def _payload(self, attributes):
        # the wire carries JSON-blob fields (like every entry in
        # _FORMAT_TRANSFORMATIONS keyed to parse_json) as an encoded string
        return {
            "timestamp": 1,
            "object_type": "dcim.moduletype",
            "entity": {"module_type": {
                "manufacturer": {"name": "attr-mfr"},
                "model": "attr-mt",
                "attributes": json.dumps(attributes),
            }},
        }

    def test_update_persists_raw_and_rediff_is_noop(self):
        """Update attribute_data via the wire alias, then converge to NOOP."""
        r1 = self.client.post(
            self.diff_url, data=self._payload({"ram": 64}), format="json", **self.auth
        )
        self.assertEqual(r1.status_code, 200)
        cs = r1.json().get("change_set", {})
        # the aliased field must not be warned away
        self.assertNotIn("attributes", str(cs.get("warnings")))
        updates = [c for c in cs.get("changes", [])
                   if c["object_type"] == "dcim.moduletype" and c["change_type"] == "update"]
        self.assertTrue(any(c.get("data", {}).get("attributes") == {"ram": 64} for c in updates))

        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNone(r2.json().get("errors"))
        self.mt.refresh_from_db()
        self.assertEqual(self.mt.attribute_data, {"ram": 64})  # raw keys, not titled

        # convergence: identical payload must re-diff as NOOP
        r3 = self.client.post(
            self.diff_url, data=self._payload({"ram": 64}), format="json", **self.auth
        )
        self.assertEqual(r3.status_code, 200)
        changes = [c for c in r3.json().get("change_set", {}).get("changes", [])
                   if c["object_type"] == "dcim.moduletype"]
        self.assertTrue(all(c["change_type"] == "noop" for c in changes), changes)
