#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - PR 2 preload cache tests."""

from types import SimpleNamespace

from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from extras.models import Tag

from netbox_diode_plugin.api.applier import _preload_changeset_cache
from netbox_diode_plugin.api.common import Change, ChangeSet, ChangeType


class PreloadChangesetCacheTestCase(TestCase):
    """Verifies _preload_changeset_cache (PR 2)."""

    def setUp(self):
        """Create two tags + a 3-change changeset (one NOOP) for the assertions below."""
        self.tag_a = Tag.objects.create(name="alpha", slug="alpha")
        self.tag_b = Tag.objects.create(name="beta", slug="beta")

        self.change_set = ChangeSet(
            id="11111111-1111-1111-1111-111111111111",
            changes=[
                Change(
                    change_type=ChangeType.CREATE.value,
                    object_type="dcim.site",
                    ref_id="r1",
                    data={"name": "S1", "slug": "s1", "tags": ["alpha", "beta"]},
                ),
                Change(
                    change_type=ChangeType.CREATE.value,
                    object_type="dcim.site",
                    ref_id="r2",
                    # repeated tag — must dedup
                    data={"name": "S2", "slug": "s2", "tags": ["alpha"]},
                ),
                Change(
                    change_type=ChangeType.NOOP.value,
                    object_type="dcim.site",
                    ref_id="r3",
                    data={"tags": ["ignored"]},
                ),
            ],
        )

    def test_contenttype_cache_hit_after_preload(self):
        """get_for_model on a preloaded model issues 0 queries."""
        ContentType.objects.clear_cache()
        request = SimpleNamespace()
        _preload_changeset_cache(self.change_set, request)

        with CaptureQueriesContext(connection) as ctx:
            ContentType.objects.get_for_model(Site)
        assert len(ctx.captured_queries) == 0, ctx.captured_queries

    def test_tag_ids_collected_in_at_most_two_queries(self):
        """Preload should resolve tag slugs in <= 2 queries total (1 ContentType + 1 Tag)."""
        ContentType.objects.clear_cache()
        request = SimpleNamespace()
        with CaptureQueriesContext(connection) as ctx:
            preload = _preload_changeset_cache(self.change_set, request)
        # NOOP changes are skipped, so "ignored" tag should NOT be loaded.
        assert preload["tag_ids_by_slug"] == {
            "alpha": self.tag_a.id,
            "beta": self.tag_b.id,
        }
        # 1 query for ContentType warm-up + 1 query for Tag.filter(...). Allow
        # a small slack in case Django prefers to issue a savepoint.
        assert len(ctx.captured_queries) <= 2, ctx.captured_queries
        assert request._diode_preload is preload
