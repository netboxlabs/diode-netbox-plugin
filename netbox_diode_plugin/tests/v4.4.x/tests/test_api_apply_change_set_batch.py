#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - ApplyChangeSetBatch Tests."""

import uuid

from dcim.models import Site
from rest_framework import status

from .test_api_apply_change_set import BaseApplyChangeSet


class ApplyChangeSetBatchTestCase(BaseApplyChangeSet):
    """ApplyChangeSetBatch test cases."""

    def setUp(self):
        """Set up test."""
        super().setUp()
        self.batch_url = "/netbox/api/plugins/diode/apply-change-set-batch/"
        # BaseApplyChangeSet creates fixtures with hardcoded ids (Site id=10, id=20)
        # via bulk_create, which does not advance the Postgres PK sequence.
        # Auto-allocating CREATE INSERTs in this test class would otherwise
        # collide with those fixture ids once the sequence catches up.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('dcim_site', 'id'), 10000, false)"
            )

    def _changeset_create_site(self, name, slug):
        return {
            "id": str(uuid.uuid4()),
            "changes": [
                {
                    "change_id": str(uuid.uuid4()),
                    "change_type": "create",
                    "object_version": None,
                    "object_type": "dcim.site",
                    "object_id": None,
                    "ref_id": "1",
                    "data": {
                        "name": name,
                        "slug": slug,
                        "facility": "Alpha",
                        "description": "",
                        "physical_address": "1 Fake St",
                        "shipping_address": "1 Fake St",
                        "comments": "",
                        "asns": [self.asns[0].pk],
                    },
                },
            ],
        }

    def _changeset_create_site_bad(self, name, slug):
        cs = self._changeset_create_site(name, slug)
        # asns must be a list — pass an int to trigger DRF ValidationError
        cs["changes"][0]["data"]["asns"] = 1
        return cs

    def _post_batch(self, payload, status_code=status.HTTP_200_OK):
        response = self.client.post(
            self.batch_url,
            data=payload,
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status_code)
        return response

    def test_batch_three_changesets_all_created(self):
        """All three changesets in a batch create successfully."""
        payload = {
            "change_sets": [
                self._changeset_create_site("Batch Site A", "batch-site-a"),
                self._changeset_create_site("Batch Site B", "batch-site-b"),
                self._changeset_create_site("Batch Site C", "batch-site-c"),
            ],
        }

        response = self._post_batch(payload)
        results = response.json()["results"]
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertIsNone(r.get("errors"))

        for slug in ("batch-site-a", "batch-site-b", "batch-site-c"):
            self.assertTrue(Site.objects.filter(slug=slug).exists())

    def test_batch_savepoint_isolation_bad_middle(self):
        """A bad changeset in the middle does not roll back the others."""
        payload = {
            "change_sets": [
                self._changeset_create_site("Iso Site A", "iso-site-a"),
                self._changeset_create_site_bad("Iso Site B", "iso-site-b"),
                self._changeset_create_site("Iso Site C", "iso-site-c"),
            ],
        }

        response = self._post_batch(payload, status_code=status.HTTP_207_MULTI_STATUS)
        results = response.json()["results"]
        self.assertEqual(len(results), 3)
        self.assertIsNone(results[0].get("errors"))
        self.assertIsNotNone(results[1].get("errors"))
        self.assertIsNone(results[2].get("errors"))

        self.assertTrue(Site.objects.filter(slug="iso-site-a").exists())
        self.assertFalse(Site.objects.filter(slug="iso-site-b").exists())
        self.assertTrue(Site.objects.filter(slug="iso-site-c").exists())

    def test_batch_single_changeset_matches_old_endpoint(self):
        """A 1-element batch produces the same effect as the per-changeset endpoint."""
        payload = {
            "change_sets": [
                self._changeset_create_site("Single Site", "single-site"),
            ],
        }

        response = self._post_batch(payload)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].get("errors"))
        self.assertTrue(Site.objects.filter(slug="single-site").exists())

    def test_batch_empty_returns_400(self):
        """Empty change_sets list returns 400."""
        self._post_batch({"change_sets": []}, status_code=status.HTTP_400_BAD_REQUEST)

    def test_batch_missing_change_sets_returns_400(self):
        """Missing change_sets key returns 400."""
        self._post_batch({}, status_code=status.HTTP_400_BAD_REQUEST)

    def test_batch_change_sets_not_a_list_returns_400(self):
        """change_sets not a list returns 400."""
        self._post_batch(
            {"change_sets": {"not": "a list"}},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_change_type_from_dict_normalises_and_round_trips(self):
        """from_dict normalises change_type to enum; to_dict round-trips back to string."""
        from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType

        change = Change.from_dict({
            "change_type": "create",
            "object_type": "dcim.site",
            "data": {"name": "x", "slug": "x"},
        })
        self.assertEqual(change.change_type, ChangeType.CREATE)

        cs = ChangeSet.from_dict({
            "id": str(uuid.uuid4()),
            "changes": [{"change_type": "update", "object_type": "dcim.site"}],
        })
        self.assertEqual(cs.changes[0].change_type, ChangeType.UPDATE)
        self.assertEqual(cs.changes[0].to_dict()["change_type"], "update")

        bad = Change.from_dict({"change_type": "delete", "object_type": "dcim.site"})
        self.assertEqual(bad.change_type, "delete")

    def test_change_set_from_dict_carries_branch_and_warnings(self):
        """ChangeSet.from_dict picks up branch and warnings, and to_dict round-trips them."""
        from netbox_diode_plugin.api.common import ChangeSet

        cs = ChangeSet.from_dict({
            "id": str(uuid.uuid4()),
            "changes": [],
            "branch": {"id": "abc", "name": "feature/x"},
            "warnings": {"some_warning": ["foo"]},
        })
        self.assertEqual(cs.branch, {"id": "abc", "name": "feature/x"})
        self.assertEqual(cs.warnings, {"some_warning": ["foo"]})
        d = cs.to_dict()
        self.assertEqual(d["branch"], {"id": "abc", "name": "feature/x"})
        self.assertEqual(d["warnings"], {"some_warning": ["foo"]})

        cs_empty = ChangeSet.from_dict({"id": str(uuid.uuid4()), "changes": []})
        self.assertIsNone(cs_empty.branch)
        self.assertIsNone(cs_empty.warnings)

    def test_batch_malformed_per_entry_yields_207(self):
        """A batch entry with non-list ``changes`` yields a per-item error, not a 500."""
        payload = {
            "change_sets": [
                self._changeset_create_site("Batch OK", "batch-ok"),
                {"id": str(uuid.uuid4()), "changes": "not-a-list"},
            ],
        }
        response = self._post_batch(payload, status_code=status.HTTP_207_MULTI_STATUS)
        results = response.json()["results"]
        self.assertEqual(len(results), 2)
        self.assertIsNone(results[0].get("errors"))
        self.assertIsNotNone(results[1].get("errors"))
        self.assertTrue(Site.objects.filter(slug="batch-ok").exists())

