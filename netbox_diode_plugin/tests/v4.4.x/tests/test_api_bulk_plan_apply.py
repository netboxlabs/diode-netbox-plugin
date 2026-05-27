#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - BulkPlanApply API Tests."""

import logging
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from dcim.models import MACAddress, Site
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


class BulkPlanApplyTestCase(APITestCase):
    """BulkPlanApply endpoint test cases."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/bulk-plan-apply/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            "_introspect_token",
            return_value=self.diode_user,
        )
        self.introspect_patcher.start()

        self.site = Site.objects.create(
            name="Existing Site",
            slug="existing-site",
            comments="original comments",
        )

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def send_request(self, payload, expected_status=status.HTTP_200_OK):
        """Post the payload and return the response."""
        response = self.client.post(
            self.url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, expected_status)
        return response

    # --- Happy path: plan + apply both succeed ---

    def test_single_entity_create_applies(self):
        """Plan creates a new site and apply persists it to NetBox."""
        payload = {
            "entities": [
                {
                    "id": "entity-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "New Site", "slug": "new-site"}},
                }
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 1)

        r = results[0]
        self.assertEqual(r["id"], "entity-1")
        self.assertIsNone(r.get("errors"))
        cs = r.get("change_set")
        self.assertIsNotNone(cs)
        self.assertEqual(len(cs["changes"]), 1)
        self.assertEqual(cs["changes"][0]["change_type"], "create")
        # Site should exist after apply.
        self.assertTrue(Site.objects.filter(slug="new-site").exists())

    def test_single_entity_update_applies(self):
        """Plan updates an existing site and apply persists the change."""
        payload = {
            "entities": [
                {
                    "id": "entity-1",
                    "object_type": "dcim.site",
                    "entity": {
                        "site": {
                            "name": "Existing Site",
                            "slug": "existing-site",
                            "comments": "updated comments",
                        }
                    },
                }
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        r = results[0]
        self.assertIsNone(r.get("errors"))
        cs = r.get("change_set")
        self.assertEqual(cs["changes"][0]["change_type"], "update")
        self.site.refresh_from_db()
        self.assertEqual(self.site.comments, "updated comments")

    def test_single_entity_no_changes_skips_apply(self):
        """Entity matching NetBox exactly produces empty change_set; apply is skipped."""
        payload = {
            "entities": [
                {
                    "id": "entity-1",
                    "object_type": "dcim.site",
                    "entity": {
                        "site": {
                            "name": "Existing Site",
                            "slug": "existing-site",
                            "comments": "original comments",
                        }
                    },
                }
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        r = results[0]
        self.assertIsNone(r.get("errors"))
        cs = r.get("change_set")
        self.assertEqual(len(cs["changes"]), 0)

    # --- Plan failures short-circuit apply ---

    def test_plan_failure_missing_entity_field(self):
        """Missing entity field returns plan error per-entity; batch returns 207."""
        payload = {
            "entities": [
                {
                    "id": "good-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Good Site", "slug": "good-site"}},
                },
                {
                    "id": "bad-1",
                    "object_type": "dcim.site",
                    "entity": None,
                },
            ]
        }

        response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)
        results = response.json().get("results", [])
        ids = {r["id"]: r for r in results}

        good = ids["good-1"]
        self.assertIsNone(good.get("errors"))
        self.assertIsNotNone(good.get("change_set"))
        self.assertTrue(Site.objects.filter(slug="good-site").exists())

        bad = ids["bad-1"]
        self.assertIsNone(bad.get("change_set"))
        self.assertIsNotNone(bad.get("errors"))
        self.assertIn("plan", bad["errors"])
        self.assertNotIn("apply", bad["errors"])

    def test_plan_failure_unsupported_object_type(self):
        """Unsupported object_type returns plan error, apply is skipped."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "object_type": "fake.model",
                    "entity": {"model": {"name": "test"}},
                },
            ]
        }

        response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)
        r = response.json()["results"][0]
        self.assertIsNone(r.get("change_set"))
        self.assertIn("plan", r["errors"])
        self.assertIn("object_type", r["errors"]["plan"].get("request", {}))

    def test_plan_failure_invalid_object_type_format(self):
        """Invalid object_type format returns plan error."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "object_type": "nodotshere",
                    "entity": {"nodotshere": {"name": "test"}},
                },
            ]
        }

        response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)
        r = response.json()["results"][0]
        self.assertIsNone(r.get("change_set"))
        self.assertIn("plan", r["errors"])

    def test_plan_failure_missing_object_type(self):
        """Missing object_type returns plan error."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "entity": {"site": {"name": "Site", "slug": "site"}},
                },
            ]
        }

        response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)
        r = response.json()["results"][0]
        self.assertIn("plan", r["errors"])

    # --- Apply failures: change_set still returned, apply error reported ---

    def test_apply_failure_returns_change_set_and_apply_error(self):
        """When _apply_one_changeset returns errors, the change_set is still in the response."""
        from netbox_diode_plugin.api.common import ChangeSetResult

        payload = {
            "entities": [
                {
                    "id": "entity-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Apply Fail Site", "slug": "apply-fail-site"}},
                }
            ]
        }

        with mock.patch(
            "netbox_diode_plugin.api.views._apply_one_changeset",
            return_value=ChangeSetResult(
                id="cs-id",
                errors={"dcim.site": {"slug": ["already exists"]}},
            ),
        ):
            response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)

        r = response.json()["results"][0]
        self.assertIsNotNone(r.get("change_set"))
        self.assertIsNotNone(r.get("errors"))
        self.assertNotIn("plan", r["errors"])
        self.assertIn("apply", r["errors"])
        self.assertIn("dcim.site", r["errors"]["apply"])

    def test_mixed_batch_plan_ok_plan_fail_apply_fail(self):
        """Mixed batch: one ok, one plan-fail, one apply-fail. Order preserved, 207 returned."""
        from netbox_diode_plugin.api.common import ChangeSetResult

        payload = {
            "entities": [
                {
                    "id": "ok-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "OK Site", "slug": "ok-site"}},
                },
                {
                    "id": "plan-fail-1",
                    "object_type": "fake.model",
                    "entity": {"model": {"name": "fake"}},
                },
                {
                    "id": "apply-fail-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Apply Fail", "slug": "apply-fail"}},
                },
            ]
        }

        # Only fail the third entity's apply. The first uses the real applier.
        original_apply = None
        call_count = {"n": 0}

        def fake_apply(change_set, request):
            call_count["n"] += 1
            # First call (ok-1) — delegate to real applier.
            # Second call would be for apply-fail-1 (plan-fail-1 short-circuits).
            if call_count["n"] == 1:
                return original_apply(change_set, request)
            return ChangeSetResult(
                id=change_set.id,
                errors={"dcim.site": {"slug": ["already exists"]}},
            )

        # Capture the real _apply_one_changeset for the first call to delegate to.
        from netbox_diode_plugin.api import views as views_module
        original_apply = views_module._apply_one_changeset

        with mock.patch.object(views_module, "_apply_one_changeset", side_effect=fake_apply):
            response = self.send_request(payload, expected_status=status.HTTP_207_MULTI_STATUS)

        results = response.json().get("results", [])
        self.assertEqual(len(results), 3)
        ids = [r["id"] for r in results]
        self.assertEqual(ids, ["ok-1", "plan-fail-1", "apply-fail-1"])

        ok = results[0]
        self.assertIsNone(ok.get("errors"))
        self.assertIsNotNone(ok.get("change_set"))

        plan_fail = results[1]
        self.assertIsNone(plan_fail.get("change_set"))
        self.assertIn("plan", plan_fail["errors"])

        apply_fail = results[2]
        self.assertIsNotNone(apply_fail.get("change_set"))
        self.assertIn("apply", apply_fail["errors"])

    # --- Envelope validation ---

    def test_empty_entities_list(self):
        """Empty entities list returns 400."""
        self.send_request({"entities": []}, expected_status=status.HTTP_400_BAD_REQUEST)

    def test_missing_entities_field(self):
        """Missing entities field returns 400."""
        self.send_request({"something_else": "value"}, expected_status=status.HTTP_400_BAD_REQUEST)

    def test_entities_not_a_list(self):
        """Non-list entities returns 400."""
        self.send_request({"entities": "not a list"}, expected_status=status.HTTP_400_BAD_REQUEST)

    # --- ID correlation ---

    def test_id_correlation_preserved(self):
        """Result IDs match input IDs in order."""
        payload = {
            "entities": [
                {
                    "id": "aaa-111",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Site A", "slug": "site-a"}},
                },
                {
                    "id": "bbb-222",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Site B", "slug": "site-b"}},
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual([r["id"] for r in results], ["aaa-111", "bbb-222"])

    def test_entity_without_id(self):
        """Entity without an id field gets null id in result."""
        payload = {
            "entities": [
                {
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "No ID Site", "slug": "no-id-site"}},
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["id"])
        self.assertIsNone(results[0].get("errors"))

    # --- Auth/permissions ---

    def test_unauthenticated_request_returns_403(self):
        """Missing token returns 403 (DiodeOAuth2Authentication has no authenticate_header)."""
        response = self.client.post(self.url, data={"entities": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- Cross-request plan race must not produce duplicate MACAddress ---

    def test_concurrent_plans_dedupe_macaddress_via_pre_save_match(self):
        """
        Two requests planning the same MAC must apply to a single row.

        Two reconciler workers planning equivalent change_sets for the same
        interface + MAC each see no existing MAC row and each plan a CREATE.
        Two sequential ``/bulk-plan/`` calls model this exactly: each request
        has its own request-scoped obj_cache, so plan B cannot see plan A's
        pending CREATE.

        NetBox has no DB-level unique constraint on
        (mac_address, assigned_object_type, assigned_object_id), so the
        applier dedupes by routing dcim.macaddress through the find-first
        CREATE path (matcher.requires_pre_save_match). Apply B's CREATE
        therefore matches the row apply A just committed instead of
        inserting a second one.
        """
        suffix = uuid4().hex[:8]
        mac = "00:00:00:00:00:42"

        plan_payload = {
            "entities": [
                {
                    "id": f"race-{suffix}",
                    "object_type": "dcim.interface",
                    "entity": {
                        "interface": {
                            "name": f"eth0-{suffix}",
                            "type": "1000base-t",
                            "device": {
                                "name": f"dev-{suffix}",
                                "role": {"name": f"role-{suffix}"},
                                "site": {"name": f"site-{suffix}"},
                                "device_type": {
                                    "manufacturer": {"name": f"mfr-{suffix}"},
                                    "model": f"dt-{suffix}",
                                },
                            },
                            "primary_mac_address": {"mac_address": mac},
                        },
                    },
                }
            ]
        }

        plan_url = "/netbox/api/plugins/diode/bulk-plan/"
        apply_url = "/netbox/api/plugins/diode/bulk-apply/"

        plan_a = self.client.post(
            plan_url, data=plan_payload, format="json", **self.authorization_header
        )
        plan_b = self.client.post(
            plan_url, data=plan_payload, format="json", **self.authorization_header
        )
        self.assertEqual(plan_a.status_code, status.HTTP_200_OK, plan_a.json())
        self.assertEqual(plan_b.status_code, status.HTTP_200_OK, plan_b.json())

        result_a = plan_a.json()["results"][0]
        result_b = plan_b.json()["results"][0]
        self.assertIsNone(result_a.get("errors"), result_a)
        self.assertIsNone(result_b.get("errors"), result_b)
        cs_a = result_a.get("change_set")
        cs_b = result_b.get("change_set")
        self.assertIsNotNone(cs_a, result_a)
        self.assertIsNotNone(cs_b, result_b)

        def mac_creates(change_set):
            return [
                c for c in change_set["changes"]
                if c["object_type"] == "dcim.macaddress" and c["change_type"] == "create"
            ]

        self.assertEqual(len(mac_creates(cs_a)), 1, cs_a)
        self.assertEqual(len(mac_creates(cs_b)), 1, cs_b)

        apply_a = self.client.post(
            apply_url,
            data={"change_sets": [cs_a]},
            format="json",
            **self.authorization_header,
        )
        apply_b = self.client.post(
            apply_url,
            data={"change_sets": [cs_b]},
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(apply_a.status_code, status.HTTP_200_OK, apply_a.json())
        self.assertEqual(apply_b.status_code, status.HTTP_200_OK, apply_b.json())

        macs = MACAddress.objects.filter(mac_address=mac)
        self.assertEqual(
            macs.count(),
            1,
            f"expected exactly one MAC row after dedup, got {macs.count()}: "
            f"{list(macs.values('pk', 'mac_address', 'assigned_object_id'))}",
        )

    def test_insufficient_scope_returns_403(self):
        """Token with only read scope cannot call bulk-plan-apply (requires write)."""
        read_only_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read"],
            token_data={"scope": "netbox:read"},
        )
        with mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=read_only_user
        ):
            response = self.client.post(
                self.url,
                data={"entities": [{"id": "x", "object_type": "dcim.site",
                                    "entity": {"site": {"name": "x", "slug": "x"}}}]},
                format="json",
                **self.authorization_header,
            )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
