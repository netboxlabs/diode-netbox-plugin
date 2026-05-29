#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - buffered_change_logging tests."""

import logging
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from core.models import ObjectChange
from dcim.models import Site
from django.db.models.signals import post_save
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import change_log_buffer, views
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


class BufferedChangeLoggingTestCase(APITestCase):
    """Exercise the buffered change-logging path end-to-end via /bulk-plan-apply/."""

    def setUp(self):
        """Auth + clean ObjectChange table for predictable counts."""
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

        # Wipe pre-existing ObjectChange rows so each test asserts on a
        # delta from zero rather than from whatever the fixture loader
        # has populated.
        ObjectChange.objects.all().delete()

    def tearDown(self):
        """Stop the auth patcher."""
        self.introspect_patcher.stop()
        super().tearDown()

    def _make_payload(self, suffix):
        """Build a single-entity bulk-plan-apply payload that creates a fresh Site."""
        return {
            "entities": [
                {
                    "id": f"entity-{suffix}",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": f"Site {suffix}", "slug": f"site-{suffix}"}},
                }
            ]
        }

    def _site_change_count(self):
        """Return the number of ObjectChange rows whose changed_object is a Site."""
        return ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="site",
        ).count()

    # --- Setting OFF: buffer is a pass-through ---

    def test_setting_false_writes_objectchange_normally(self):
        """With the setting off (default), the apply produces ObjectChange rows via the unbuffered path."""
        response = self.client.post(
            self.url, data=self._make_payload(uuid4().hex[:8]), format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(self._site_change_count(), 1)

    # --- Setting ON: buffer collects, bulk_create flushes ---

    def test_setting_true_persists_objectchange_via_bulk_create(self):
        """With the setting on, the apply still produces ObjectChange rows; they land via bulk_create on exit."""
        suffix = uuid4().hex[:8]
        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True):
            response = self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(self._site_change_count(), 1)

    def test_buffered_and_unbuffered_produce_equivalent_rows(self):
        """Row count, action, and changed_object are identical between buffered and unbuffered apply."""
        unbuffered_suffix = uuid4().hex[:8]
        response = self.client.post(
            self.url, data=self._make_payload(unbuffered_suffix), format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        unbuffered_rows = list(
            ObjectChange.objects.filter(
                changed_object_type__app_label="dcim",
                changed_object_type__model="site",
            ).order_by("pk").values("action", "changed_object_type_id")
        )

        ObjectChange.objects.all().delete()

        buffered_suffix = uuid4().hex[:8]
        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True):
            response = self.client.post(
                self.url, data=self._make_payload(buffered_suffix), format="json", **self.authorization_header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        buffered_rows = list(
            ObjectChange.objects.filter(
                changed_object_type__app_label="dcim",
                changed_object_type__model="site",
            ).order_by("pk").values("action", "changed_object_type_id")
        )

        self.assertEqual(unbuffered_rows, buffered_rows)

    # --- Rollback: buffered rows do NOT leak when apply raises ---

    def test_rollback_drops_buffered_objectchanges(self):
        """When apply_changeset raises mid-batch, no ObjectChange row is persisted."""
        suffix = uuid4().hex[:8]

        def failing_apply(change_set, request):
            # Save something so the buffer captures at least one event,
            # then raise to force the outer transaction to roll back.
            Site.objects.create(name=f"Doomed {suffix}", slug=f"doomed-{suffix}")
            raise RuntimeError("forced rollback")

        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(views, "apply_changeset", side_effect=failing_apply):
            # The apply path raises a non-ChangeSetException, which bubbles
            # up through the view and returns 500. The exact response code
            # is not what we are asserting on; we are asserting that the
            # transaction rolled back the would-be ObjectChange + Site.
            self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )

        self.assertFalse(Site.objects.filter(slug=f"doomed-{suffix}").exists())
        self.assertEqual(self._site_change_count(), 0)

    # --- post_save re-emit so dependent plugins still fire ---

    def test_post_save_signal_reemitted_for_each_flushed_row(self):
        """Receivers connected to post_save sender=ObjectChange see each flushed row exactly once."""
        suffix = uuid4().hex[:8]
        captured_pks = []

        def capture(sender, instance, created, **kwargs):
            captured_pks.append(instance.pk)

        post_save.connect(capture, sender=ObjectChange)
        try:
            with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True):
                response = self.client.post(
                    self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
                )
        finally:
            post_save.disconnect(capture, sender=ObjectChange)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        flushed_pks = list(
            ObjectChange.objects.filter(
                changed_object_type__app_label="dcim",
                changed_object_type__model="site",
            ).values_list("pk", flat=True)
        )
        self.assertEqual(len(flushed_pks), 1)
        self.assertIn(flushed_pks[0], captured_pks)

    # --- Bypass wins when both flags are enabled ---

    def test_bypass_takes_precedence_over_buffer_when_both_active(self):
        """When apply_bypass_change_logging is active in the same context, no ObjectChange row is produced."""
        suffix = uuid4().hex[:8]

        # Activate the bypass contextvar manually for the duration of
        # the request. (The plugin setting normally drives this via the
        # bypass_change_logging context manager; we shortcut here to
        # avoid having to flip module-level state mid-test.)
        bypass_token = change_log_buffer._bypass_active.set(True)
        try:
            with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True):
                response = self.client.post(
                    self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
                )
        finally:
            change_log_buffer._bypass_active.reset(bypass_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertTrue(Site.objects.filter(slug=f"site-{suffix}").exists())
        self.assertEqual(self._site_change_count(), 0)
