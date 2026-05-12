#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - PR 4 bulk TaggedItem tests."""

import uuid
from types import SimpleNamespace

from core.models import ObjectChange
from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from extras.models import Tag, TaggedItem
from netbox.context import current_request, events_queue

from netbox_diode_plugin.api.bulk_tags import apply_tags_bulk
from netbox_diode_plugin.api.deferred_changelog import deferred_changelog
from netbox_diode_plugin.plugin_config import get_diode_user


def _make_request():
    rf = RequestFactory()
    req = rf.post("/x")
    req.id = uuid.uuid4()
    req.user = get_diode_user()
    return req


class BulkTaggedItemTestCase(TestCase):
    """Verifies apply_tags_bulk (PR 4)."""

    def setUp(self):
        """Create K distinct tags + N untagged sites."""
        self.tags = [Tag.objects.create(name=f"t{i}", slug=f"t{i}") for i in range(3)]
        self.sites = [Site.objects.create(name=f"S{i}", slug=f"s{i}") for i in range(4)]

    def test_n_objects_one_bulk_insert(self):
        """N tagged objects produce 1 INSERT INTO extras_taggeditem (modulo batches)."""
        req = _make_request()
        # Mimic preload contract from PR 2.
        req._diode_preload = {
            "tag_ids_by_slug": {t.slug: t.id for t in self.tags},
        }
        token = current_request.set(req)
        try:
            pairs = [(s, [t.slug for t in self.tags]) for s in self.sites]
            with CaptureQueriesContext(connection) as ctx:
                apply_tags_bulk(pairs, req)
            inserts = [
                q for q in ctx.captured_queries
                if "INSERT INTO" in q["sql"] and '"extras_taggeditem"' in q["sql"]
            ]
            assert len(inserts) == 1, [q["sql"] for q in inserts]
        finally:
            current_request.reset(token)

    def test_tag_set_per_object_matches_baseline(self):
        """Final tag set per instance equals what `instance.tags.set([...])` would produce."""
        req = _make_request()
        req._diode_preload = {
            "tag_ids_by_slug": {t.slug: t.id for t in self.tags},
        }
        token = current_request.set(req)
        try:
            pairs = [(self.sites[0], ["t0", "t1"]), (self.sites[1], ["t2"])]
            apply_tags_bulk(pairs, req)
            ct = ContentType.objects.get_for_model(Site)

            for site, expected_slugs in [(self.sites[0], {"t0", "t1"}), (self.sites[1], {"t2"})]:
                got = set(
                    Tag.objects.filter(
                        id__in=TaggedItem.objects.filter(
                            content_type=ct, object_id=site.pk
                        ).values_list("tag_id", flat=True)
                    ).values_list("slug", flat=True)
                )
                assert got == expected_slugs, (site, got, expected_slugs)
        finally:
            current_request.reset(token)

    def test_no_extra_audit_row_per_tagged_object(self):
        """N audit rows for N tagged-only updates — no doubled rows from m2m re-fire."""
        req = _make_request()
        req._diode_preload = {
            "tag_ids_by_slug": {t.slug: t.id for t in self.tags},
        }
        token = current_request.set(req)
        try:
            ObjectChange.objects.all().delete()
            with deferred_changelog():
                # Touch each instance once via a normal save, then bulk-tag.
                for s in self.sites:
                    s.description = "touched"
                    s.save()
                apply_tags_bulk([(s, ["t0"]) for s in self.sites], req)

            ct = ContentType.objects.get_for_model(Site)
            row_count = ObjectChange.objects.filter(
                changed_object_type=ct,
                changed_object_id__in=[s.pk for s in self.sites],
            ).count()
            assert row_count == len(self.sites), row_count
        finally:
            current_request.reset(token)

    def test_events_queue_populated_with_post_tag_state(self):
        """events_queue has one entry per tagged instance, with key matching baseline format."""
        req = _make_request()
        req._diode_preload = {
            "tag_ids_by_slug": {t.slug: t.id for t in self.tags},
        }
        token = current_request.set(req)
        # Reset events queue.
        q_token = events_queue.set({})
        try:
            apply_tags_bulk([(self.sites[0], ["t0"])], req)
            queued = events_queue.get()
            expected_key = f"dcim.site:{self.sites[0].pk}"
            assert expected_key in queued, list(queued.keys())
        finally:
            events_queue.reset(q_token)
            current_request.reset(token)

    def test_empty_pairs_is_noop(self):
        """No pairs -> no INSERT, no DELETE."""
        req = _make_request()
        req._diode_preload = {"tag_ids_by_slug": {}}
        with CaptureQueriesContext(connection) as ctx:
            apply_tags_bulk([], req)
        # An empty input may still touch a tx savepoint, but must not write taggeditem.
        for q in ctx.captured_queries:
            assert '"extras_taggeditem"' not in q["sql"], q["sql"]
