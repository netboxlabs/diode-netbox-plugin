#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - buffered_change_logging tests."""

import logging
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from core.models import ObjectChange
from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from extras.models import Tag
from ipam.models import ASN, RIR
from netbox.config import get_config
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import change_log_buffer, views
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

    # --- Setting OFF: buffer is a pass-through, upstream writes synchronously ---

    def test_setting_false_writes_objectchange_synchronously(self):
        """With the setting off (default), the apply writes ObjectChange synchronously via upstream."""
        response = self.client.post(
            self.url,
            data=self._make_payload(uuid4().hex[:8]),
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        # Upstream synchronous path -> row already in DB at request return.
        self.assertEqual(self._site_change_count(), 1)

    # --- Setting ON: buffer collects + flushes one bulk_create at commit ---

    def test_setting_true_flushes_objectchange_on_commit(self):
        """With the setting on, the buffered ObjectChange is written by the on_commit flush."""
        suffix = uuid4().hex[:8]
        # Django's TestCase wraps each test in a transaction that is
        # rolled back at end-of-test, which means `transaction.on_commit`
        # callbacks normally never fire. `captureOnCommitCallbacks` runs
        # them explicitly on context exit so we can assert the flush.
        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

        # Apply transaction committed -> on_commit fired -> exactly one row.
        self.assertEqual(self._site_change_count(), 1)
        oc = ObjectChange.objects.get(
            changed_object_type__app_label="dcim", changed_object_type__model="site"
        )
        self.assertEqual(oc.action, "create")
        self.assertEqual(oc.object_repr, f"Site {suffix}")

    # --- Rollback: no flush on apply failure ---

    def test_rollback_does_not_flush(self):
        """If apply_changeset raises, transaction.on_commit never fires and nothing is written."""
        suffix = uuid4().hex[:8]

        def failing_apply(change_set, request):
            Site.objects.create(name=f"Doomed {suffix}", slug=f"doomed-{suffix}")
            raise RuntimeError("forced rollback")

        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(views, "apply_changeset", side_effect=failing_apply), \
             mock.patch.object(change_log_buffer, "_flush_objectchanges") as mock_flush, \
             self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
            )

        # Outer atomic rolled back the Site; the append on_commit was discarded.
        self.assertFalse(Site.objects.filter(slug=f"doomed-{suffix}").exists())
        # Flush still runs at request end but the batch is empty.
        if mock_flush.called:
            self.assertEqual(list(mock_flush.call_args.args[0]), [])

    # --- Bypass wins when both flags are enabled ---

    def test_bypass_takes_precedence_over_buffer_when_both_active(self):
        """When apply_bypass_change_logging is active, the buffer never collects and nothing is written."""
        suffix = uuid4().hex[:8]

        bypass_token = change_log_buffer._bypass_active.set(True)
        try:
            with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
                 self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    self.url, data=self._make_payload(suffix), format="json", **self.authorization_header
                )
        finally:
            change_log_buffer._bypass_active.reset(bypass_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        # Site was created (apply itself wasn't bypassed), but no ObjectChange.
        self.assertTrue(Site.objects.filter(slug=f"site-{suffix}").exists())
        self.assertEqual(self._site_change_count(), 0)

    # --- Request-level batching: many entities -> one consolidated flush ---

    def test_multi_entity_request_flushes_one_batch(self):
        """3 entities in one request -> ONE flush carrying all 3 entities' rows."""
        suffix = uuid4().hex[:8]
        payload = {
            "entities": [
                {
                    "id": f"entity-{i}-{suffix}",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": f"Site {i} {suffix}", "slug": f"site-{i}-{suffix}"}},
                }
                for i in range(3)
            ]
        }

        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(
                 change_log_buffer, "_flush_objectchanges",
                 side_effect=change_log_buffer._flush_objectchanges,
             ) as spy_flush, \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, data=payload, format="json", **self.authorization_header
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        # Single flush for the whole request, not one per entity.
        spy_flush.assert_called_once()
        flushed = list(spy_flush.call_args.args[0])
        self.assertEqual(len(flushed), 3)
        reprs = {oc.object_repr for oc in flushed}
        self.assertEqual(reprs, {f"Site 0 {suffix}", f"Site 1 {suffix}", f"Site 2 {suffix}"})
        self.assertEqual(self._site_change_count(), 3)

    def test_failed_entity_excluded_from_batch(self):
        """When one entity fails, its rows are dropped from the consolidated flush."""
        suffix = uuid4().hex[:8]
        good_slug = f"good-{suffix}"
        bad_slug = f"bad-{suffix}"
        payload = {
            "entities": [
                {
                    "id": f"good-{suffix}",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": f"Good {suffix}", "slug": good_slug}},
                },
                {
                    "id": f"bad-{suffix}",
                    "object_type": "dcim.site",
                    "entity": {"site": {"name": f"Bad {suffix}", "slug": bad_slug}},
                },
            ]
        }

        original_apply = views.apply_changeset
        call_count = {"n": 0}

        def selective_failing_apply(change_set, request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return original_apply(change_set, request)
            Site.objects.create(name=f"Bad inner {suffix}", slug=bad_slug)
            raise RuntimeError("forced rollback for second entity")

        with mock.patch.object(change_log_buffer, "get_plugin_config", return_value=True), \
             mock.patch.object(views, "apply_changeset", side_effect=selective_failing_apply), \
             mock.patch.object(
                 change_log_buffer, "_flush_objectchanges",
                 side_effect=change_log_buffer._flush_objectchanges,
             ) as spy_flush, \
             self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.url, data=payload, format="json", **self.authorization_header
            )

        # Good entity made it through; bad entity rolled back.
        self.assertTrue(Site.objects.filter(slug=good_slug).exists())
        self.assertFalse(Site.objects.filter(slug=bad_slug).exists())
        # Flush happens but only contains the good row.
        spy_flush.assert_called_once()
        flushed = list(spy_flush.call_args.args[0])
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].object_repr, f"Good {suffix}")


class FastSerializeTestCase(TestCase):
    """`_fast_serialize_object` parity and the buffer-active gate."""

    def setUp(self):
        """Create a Site with an m2m relation (asns) and a tag."""
        ObjectChange.objects.all().delete()
        self.site = Site.objects.create(name="Parity site", slug=f"parity-{uuid4().hex[:8]}")
        self.site.tags.add(
            Tag.objects.create(name="alpha", slug="alpha"),
            Tag.objects.create(name="beta", slug="beta"),
        )
        rir = RIR.objects.create(name="Test RIR", slug=f"rir-{uuid4().hex[:8]}")
        self.asn = ASN.objects.create(asn=65001, rir=rir)
        self.site.asns.add(self.asn)

    def test_fast_serialize_omits_m2m_and_placeholders_tags(self):
        """Fast output equals upstream minus m2m, with tags left as an empty placeholder."""
        vanilla = change_log_buffer._original_serialize_object(self.site, exclude=[])
        fast = change_log_buffer._fast_serialize_object(self.site, exclude=[])

        # `asns` is the only serialisable m2m on Site; the fast path drops it.
        m2m_names = {f.name for f in Site._meta.local_many_to_many if f.serialize}
        self.assertIn("asns", m2m_names)
        self.assertNotIn("asns", fast)
        # Tags are a placeholder, resolved in bulk at flush, not per-save.
        self.assertEqual(fast["tags"], [])

        # Every other field matches upstream exactly.
        expected = {k: v for k, v in vanilla.items() if k not in m2m_names and k != "tags"}
        actual = {k: v for k, v in fast.items() if k != "tags"}
        self.assertEqual(actual, expected)

    def test_full_parity_after_enrichment(self):
        """Fast serialize + m2m + tag enrichment reproduces the upstream serializer output."""
        vanilla = change_log_buffer._original_serialize_object(self.site, exclude=[])
        row = ObjectChange(
            changed_object_type_id=ContentType.objects.get_for_model(Site).id,
            changed_object_id=self.site.pk,
            action="create",
            postchange_data=change_log_buffer._fast_serialize_object(self.site, exclude=[]),
        )
        change_log_buffer._enrich_m2m([row])
        change_log_buffer._enrich_tags([row])

        # m2m ordering: enrichment sorts, upstream uses queryset order;
        # compare membership for the relation, then the rest exactly.
        self.assertEqual(set(row.postchange_data.pop("asns")), set(vanilla.pop("asns")))
        self.assertEqual(row.postchange_data, vanilla)

    def test_serialize_object_gate_is_inactive_without_buffer(self):
        """With no buffer active, serialize_object delegates to the upstream implementation."""
        # Identical output to the captured original means the gate did not
        # engage the fast path.
        self.assertEqual(
            self.site.serialize_object(),
            change_log_buffer._original_serialize_object(self.site),
        )

    def test_serialize_object_gate_engages_fast_path_with_buffer(self):
        """With a buffer active, serialize_object routes through the fast path (no m2m)."""
        token = change_log_buffer._apply_change_buffer.set({})
        try:
            gated = self.site.serialize_object()
        finally:
            change_log_buffer._apply_change_buffer.reset(token)
        self.assertNotIn("asns", gated)


class EnrichM2MTestCase(TestCase):
    """`_enrich_m2m` re-adds m2m relations to buffered rows in bulk."""

    def setUp(self):
        """Two Sites, each with distinct ASNs, to exercise per-object grouping."""
        ObjectChange.objects.all().delete()
        rir = RIR.objects.create(name="Enrich RIR", slug=f"erir-{uuid4().hex[:8]}")
        self.asn1 = ASN.objects.create(asn=65010, rir=rir)
        self.asn2 = ASN.objects.create(asn=65011, rir=rir)
        self.site_a = Site.objects.create(name="Enrich A", slug=f"enrich-a-{uuid4().hex[:8]}")
        self.site_b = Site.objects.create(name="Enrich B", slug=f"enrich-b-{uuid4().hex[:8]}")
        self.site_a.asns.add(self.asn1, self.asn2)
        self.site_b.asns.add(self.asn1)
        self.ct_id = ContentType.objects.get_for_model(Site).id

    def _row(self, site):
        return ObjectChange(
            changed_object_type_id=self.ct_id,
            changed_object_id=site.pk,
            action="create",
            postchange_data=change_log_buffer._fast_serialize_object(site, exclude=[]),
        )

    def test_enrich_populates_m2m_per_object(self):
        """Each row's postchange_data gets its own object's relation, sorted."""
        rows = [self._row(self.site_a), self._row(self.site_b)]
        change_log_buffer._enrich_m2m(rows)

        self.assertEqual(rows[0].postchange_data["asns"], sorted([self.asn1.pk, self.asn2.pk]))
        self.assertEqual(rows[1].postchange_data["asns"], [self.asn1.pk])

    def test_enriched_membership_matches_upstream(self):
        """After enrichment, m2m membership matches what the upstream serializer records."""
        vanilla = change_log_buffer._original_serialize_object(self.site_a, exclude=[])
        row = self._row(self.site_a)
        change_log_buffer._enrich_m2m([row])
        self.assertEqual(set(row.postchange_data["asns"]), set(vanilla["asns"]))

    def test_enrich_is_bulk_one_query_per_relation(self):
        """Resolving N objects' relations costs one query per relation, not one per object."""
        rows = [self._row(self.site_a), self._row(self.site_b)]
        # Site has a single serialisable m2m (asns) -> exactly one query.
        with self.assertNumQueries(1):
            change_log_buffer._enrich_m2m(rows)


class EnrichTagsTestCase(TestCase):
    """`_enrich_tags` fills the tag placeholder left by the fast serializer in bulk."""

    def setUp(self):
        """Two Sites with overlapping tags to exercise per-object grouping."""
        ObjectChange.objects.all().delete()
        self.red = Tag.objects.create(name="red", slug="red")
        self.blue = Tag.objects.create(name="blue", slug="blue")
        self.site_a = Site.objects.create(name="Tag A", slug=f"tag-a-{uuid4().hex[:8]}")
        self.site_b = Site.objects.create(name="Tag B", slug=f"tag-b-{uuid4().hex[:8]}")
        self.site_a.tags.add(self.red, self.blue)
        self.site_b.tags.add(self.red)
        self.ct_id = ContentType.objects.get_for_model(Site).id

    def _row(self, site):
        return ObjectChange(
            changed_object_type_id=self.ct_id,
            changed_object_id=site.pk,
            action="create",
            postchange_data=change_log_buffer._fast_serialize_object(site, exclude=[]),
        )

    def test_enrich_fills_tags_per_object_sorted(self):
        """Each row gets its own object's tag names, sorted."""
        rows = [self._row(self.site_a), self._row(self.site_b)]
        change_log_buffer._enrich_tags(rows)
        self.assertEqual(rows[0].postchange_data["tags"], ["blue", "red"])
        self.assertEqual(rows[1].postchange_data["tags"], ["red"])

    def test_enrich_tags_one_query_per_content_type(self):
        """N taggable objects of one model cost a single tag query, not one per object."""
        rows = [self._row(self.site_a), self._row(self.site_b)]
        with self.assertNumQueries(1):
            change_log_buffer._enrich_tags(rows)

    def test_enrich_tags_empty_for_untagged_object(self):
        """A taggable object with no tags keeps the empty placeholder."""
        site_c = Site.objects.create(name="Tag C", slug=f"tag-c-{uuid4().hex[:8]}")
        row = self._row(site_c)
        change_log_buffer._enrich_tags([row])
        self.assertEqual(row.postchange_data["tags"], [])

    def test_enrich_tags_skips_rows_without_placeholder(self):
        """Rows whose postchange_data has no `tags` key (non-taggable shape) are not given one."""
        row = ObjectChange(
            changed_object_type_id=self.ct_id,
            changed_object_id=self.site_a.pk,
            action="create",
            postchange_data={"name": "no-tags-key"},
        )
        change_log_buffer._enrich_tags([row])
        self.assertNotIn("tags", row.postchange_data)


class SnapshotForApplyTestCase(TestCase):
    """`snapshot_for_apply` captures prechange consistent with the buffered postchange."""

    def setUp(self):
        """A Site with an m2m relation (asns) and a tag."""
        ObjectChange.objects.all().delete()
        self.site = Site.objects.create(name="Snap site", slug=f"snap-{uuid4().hex[:8]}")
        self.site.tags.add(Tag.objects.create(name="green", slug="green"))
        rir = RIR.objects.create(name="Snap RIR", slug=f"srir-{uuid4().hex[:8]}")
        self.asn = ASN.objects.create(asn=65100, rir=rir)
        self.site.asns.add(self.asn)
        self.ct_id = ContentType.objects.get_for_model(Site).id
        self._exclude = ["last_updated"] if get_config().CHANGELOG_SKIP_EMPTY_CHANGES else []

    def _snapshot_with_buffer(self):
        token = change_log_buffer._apply_change_buffer.set({})
        try:
            change_log_buffer.snapshot_for_apply(self.site)
        finally:
            change_log_buffer._apply_change_buffer.reset(token)
        return self.site._prechange_snapshot

    def test_buffer_active_captures_sorted_m2m_and_tags(self):
        """With the buffer active, prechange records m2m + tags resolved now, sorted."""
        snap = self._snapshot_with_buffer()
        self.assertEqual(snap["asns"], [self.asn.pk])
        self.assertEqual(snap["tags"], ["green"])

    def test_prechange_matches_postchange_for_unchanged_object(self):
        """Prechange and postchange are identical for an unmodified object (no spurious diff)."""
        prechange = self._snapshot_with_buffer()

        # Build postchange exactly as the buffered flush would.
        row = ObjectChange(
            changed_object_type_id=self.ct_id,
            changed_object_id=self.site.pk,
            action="update",
            postchange_data=change_log_buffer._fast_serialize_object(self.site, exclude=self._exclude),
        )
        change_log_buffer._enrich_m2m([row])
        change_log_buffer._enrich_tags([row])

        self.assertEqual(prechange, row.postchange_data)

    def test_buffer_inactive_delegates_to_netbox_snapshot(self):
        """With no buffer, prechange comes from NetBox's own snapshot (full serializer)."""
        if hasattr(self.site, "_prechange_snapshot"):
            del self.site._prechange_snapshot
        change_log_buffer.snapshot_for_apply(self.site)
        self.assertTrue(hasattr(self.site, "_prechange_snapshot"))
        # The full serializer includes the m2m relation directly.
        self.assertIn("asns", self.site._prechange_snapshot)
