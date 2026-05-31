#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Buffer NetBox's ObjectChange writes during diode applies and flush them as one bulk_create.

NetBox's ``handle_changed_object`` receiver (``core/signals.py``) runs
``instance.to_objectchange(action)`` on every saved model, and that
serialisation is expensive: Django's ``serializers.serialize('json',
[obj])`` issues one SELECT per many-to-many relation on the model (its
``handle_m2m_field`` walks each m2m manager). For a model like
``dcim.Interface`` with three m2m relations that is three DB
round-trips on *every* save, which dominates the apply request critical
path - measured at roughly 40% of total apply throughput on production
load.

This module removes that cost while preserving the audit trail (and the
receivers connected to ``post_save(sender=ObjectChange)``) in three
pieces:

  1. **Fast serialisation.** While a diode apply buffer is active,
     ``ChangeLoggingMixin.serialize_object`` is routed through
     ``_fast_serialize_object``, which restricts Django's serializer to
     the model's local (non-m2m) fields via the ``fields=`` allowlist.
     Django then skips ``handle_m2m_field`` entirely, so no per-relation
     SELECT is issued. Tags are left as an empty placeholder rather than
     queried per save. Both the omitted m2m fields and the tags are
     re-added in bulk at flush (see piece 3). Outside the apply path the
     original serialiser runs unchanged.

  2. **In-memory buffer.** A request-scoped ``contextvars.ContextVar``
     holds a dict of ObjectChange instances keyed by
     ``(content_type_id, changed_object_id)``. m2m_changed events for an
     object already in the buffer merge their ``postchange_data`` in
     memory, replacing the upstream SELECT-then-UPDATE pair with a dict
     lookup.

  3. **Batched flush with bulk enrichment.** The per-entity buffers
     are collected into a request-level batch (via
     ``request_change_logging_batch``). After the apply commits, the
     batch is flushed: for each model, one query per m2m relation and
     one query for tags resolve them for *all* buffered objects at once
     and patch the values back into ``postchange_data``; then a single
     ``bulk_create`` writes every row and ``post_save`` is re-emitted so
     downstream receivers still fire. This turns O(saves x relations)
     round-trips into O(models x relations) per request.

Gating: the plugin setting ``apply_buffer_change_logging`` (default
``False``) controls whether the buffer activates. With it off the
buffered handler delegates straight through to upstream NetBox and the
fast serialiser is never engaged.

Flush timing: the per-entity append and the request-level flush are both
registered via ``transaction.on_commit``. A rolled-back entity's append
callback never fires, so its rows never reach the batch; only changes
that genuinely committed are written. The flush runs after commit, so
the bulk m2m queries observe the final committed relation state.

m2m ordering: enrichment records each relation as a sorted list of
related primary keys. This is deterministic but may differ in order
(not membership) from the queryset order Django's serializer would
produce on the synchronous path.

Pre_delete is intentionally left untouched: NetBox's delete handler runs
protection-rule validation, which must keep firing, and deletes are
uncommon on the auto-apply path.
"""

import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

from core.choices import ObjectChangeActionChoices
from core.events import OBJECT_CREATED, OBJECT_UPDATED
from core.models import ObjectChange
from core.signals import handle_changed_object as _original_handler
from django.contrib.contenttypes.models import ContentType
from django.core import serializers as dj_serializers
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save
from extras.events import enqueue_event
from extras.models import Tag
from extras.models.tags import TaggedItem
from extras.utils import is_taggable
from netbox.context import current_request, events_queue
from netbox.models.features import ChangeLoggingMixin
from netbox.plugins import get_plugin_config

from .change_log_bypass import _bypass_active, _guarded_handler
from .change_log_bypass import _enabled as _bypass_enabled

logger = logging.getLogger(__name__)

# When the bypass module is enabled it has already swapped NetBox's
# `handle_changed_object` for its own `_guarded_handler` at the
# receiver-registry level. Capture whichever function is currently the
# "real" handler so our wrapper can delegate to it on the no-buffer
# path. Order matters here: this module imports the bypass module
# above, which means the bypass module's import-time swap has already
# run by the time we read the reference.
_previous_handler = _guarded_handler if _bypass_enabled else _original_handler

# Per-entity buffer. The value type is `dict[tuple[int, int], ObjectChange]`
# keyed by (content_type_id, changed_object_id). A value of None
# means the per-entity context manager is not active and the wrapper
# should delegate to upstream.
_apply_change_buffer: ContextVar = ContextVar(
    "diode_apply_change_buffer", default=None
)

# Request-scoped batch. The value type is `list[ObjectChange] | None`.
# When set (via `request_change_logging_batch`), each per-entity buffer
# appends its ObjectChange instances here instead of flushing on its
# own. The outer context manager flushes the whole batch as one
# bulk_create at request end, which is also where m2m enrichment can
# resolve every buffered object's relations in one query per relation.
_request_batch: ContextVar = ContextVar(
    "diode_request_change_logging_batch", default=None
)


def _fast_serialize_object(obj, exclude=None):
    """
    Serialise ``obj`` for change logging without issuing per-m2m SELECTs.

    Mirrors ``utilities.serialization.serialize_object`` but passes
    ``fields=`` to Django's serializer restricted to the model's local
    (non-m2m) fields. Django skips ``handle_m2m_field`` for any field
    not in the allowlist, so the m2m round-trips are eliminated. The
    omitted m2m fields are re-added in bulk by ``_enrich_m2m`` at flush.

    FK fields are matched by Django on ``attname[:-3]`` (the field name
    without the ``_id`` suffix), so passing field *names* keeps every
    scalar and FK field while dropping only the m2m relations.

    Tags get an empty placeholder rather than a per-save query;
    ``_enrich_tags`` fills it in bulk at flush. The placeholder is only
    added for taggable models, which is also how a row is later
    recognised as needing tag enrichment.
    """
    field_names = [f.name for f in obj._meta.local_fields]
    json_str = dj_serializers.serialize("json", [obj], fields=field_names)
    data = json.loads(json_str)[0]["fields"]
    exclude = exclude or []

    if "custom_field_data" in data:
        data["custom_fields"] = data.pop("custom_field_data")

    # Resolving tags here would cost one query per save. Leave an empty
    # placeholder for taggable models and let `_enrich_tags` fill it in
    # bulk at flush. The placeholder's presence also marks the row as
    # taggable: is_taggable needs the live instance we have here but not
    # at flush time. (The `_tags` instance cache upstream consults is not
    # populated on the diode apply path that this fast route serves.)
    if is_taggable(obj):
        data["tags"] = []

    for key in list(data.keys()):
        if key in exclude:
            data.pop(key)

    return data


_original_serialize_object = ChangeLoggingMixin.serialize_object


def _serialize_object_gated(self, exclude=None):
    """
    Route serialisation through the fast path only while a buffer is active.

    Installed over ``ChangeLoggingMixin.serialize_object`` at import.
    When no diode apply buffer is active in the current context this
    delegates to the original method, so all non-apply change logging
    (the UI, the REST API, etc.) is byte-for-byte unchanged.
    """
    if _apply_change_buffer.get() is not None:
        return _fast_serialize_object(self, exclude=exclude or [])
    return _original_serialize_object(self, exclude=exclude)


ChangeLoggingMixin.serialize_object = _serialize_object_gated


def _classify_signal(instance, kwargs):
    """
    Mirror of NetBox's event-type / m2m-flag detection.

    Returns ``(event_type, action, m2m_changed_flag)`` for the buffer
    path to act on, or ``None`` if the signal does not produce an
    ObjectChange. Kept in lockstep with
    ``core/signals.py:handle_changed_object``.
    """
    if kwargs.get("created"):
        return OBJECT_CREATED, ObjectChangeActionChoices.ACTION_CREATE, False
    if "created" in kwargs:
        return OBJECT_UPDATED, ObjectChangeActionChoices.ACTION_UPDATE, False
    if kwargs.get("action") in ("post_add", "post_remove") and kwargs.get("pk_set"):
        return OBJECT_UPDATED, ObjectChangeActionChoices.ACTION_UPDATE, True
    if kwargs.get("action") == "post_clear":
        if kwargs.get("model") == Tag and getattr(instance, "_prechange_snapshot", {}).get("tags"):
            return OBJECT_UPDATED, ObjectChangeActionChoices.ACTION_UPDATE, True
    return None


def _buffered_handler(sender, instance, **kwargs):
    """
    Receiver wrapper installed in place of NetBox's ``handle_changed_object``.

    When no buffer is active in the current context, delegates to the
    previously-connected handler (NetBox's default, or the bypass
    wrapper if that feature is enabled). When a buffer is active,
    captures the would-be ObjectChange in the buffer rather than
    saving it.
    """
    buffer = _apply_change_buffer.get()
    if buffer is None:
        return _previous_handler(sender, instance, **kwargs)

    # If apply_bypass_change_logging is also active in this context,
    # bypass wins: no ObjectChange row, no buffered entry, no event.
    # This keeps the combined-settings semantics predictable - enabling
    # both flags lands on "no change logging" rather than "buffered
    # change logging".
    if _bypass_active.get():
        return None

    if not hasattr(instance, "to_objectchange"):
        return None

    request = current_request.get()
    if request is None:
        return None

    classified = _classify_signal(instance, kwargs)
    if classified is None:
        return None
    event_type, action, m2m_changed_flag = classified

    objectchange = instance.to_objectchange(action)
    key = (ContentType.objects.get_for_model(instance).id, instance.pk)

    if m2m_changed_flag and key in buffer:
        # In-memory equivalent of the upstream "find prior ObjectChange
        # by (content_type, object_id, request_id) and merge
        # postchange_data". No DB round-trip.
        buffer[key].postchange_data = objectchange.postchange_data
    elif objectchange and objectchange.has_changes:
        # Pre-populate the fields that ObjectChange.save() would
        # otherwise set (user_name, object_repr). bulk_create skips
        # the model's save() override, so these must be set on the
        # instance before queuing it.
        objectchange.user = request.user
        objectchange.user_name = request.user.username if request.user else ""
        objectchange.request_id = request.id
        objectchange.object_repr = str(instance)
        buffer[key] = objectchange

    if m2m_changed_flag:
        # Match upstream behaviour: ensure subsequent reads see the
        # fresh m2m assignments.
        instance.refresh_from_db()

    # Append to the request-scoped events queue exactly as upstream
    # does. NetBox flushes this queue at `request_finished`, which is
    # how downstream signal consumers receive their payloads. Buffering
    # the ObjectChange writes does not change this path.
    queue = events_queue.get()
    enqueue_event(queue, instance, request, event_type)
    events_queue.set(queue)

    return None


# Install the wrapper unconditionally at import time. The wrapper
# itself is the no-op gate (it checks the contextvar on every call),
# so installing it always is the cheapest correct option and keeps
# tests able to exercise the buffer path without re-wiring receivers
# at runtime. The disconnect/connect pair runs once per worker process
# at module import, before any request is served, so it is not subject
# to the threading hazard that motivated the bypass module's design.
post_save.disconnect(_previous_handler)
m2m_changed.disconnect(_previous_handler)
post_save.connect(_buffered_handler)
m2m_changed.connect(_buffered_handler)


def _enrich_m2m(objectchanges):
    """
    Re-add m2m relations to buffered ObjectChange rows in bulk.

    ``_fast_serialize_object`` omits m2m fields to avoid a per-save
    SELECT per relation. This restores them: rows are grouped by content
    type, and for each m2m relation on the model a single through-table
    query resolves the relation for every object at once. The resolved
    primary keys are written back into each row's ``postchange_data`` as
    a sorted list, matching the membership Django's serializer would
    have recorded.

    Must run after the apply transaction commits so the through-table
    reads observe the final relation state.
    """
    by_ct: dict[int, list] = defaultdict(list)
    for oc in objectchanges:
        if oc.postchange_data is not None and oc.changed_object_id is not None:
            by_ct[oc.changed_object_type_id].append(oc)

    for ct_id, rows in by_ct.items():
        model = ContentType.objects.get_for_id(ct_id).model_class()
        if model is None:
            continue

        m2m_fields = [f for f in model._meta.local_many_to_many if f.serialize]
        if not m2m_fields:
            continue

        obj_ids = [oc.changed_object_id for oc in rows]
        for field in m2m_fields:
            through = field.remote_field.through
            src_col = field.m2m_column_name()
            tgt_col = field.m2m_reverse_name()

            relation: dict[int, list] = defaultdict(list)
            pairs = through.objects.filter(
                **{f"{src_col}__in": obj_ids}
            ).values_list(src_col, tgt_col)
            for src_id, tgt_id in pairs:
                relation[src_id].append(tgt_id)

            for oc in rows:
                oc.postchange_data[field.name] = sorted(relation.get(oc.changed_object_id, []))


def _enrich_tags(objectchanges):
    """
    Fill the tag placeholder left by ``_fast_serialize_object`` in bulk.

    ``_fast_serialize_object`` records ``tags: []`` for taggable models
    instead of resolving tags per save (one query each). This resolves
    them for all buffered objects at once: rows are grouped by content
    type and one query over ``extras_taggeditem`` returns every object's
    tag names, written back as a sorted list - matching what upstream
    ``serialize_object`` records.

    Only rows that carry the ``tags`` placeholder (taggable models) are
    touched, so non-taggable rows keep no ``tags`` key, exactly as
    upstream. Must run after the apply transaction commits so the reads
    observe the final tag assignments.
    """
    by_ct: dict[int, list] = defaultdict(list)
    for oc in objectchanges:
        if (
            oc.postchange_data is not None
            and "tags" in oc.postchange_data
            and oc.changed_object_id is not None
        ):
            by_ct[oc.changed_object_type_id].append(oc)

    for ct_id, rows in by_ct.items():
        obj_ids = [oc.changed_object_id for oc in rows]
        tags_by_obj: dict[int, list] = defaultdict(list)
        pairs = TaggedItem.objects.filter(
            content_type_id=ct_id, object_id__in=obj_ids
        ).values_list("object_id", "tag__name")
        for obj_id, tag_name in pairs:
            tags_by_obj[obj_id].append(tag_name)

        for oc in rows:
            oc.postchange_data["tags"] = sorted(tags_by_obj.get(oc.changed_object_id, []))


def _flush_objectchanges(objectchanges):
    """
    Persist buffered ObjectChange rows as one bulk_create and re-emit post_save.

    bulk_create does not fire ``post_save``; we re-emit it for each row
    so receivers connected to ``post_save(sender=ObjectChange)`` fire
    exactly once. The full kwargs set Django's own ``_save_table`` would
    pass is supplied because some NetBox receivers (e.g.
    ``update_denormalized_fields``) declare ``raw`` as required.
    """
    if not objectchanges:
        return

    _enrich_m2m(objectchanges)
    _enrich_tags(objectchanges)
    created = ObjectChange.objects.bulk_create(objectchanges)
    for obj in created:
        post_save.send(
            sender=ObjectChange,
            instance=obj,
            created=True,
            update_fields=None,
            raw=False,
            using=obj._state.db,
        )


@contextmanager
def buffered_change_logging():
    """
    Collect ObjectChange writes during an apply and flush them in bulk.

    No-op when ``apply_buffer_change_logging`` is False (the default),
    which means the buffered handler still runs but delegates straight
    through to upstream NetBox without touching the buffer.

    On a successful apply the buffered ObjectChange instances are handed
    off for flushing. If a request-level batch is active (the view
    wrapped its entity loop in ``request_change_logging_batch``) the
    instances are appended to the batch and the outer context manager
    flushes everything as one bulk_create at request end. Otherwise this
    flushes its own buffer directly.

    Both paths register their work via ``transaction.on_commit`` so a
    rolled-back apply drops the buffered rows without persisting them and
    the bulk m2m enrichment observes committed relation state.
    """
    if not get_plugin_config("netbox_diode_plugin", "apply_buffer_change_logging"):
        yield
        return

    token = _apply_change_buffer.set({})
    try:
        yield
        buffer = _apply_change_buffer.get()
        if not buffer:
            return

        instances = list(buffer.values())
        batch = _request_batch.get()
        if batch is not None:
            transaction.on_commit(lambda: batch.extend(instances))
        else:
            transaction.on_commit(lambda: _flush_objectchanges(instances))
    finally:
        _apply_change_buffer.reset(token)


@contextmanager
def request_change_logging_batch():
    """
    Collect every per-entity buffer in a request and flush them as one bulk_create.

    Endpoints that process many entities per HTTP request
    (``/bulk-plan-apply/``, ``/bulk-apply/``) wrap their entity loop in
    this context. Each per-entity ``buffered_change_logging`` appends its
    ObjectChange instances to a shared list; on exit the whole list is
    flushed once. Flushing at request scope is also what lets
    ``_enrich_m2m`` resolve every object's relations in one query per
    relation instead of one per entity.

    Per-entity rollback semantics are preserved: each entity's append is
    registered via ``transaction.on_commit`` on its own atomic block, so
    a failed entity contributes nothing. The flush itself is registered
    via ``on_commit`` in ``finally`` so a partial batch from the entities
    that did commit is still written even if a later entity raised.

    No-op when ``apply_buffer_change_logging`` is False (matches the
    per-entity context manager's gate, so the two stay in lockstep).
    """
    if not get_plugin_config("netbox_diode_plugin", "apply_buffer_change_logging"):
        yield
        return

    batch: list = []
    token = _request_batch.set(batch)
    try:
        yield
    finally:
        # Register the flush via on_commit rather than calling it inline
        # so it queues after the per-entity appends. Django fires queued
        # on_commit callbacks FIFO, so registering ours last guarantees a
        # fully-populated batch. When the request has no surrounding
        # atomic block, on_commit fires immediately - by which point the
        # per-entity atomics have already committed and appended.
        transaction.on_commit(lambda: _flush_objectchanges(batch))
        _request_batch.reset(token)
