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
from django.test import TestCase
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import async_change_logging, change_log_buffer, views
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


class BufferedChangeLoggingApplyTestCase(APITestCase):
    """End-to-end behaviour of `buffered_change_logging` via `/bulk-plan-apply/`."""

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

    def test_setting_false_writes_objectchange_synchronously(self):
        """With the setting off (default), the apply writes ObjectChange synchronously, no RQ enqueue."""
        with mock.patch.object(change_log_buffer, "enqueue_async_write") as mock_enqueue:
            response = self.client.post(
                self.url,
                data=self._make_payload(uuid4().hex[:8]),
                format="json",
                **self.authorization_header,
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        # Synchronous path -> row already in DB at request return.
        self.assertEqual(self._site_change_count(), 1)
        # Async path -> NOT invoked.
        mock_enqueue.assert_not_called()

    # --- Setting ON: buffer collects + enqueues async job ---

    def test_setting_true_enqueues_async_job_with_payload(self):
        """With the setting on, the apply enqueues an RQ job carrying the buffered ObjectChange data."""
        suffix = uuid4().hex[:8]
        # Django's TestCase wraps each test in a transaction that is
        # rolled back at end-of-test, which means `transaction.on_commit`
        # callbacks normally never fire. `captureOnCommitCallbacks` runs
        # them explicitly on context exit so we can assert the enqueue.
        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(change_log_buffer, "enqueue_async_write") as mock_enqueue, \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

        # Apply transaction committed -> on_commit fired -> enqueue called exactly once.
        mock_enqueue.assert_called_once()
        payload = mock_enqueue.call_args.args[0]
        self.assertIn("rows", payload)
        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
        self.assertEqual(row["action"], "create")
        self.assertIn("changed_object_type_id", row)
        self.assertIn("changed_object_id", row)
        self.assertIn("postchange_data", row)
        self.assertIsNotNone(payload.get("request_id"))

    # --- Rollback: no enqueue on apply failure ---

    def test_rollback_does_not_enqueue(self):
        """If apply_changeset raises, transaction.on_commit never fires and the job is not enqueued."""
        suffix = uuid4().hex[:8]

        def failing_apply(change_set, request):
            Site.objects.create(name=f"Doomed {suffix}", slug=f"doomed-{suffix}")
            raise RuntimeError("forced rollback")

        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(views, "apply_changeset", side_effect=failing_apply), \
             mock.patch.object(change_log_buffer, "enqueue_async_write") as mock_enqueue:
            self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )

        # Outer atomic rolled back the Site, on_commit was discarded.
        self.assertFalse(Site.objects.filter(slug=f"doomed-{suffix}").exists())
        mock_enqueue.assert_not_called()

    # --- Bypass wins when both flags are enabled ---

    def test_bypass_takes_precedence_over_buffer_when_both_active(self):
        """When apply_bypass_change_logging is active, the buffer never collects and no enqueue happens."""
        suffix = uuid4().hex[:8]

        bypass_token = change_log_buffer._bypass_active.set(True)
        try:
            with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
                 mock.patch.object(change_log_buffer, "enqueue_async_write") as mock_enqueue:
                response = self.client.post(
                    self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
                )
        finally:
            change_log_buffer._bypass_active.reset(bypass_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        # Site was created (apply itself wasn't bypassed), but no ObjectChange and no enqueue.
        self.assertTrue(Site.objects.filter(slug=f"site-{suffix}").exists())
        self.assertEqual(self._site_change_count(), 0)
        mock_enqueue.assert_not_called()


class AsyncWorkerTestCase(TestCase):
    """Unit tests for the RQ worker entry point, invoked directly (no queue)."""

    def setUp(self):
        """Clean ObjectChange table for predictable counts."""
        ObjectChange.objects.all().delete()
        self.site = Site.objects.create(
            name="Worker test site",
            slug=f"worker-test-{uuid4().hex[:8]}",
        )
        self.content_type_id = self._site_content_type_id()

    def _site_content_type_id(self):
        from django.contrib.contenttypes.models import ContentType
        return ContentType.objects.get_for_model(Site).id

    def _build_payload(self, rows=None, **overrides):
        """Return a payload dict matching what serialise_buffer_to_payload produces."""
        if rows is None:
            rows = [
                {
                    "time": "2026-05-29T07:00:00+00:00",
                    "action": "create",
                    "changed_object_type_id": self.content_type_id,
                    "changed_object_id": self.site.pk,
                    "related_object_type_id": None,
                    "related_object_id": None,
                    "object_repr": str(self.site),
                    "prechange_data": None,
                    "postchange_data": {"name": self.site.name, "slug": self.site.slug},
                }
            ]
        payload = {
            "rows": rows,
            "user_id": None,
            "user_name": "diode",
            "request_id": str(uuid4()),
            "branch_schema_id": None,
        }
        payload.update(overrides)
        return payload

    def test_worker_writes_objectchange_rows_from_payload(self):
        """write_object_changes_async builds ObjectChange instances from payload and persists them."""
        payload = self._build_payload()
        async_change_logging.write_object_changes_async(payload)

        rows = ObjectChange.objects.filter(
            changed_object_type__app_label="dcim",
            changed_object_type__model="site",
            changed_object_id=self.site.pk,
        )
        self.assertEqual(rows.count(), 1)
        oc = rows.first()
        self.assertEqual(oc.action, "create")
        # `request_id` round-trips through UUIDField, so compare as string.
        self.assertEqual(str(oc.request_id), payload["request_id"])
        self.assertEqual(oc.object_repr, str(self.site))

    def test_worker_reemits_post_save_for_each_row(self):
        """Receivers connected to post_save sender=ObjectChange see each row exactly once."""
        captured_pks = []

        def capture(sender, instance, created, **kwargs):
            captured_pks.append(instance.pk)

        post_save.connect(capture, sender=ObjectChange)
        try:
            async_change_logging.write_object_changes_async(self._build_payload())
        finally:
            post_save.disconnect(capture, sender=ObjectChange)

        oc_pk = ObjectChange.objects.get(changed_object_id=self.site.pk).pk
        self.assertIn(oc_pk, captured_pks)

    def test_worker_no_op_on_empty_payload(self):
        """Empty rows -> no DB writes, no signals."""
        before = ObjectChange.objects.count()
        async_change_logging.write_object_changes_async(self._build_payload(rows=[]))
        self.assertEqual(ObjectChange.objects.count(), before)

    def test_worker_bulk_create_batches_rows(self):
        """Two rows in payload land via a single bulk_create call."""
        site_b = Site.objects.create(
            name="Worker test site B",
            slug=f"worker-test-b-{uuid4().hex[:8]}",
        )
        rows = [
            {
                "time": "2026-05-29T07:00:00+00:00",
                "action": "create",
                "changed_object_type_id": self.content_type_id,
                "changed_object_id": self.site.pk,
                "related_object_type_id": None,
                "related_object_id": None,
                "object_repr": str(self.site),
                "prechange_data": None,
                "postchange_data": {"name": self.site.name},
            },
            {
                "time": "2026-05-29T07:00:00+00:00",
                "action": "create",
                "changed_object_type_id": self.content_type_id,
                "changed_object_id": site_b.pk,
                "related_object_type_id": None,
                "related_object_id": None,
                "object_repr": str(site_b),
                "prechange_data": None,
                "postchange_data": {"name": site_b.name},
            },
        ]
        async_change_logging.write_object_changes_async(self._build_payload(rows=rows))
        self.assertEqual(
            ObjectChange.objects.filter(
                changed_object_id__in=[self.site.pk, site_b.pk]
            ).count(),
            2,
        )
