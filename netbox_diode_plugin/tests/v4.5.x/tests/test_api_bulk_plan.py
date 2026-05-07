#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - BulkPlan API Tests."""

import logging
from types import SimpleNamespace
from unittest import mock

from dcim.models import Site
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


class BulkPlanTestCase(APITestCase):
    """BulkPlan endpoint test cases."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/bulk-plan/"

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

    def test_single_entity_create(self):
        """Test bulk plan with a single new entity."""
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
        cs = r.get("change_set", {})
        self.assertIsNotNone(cs.get("id"))
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "create")
        self.assertEqual(changes[0]["object_type"], "dcim.site")

    def test_single_entity_update(self):
        """Test bulk plan with a single existing entity that has changes."""
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
        self.assertEqual(len(results), 1)

        r = results[0]
        self.assertEqual(r["id"], "entity-1")
        self.assertIsNone(r.get("errors"))
        cs = r.get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "update")
        self.assertEqual(changes[0]["object_id"], self.site.pk)

    def test_single_entity_no_changes(self):
        """Test bulk plan with an entity that matches NetBox exactly — empty changes."""
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
        self.assertEqual(len(results), 1)

        r = results[0]
        self.assertEqual(r["id"], "entity-1")
        cs = r.get("change_set", {})
        changes = cs.get("changes", [])
        self.assertEqual(len(changes), 0)

    def test_multiple_entities_mixed(self):
        """Test bulk plan with multiple entities — one create, one update, one no-change."""
        payload = {
            "entities": [
                {
                    "id": "create-1",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": "Brand New Site", "slug": "brand-new-site"}},
                },
                {
                    "id": "update-1",
                    "object_type": "dcim.site",
                    "entity": {
                        "site": {
                            "name": "Existing Site",
                            "slug": "existing-site",
                            "comments": "changed",
                        }
                    },
                },
                {
                    "id": "noop-1",
                    "object_type": "dcim.site",
                    "entity": {
                        "site": {
                            "name": "Existing Site",
                            "slug": "existing-site",
                            "comments": "original comments",
                        }
                    },
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 3)

        ids = {r["id"]: r for r in results}

        create_r = ids["create-1"]
        self.assertIsNone(create_r.get("errors"))
        self.assertEqual(len(create_r["change_set"]["changes"]), 1)
        self.assertEqual(create_r["change_set"]["changes"][0]["change_type"], "create")

        update_r = ids["update-1"]
        self.assertIsNone(update_r.get("errors"))
        self.assertEqual(len(update_r["change_set"]["changes"]), 1)
        self.assertEqual(update_r["change_set"]["changes"][0]["change_type"], "update")

        noop_r = ids["noop-1"]
        self.assertEqual(len(noop_r["change_set"]["changes"]), 0)

    def test_entity_with_missing_entity_field(self):
        """Test that a missing entity field returns per-entity error without failing the batch."""
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

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 2)

        ids = {r["id"]: r for r in results}

        good = ids["good-1"]
        self.assertIsNone(good.get("errors"))
        self.assertIsNotNone(good.get("change_set"))

        bad = ids["bad-1"]
        self.assertIsNotNone(bad.get("errors"))
        self.assertIn("entity", bad["errors"].get("request", {}))

    def test_entity_with_missing_object_type(self):
        """Test that a missing object_type returns per-entity error."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "entity": {"site": {"name": "Some Site", "slug": "some-site"}},
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].get("errors"))
        self.assertIn("object_type", results[0]["errors"].get("request", {}))

    def test_entity_with_unsupported_object_type(self):
        """Test that an unsupported object_type returns per-entity error."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "object_type": "fake.model",
                    "entity": {"model": {"name": "test"}},
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].get("errors"))
        self.assertIn("object_type", results[0]["errors"].get("request", {}))

    def test_entity_with_invalid_object_type_format(self):
        """Test that an invalid object_type format returns per-entity error."""
        payload = {
            "entities": [
                {
                    "id": "bad-1",
                    "object_type": "nodotshere",
                    "entity": {"nodotshere": {"name": "test"}},
                },
            ]
        }

        response = self.send_request(payload)
        results = response.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].get("errors"))

    def test_empty_entities_list(self):
        """Test that an empty entities list returns 400."""
        payload = {"entities": []}
        self.send_request(payload, expected_status=status.HTTP_400_BAD_REQUEST)

    def test_missing_entities_field(self):
        """Test that a missing entities field returns 400."""
        payload = {"something_else": "value"}
        self.send_request(payload, expected_status=status.HTTP_400_BAD_REQUEST)

    def test_entities_not_a_list(self):
        """Test that entities as a non-list returns 400."""
        payload = {"entities": "not a list"}
        self.send_request(payload, expected_status=status.HTTP_400_BAD_REQUEST)

    def test_id_correlation(self):
        """Test that returned results preserve the entity IDs for correlation."""
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
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "aaa-111")
        self.assertEqual(results[1]["id"], "bbb-222")

    def test_entity_without_id(self):
        """Test that an entity without an id field gets null id in the result."""
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
