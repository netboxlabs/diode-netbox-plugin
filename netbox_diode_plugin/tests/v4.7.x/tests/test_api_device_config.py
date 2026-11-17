#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Diode NetBox Plugin - device_config_handler tests.

A Device entity may carry ``config`` (DeviceConfig). NetBox has no field for it; when
``device_config_handler`` names a callable, bulk-plan-apply hands the decoded config
to it once the device row is known, whether the device was created, updated or
matched unchanged.
"""

import base64
from types import SimpleNamespace
from unittest import mock

from dcim.models import Device
from django.conf import settings
from django.test import override_settings
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import device_config
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType
from netbox_diode_plugin.plugin_config import get_diode_user

# The handler under test records what it was given.
CALLS: list[dict] = []


def recording_handler(device, **kwargs):
    """Reference-shaped handler that records its arguments."""
    CALLS.append({"device": device, **kwargs})


def failing_handler(device, **kwargs):
    """A handler that raises; the apply must survive it."""
    raise RuntimeError("store unavailable")


HANDLER = f"{__name__}.recording_handler"
FAILING = f"{__name__}.failing_handler"


def _plugin_settings(**overrides):
    merged = {**settings.PLUGINS_CONFIG}
    merged["netbox_diode_plugin"] = {**merged.get("netbox_diode_plugin", {}), **overrides}
    return override_settings(PLUGINS_CONFIG=merged)


class ExtractDeviceConfigTestCase(APITestCase):
    """Pure helpers."""

    def test_extract_decodes_base64_and_removes_the_field(self):
        """Base64 blobs are decoded and ``config`` leaves the entity."""
        running = b"hostname r1\n"
        entity = {"name": "r1", "config": {"running": base64.b64encode(running).decode(), "metadata": {"a": 1}}}
        out = device_config.extract_device_config(entity)
        self.assertEqual(out["running"], running)
        self.assertIsNone(out["startup"])
        self.assertEqual(out["metadata"], {"a": 1})
        self.assertNotIn("config", entity)

    def test_extract_accepts_plain_text_and_empty(self):
        """Plain text is accepted; empty or absent config yields None."""
        self.assertEqual(
            device_config.extract_device_config({"config": {"running": "hostname r1"}})["running"],
            b"hostname r1",
        )
        self.assertIsNone(device_config.extract_device_config({"config": {"running": ""}}))
        self.assertIsNone(device_config.extract_device_config({"name": "r1"}))

    def test_resolve_device_id_prefers_change_then_root(self):
        """A device change's id wins; an empty plan falls back to the matched root."""
        cs = ChangeSet(changes=[Change(change_type=ChangeType.UPDATE, object_type="dcim.device", object_id=7)])
        self.assertEqual(device_config.resolve_device_id(cs), 7)
        empty = ChangeSet(changes=[])
        self.assertIsNone(device_config.resolve_device_id(empty))
        empty.root_object_id = 9
        self.assertEqual(device_config.resolve_device_id(empty), 9)
        self.assertIsNone(device_config.resolve_device_id(None))


class DeviceConfigHandlerAPITestCase(APITestCase):
    """bulk-plan-apply hands a device's config to the configured handler."""

    def setUp(self):
        """Mock the OAuth introspection and reset the recorded calls."""
        # Honour BASE_PATH so the test runs in the plugin's compose (netbox/) and elsewhere.
        self.url = f"/{settings.BASE_PATH}api/plugins/diode/bulk-plan-apply/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user)
        self.introspect_patcher.start()
        CALLS.clear()

    def tearDown(self):
        """Stop the introspection mock."""
        self.introspect_patcher.stop()
        super().tearDown()

    @staticmethod
    def _device_entity(name, running, startup=None):
        config = {"running": base64.b64encode(running).decode(), "metadata": {"collected_at": "2026-11-17T00:00:00Z"}}
        if startup is not None:
            config["startup"] = base64.b64encode(startup).decode()
        return {
            "id": f"e-{name}",
            "object_type": "dcim.device",
            "entity": {
                "device": {
                    "name": name,
                    "device_type": {"model": "Model X", "manufacturer": {"name": "Acme"}},
                    "role": {"name": "Leaf"},
                    "site": {"name": "Site A"},
                    "status": "active",
                    "config": config,
                }
            },
        }

    def _post(self, entity, expected=status.HTTP_200_OK):
        response = self.client.post(self.url, data={"entities": [entity]}, format="json", **self.authorization_header)
        self.assertEqual(response.status_code, expected, response.content)
        return response.json()["results"][0]

    def test_created_device_config_is_handed_over(self):
        """A created device's config reaches the handler with its metadata."""
        with _plugin_settings(device_config_handler=HANDLER):
            result = self._post(self._device_entity("leaf1", b"hostname leaf1\n", b"hostname leaf1\n"))
        self.assertIsNone(result["errors"])
        self.assertNotIn("warnings", result)
        device = Device.objects.get(name="leaf1")
        self.assertEqual(len(CALLS), 1)
        call = CALLS[0]
        self.assertEqual(call["device"], device)
        self.assertEqual(call["running"], b"hostname leaf1\n")
        self.assertEqual(call["startup"], b"hostname leaf1\n")
        self.assertIsNone(call["candidate"])
        self.assertEqual(call["metadata"], {"collected_at": "2026-11-17T00:00:00Z"})
        self.assertEqual(call["entity_id"], "e-leaf1")

    def test_unchanged_device_still_hands_over_config(self):
        """The steady-state case: NetBox attributes unchanged, config present."""
        with _plugin_settings(device_config_handler=HANDLER):
            self._post(self._device_entity("leaf2", b"v1\n"))
            result = self._post(self._device_entity("leaf2", b"v2\n"))
        self.assertEqual(result["change_set"]["changes"], [])
        self.assertNotIn("warnings", result)
        self.assertEqual([c["running"] for c in CALLS], [b"v1\n", b"v2\n"])
        self.assertEqual(CALLS[1]["device"], Device.objects.get(name="leaf2"))

    def test_handler_failure_is_a_warning_not_an_error(self):
        """A raising handler produces a warning; the device is still applied."""
        with _plugin_settings(device_config_handler=FAILING):
            result = self._post(self._device_entity("leaf3", b"hostname leaf3\n"))
        self.assertIsNone(result["errors"])
        self.assertTrue(Device.objects.filter(name="leaf3").exists())
        self.assertIn("store unavailable", result["warnings"][0]["device_config"])

    def test_plan_only_hands_over_config_for_an_existing_device(self):
        """A reviewed deviation: bulk-plan alone delivers an existing device's config."""
        plan_url = f"/{settings.BASE_PATH}api/plugins/diode/bulk-plan/"
        with _plugin_settings(device_config_handler=HANDLER):
            self._post(self._device_entity("leaf5", b"v1\n"))  # creates the device
            CALLS.clear()
            response = self.client.post(
                plan_url,
                data={"entities": [self._device_entity("leaf5", b"v2\n")]},
                format="json",
                **self.authorization_header,
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        result = response.json()["results"][0]
        self.assertEqual(result["change_set"]["changes"], [])
        self.assertNotIn("warnings", result)
        self.assertEqual([c["running"] for c in CALLS], [b"v2\n"])
        self.assertEqual(CALLS[0]["device"], Device.objects.get(name="leaf5"))

    def test_plan_only_defers_config_for_a_new_device(self):
        """A new device under review has no row yet: nothing is stored and nothing warns."""
        plan_url = f"/{settings.BASE_PATH}api/plugins/diode/bulk-plan/"
        with _plugin_settings(device_config_handler=HANDLER):
            response = self.client.post(
                plan_url,
                data={"entities": [self._device_entity("leaf6", b"v1\n")]},
                format="json",
                **self.authorization_header,
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        result = response.json()["results"][0]
        self.assertTrue(any(c["object_type"] == "dcim.device" for c in result["change_set"]["changes"]))
        self.assertNotIn("warnings", result)
        self.assertEqual(CALLS, [])
        self.assertFalse(Device.objects.filter(name="leaf6").exists())

    def test_without_handler_the_apply_is_unchanged(self):
        """No handler configured: the apply behaves as before and nothing is called."""
        with _plugin_settings(device_config_handler=None):
            result = self._post(self._device_entity("leaf4", b"hostname leaf4\n"))
        self.assertIsNone(result["errors"])
        self.assertTrue(Device.objects.filter(name="leaf4").exists())
        self.assertEqual(CALLS, [])
