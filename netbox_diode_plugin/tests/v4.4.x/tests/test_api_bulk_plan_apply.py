#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - BulkPlanApply API Tests."""

import logging
from types import SimpleNamespace
from unittest import mock

from dcim.models import Site
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

    def test_unauthenticated_request_returns_401(self):
        """Missing token returns 401."""
        response = self.client.post(self.url, data={"entities": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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
