#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Bulk TaggedItem writes for diode apply paths.

PR 4 of BULK-ORM Sprint 1.

NetBox's `instance.tags.set([...])` triggers `m2m_changed` (post_clear +
post_add) per instance. The signal handler re-fires
`handle_changed_object`, which (a) writes another ObjectChange UPDATE and
(b) calls `enqueue_event` again — that re-runs the expensive
`serialize_for_event(instance)`. Multiplied by every tagged object in a
changeset this dominates the apply path.

This module bypasses the through-table m2m signal by writing
`extras_taggeditem` rows directly via `bulk_create` and re-emitting a
single `enqueue_event` per instance to refresh the queued payload with
the post-tag state. PR 3's `deferred_changelog` already collapses the
ObjectChange side; this module collapses the TaggedItem side.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from core.events import OBJECT_UPDATED
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from extras.events import enqueue_event
from extras.models import Tag, TaggedItem
from netbox.context import events_queue
from netbox.models.features import TagsMixin

logger = logging.getLogger(__name__)


def supports_tags(model_or_instance) -> bool:
    """Return True if the model (or instance's class) inherits from TagsMixin."""
    cls = model_or_instance if isinstance(model_or_instance, type) else type(model_or_instance)
    return issubclass(cls, TagsMixin)


def apply_tags_bulk(instance_tag_pairs: list[tuple[Any, list]], request) -> None:
    """
    Write tags for a list of (instance, tag_input) pairs via bulk_create.

    `tag_input` may be a list of slug strings, a list of integer tag IDs, or
    a list of dicts with a "slug" key (the same shapes the serializer accepts).
    Tags listed for an instance fully replace its existing tag set, mirroring
    `instance.tags.set([...])` semantics — but without the m2m_changed re-fire.
    """
    if not instance_tag_pairs:
        return

    preload = getattr(request, "_diode_preload", None) or {}
    tag_ids_by_slug: dict[str, int] = preload.get("tag_ids_by_slug", {}) or {}
    _backfill_missing_tag_ids(instance_tag_pairs, tag_ids_by_slug)

    rows, target_keys, instances_to_event = _build_rows(
        instance_tag_pairs, tag_ids_by_slug,
    )

    _replace_existing(target_keys)
    if rows:
        TaggedItem.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
    _re_emit_events(instances_to_event, request)


def _backfill_missing_tag_ids(pairs, tag_ids_by_slug: dict[str, int]) -> None:
    missing: set[str] = set()
    for _instance, tag_input in pairs:
        for raw in tag_input or []:
            slug = _slug_from(raw)
            if slug and slug not in tag_ids_by_slug:
                missing.add(slug)
    if missing:
        for tag_id, slug in Tag.objects.filter(slug__in=missing).values_list("id", "slug"):
            tag_ids_by_slug[slug] = tag_id


def _build_rows(pairs, tag_ids_by_slug: dict[str, int]):
    ct_by_model: dict[type, ContentType] = {}
    rows: list[TaggedItem] = []
    target_keys: dict[int, set[int]] = defaultdict(set)
    instances_to_event: list[Any] = []

    for instance, tag_input in pairs:
        if instance is None or instance.pk is None:
            continue
        ct = _ct_for(instance, ct_by_model)
        target_keys[ct.id].add(instance.pk)
        instances_to_event.append(instance)
        for raw in tag_input or []:
            tag_id = _resolve_tag_id(raw, tag_ids_by_slug)
            if tag_id is None:
                logger.warning("apply_tags_bulk: could not resolve tag %r for %s", raw, instance)
                continue
            rows.append(TaggedItem(content_type_id=ct.id, object_id=instance.pk, tag_id=tag_id))

    return rows, target_keys, instances_to_event


def _ct_for(instance, ct_by_model: dict[type, ContentType]) -> ContentType:
    model = type(instance)
    ct = ct_by_model.get(model)
    if ct is None:
        ct = ContentType.objects.get_for_model(model)
        ct_by_model[model] = ct
    return ct


def _replace_existing(target_keys: dict[int, set[int]]) -> None:
    if not target_keys:
        return
    q = Q()
    for ct_id, obj_ids in target_keys.items():
        q |= Q(content_type_id=ct_id, object_id__in=obj_ids)
    TaggedItem.objects.filter(q).delete()


def _re_emit_events(instances, request) -> None:
    if not instances:
        return
    queue = events_queue.get()
    for instance in instances:
        try:
            enqueue_event(queue, instance, request, OBJECT_UPDATED)
        except Exception as e:
            logger.warning("apply_tags_bulk: enqueue_event failed for %s: %s", instance, e)
    events_queue.set(queue)


def _slug_from(raw) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        s = raw.get("slug")
        return s if isinstance(s, str) else None
    return None


def _resolve_tag_id(raw, tag_ids_by_slug: dict[str, int]) -> int | None:
    if isinstance(raw, int):
        return raw
    slug = _slug_from(raw)
    if slug is not None:
        return tag_ids_by_slug.get(slug)
    return None
