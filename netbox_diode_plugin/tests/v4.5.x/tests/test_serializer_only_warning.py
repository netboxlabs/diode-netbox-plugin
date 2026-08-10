"""E2E: serializer-only fields get a specific, non-fatal warning."""
from types import SimpleNamespace
from unittest import mock

from circuits.models import Circuit
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.transformer import SERIALIZER_ONLY_FIELD_WARNING
from netbox_diode_plugin.plugin_config import get_diode_user


class SerializerOnlyWarningE2ETests(APITestCase):
    """Ingesting a serializer-only field warns specifically and still applies."""

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

    def test_circuit_assignments_warns_specifically_and_applies(self):
        """Assignments is dropped with the specific message; the circuit lands."""
        payload = {
            "timestamp": 1,
            "object_type": "circuits.circuit",
            "entity": {"circuit": {
                "cid": "so-circ-1",
                "provider": {"name": "so-prov"},
                "type": {"name": "so-ctype"},
                "assignments": [{"id": 1}],
            }},
        }
        r1 = self.client.post(self.diff_url, data=payload, format="json", **self.auth)
        self.assertEqual(r1.status_code, 200)
        cs = r1.json().get("change_set", {})
        self.assertIn(SERIALIZER_ONLY_FIELD_WARNING, str(cs.get("warnings")))
        r2 = self.client.post(self.apply_url, data=cs, format="json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(Circuit.objects.filter(cid="so-circ-1").exists())
