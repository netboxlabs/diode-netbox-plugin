#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - PR 3 deferred changelog tests."""

import uuid

from core.models import ObjectChange
from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from netbox.context import current_request

from netbox_diode_plugin.api.deferred_changelog import deferred_changelog
from netbox_diode_plugin.plugin_config import get_diode_user


def _make_request():
    """Build a minimal request with a fresh request_id, attached to current_request."""
    rf = RequestFactory()
    req = rf.post("/x")
    req.id = uuid.uuid4()
    req.user = get_diode_user()
    return req


class DeferredChangelogTestCase(TestCase):
    """Verifies deferred_changelog (PR 3)."""

    def test_n_changes_one_bulk_insert(self):
        """N saves inside the context produce ONE bulk INSERT, not N."""
        req = _make_request()
        token = current_request.set(req)
        try:
            ObjectChange.objects.all().delete()
            with CaptureQueriesContext(connection) as ctx, deferred_changelog():
                for i in range(5):
                    Site.objects.create(name=f"S-{i}", slug=f"s-{i}")

            insert_stmts = [
                q for q in ctx.captured_queries
                if "INSERT INTO" in q["sql"] and '"core_objectchange"' in q["sql"]
            ]
            assert len(insert_stmts) == 1, [q["sql"] for q in insert_stmts]
            assert ObjectChange.objects.count() == 5
        finally:
            current_request.reset(token)

    def test_audit_row_content_matches_baseline(self):
        """Buffered audit rows carry the same fields as the per-save baseline."""
        req = _make_request()
        token = current_request.set(req)
        try:
            ObjectChange.objects.all().delete()
            with deferred_changelog():
                Site.objects.create(name="Deferred S", slug="deferred-s")
            deferred_row = ObjectChange.objects.get(object_repr="Deferred S")

            ObjectChange.objects.all().delete()
            req2 = _make_request()
            token2 = current_request.set(req2)
            try:
                Site.objects.create(name="Baseline S", slug="baseline-s")
            finally:
                current_request.reset(token2)
            baseline_row = ObjectChange.objects.get(object_repr="Baseline S")

            for field in ("action", "user_name", "changed_object_type_id",
                          "postchange_data"):
                assert getattr(deferred_row, field) == getattr(baseline_row, field), field
        finally:
            current_request.reset(token)

    def test_nested_context_is_noop(self):
        """An inner deferred_changelog must not flush — outer wins."""
        req = _make_request()
        token = current_request.set(req)
        try:
            ObjectChange.objects.all().delete()
            with deferred_changelog():
                with deferred_changelog():
                    Site.objects.create(name="N-1", slug="n-1")
                # If inner had flushed, this row would already exist.
                assert ObjectChange.objects.filter(object_repr="N-1").count() == 0
                Site.objects.create(name="N-2", slug="n-2")

            assert ObjectChange.objects.filter(object_repr__in=["N-1", "N-2"]).count() == 2
        finally:
            current_request.reset(token)

    def test_m2m_merge_dedup_single_row(self):
        """An UPDATE that re-fires via m2m_changed merges into one buffered row."""
        from extras.models import Tag

        tag = Tag.objects.create(name="net", slug="net")
        site = Site.objects.create(name="MM", slug="mm")  # outside the window

        req = _make_request()
        token = current_request.set(req)
        try:
            ObjectChange.objects.all().delete()
            with deferred_changelog():
                site.description = "updated"
                site.save()  # post_save → ObjectChange #1
                site.tags.set([tag])  # m2m_changed → would normally update the prior row

            ct = ContentType.objects.get_for_model(Site)
            rows = list(ObjectChange.objects.filter(
                changed_object_type=ct,
                changed_object_id=site.pk,
                request_id=req.id,
            ))
            assert len(rows) == 1, [r.postchange_data for r in rows]
            assert rows[0].postchange_data.get("tags") == [tag.id]
        finally:
            current_request.reset(token)
