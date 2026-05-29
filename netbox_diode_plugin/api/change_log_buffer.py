#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Buffer NetBox's ObjectChange writes during apply and flush them asynchronously via RQ.

NetBox's ``handle_changed_object`` receiver (``core/signals.py``) is
not just an INSERT - it also runs ``instance.to_objectchange(action)``
which serialises the model's full pre/post state, and that
serialisation triggers a cascade of FK / related-table SELECTs
(typically ~5-10 SELECTs per save, against ``django_content_type``,
``extras_tag``, and any reverse-relationship tables on the model
being saved). Under bulk auto-apply (50 entities per request, several
saves per entity) the synchronous change-logging chain dominates the
apply request critical path - measured at ~40% of total apply
throughput on production load tests.

This module preserves the audit trail (and any receivers connected
to ``post_save(sender=ObjectChange)``) by **moving the ObjectChange
write off the apply request critical path entirely**, onto NetBox's
existing RQ workers:

  1. A request-scoped buffer (a ``contextvars.ContextVar`` holding a
     dict keyed by ``(content_type_id, changed_object_id)``) gathers
     ObjectChange instances in memory during the apply. m2m_changed
     events for an object already in the buffer merge their
     ``postchange_data`` in memory, which replaces the upstream
     SELECT-then-UPDATE pair with a dict lookup. This part runs in
     the request thread but is purely Python in-memory work - no DB.
  2. On successful exit of ``buffered_change_logging()`` the buffer
     is serialised to a plain-dict payload (see
     ``async_change_logging.serialise_buffer_to_payload``) and an RQ
     job is enqueued via ``transaction.on_commit`` so that it only
     fires after the apply transaction successfully commits. The
     ObjectChange rows themselves are written by an RQ worker
     (``async_change_logging.write_object_changes_async``) on its
     own time, off the request thread. ``post_save`` is re-emitted
     for each row inside the worker so any receiver connected to
     ``post_save(sender=ObjectChange)`` still fires.

The module installs its wrapper into ``post_save`` and ``m2m_changed``
at import time, unconditionally. The wrapper is a no-op when the
context manager is not active (it just delegates to whichever handler
was previously connected, which is NetBox's ``handle_changed_object``
or - if ``apply_bypass_change_logging`` is enabled in the bypass
module - the bypass-aware wrapper from ``change_log_bypass``). The
plugin setting ``apply_buffer_change_logging`` gates whether
``buffered_change_logging()`` actually activates the buffer; with the
setting at its default ``False`` the context manager yields without
touching the buffer and behaviour is exactly upstream.

Rollback semantics: ``transaction.on_commit`` callbacks only fire if
the outer atomic block commits. If the apply raises after we
collected the buffer but before commit, the on_commit callback never
runs and the payload is discarded - no orphan audit rows.

Trade-offs vs the synchronous flush this replaces:

  - Audit log becomes eventually-consistent (typical lag <1s under
    healthy queue load). Readers of ``core_objectchange``
    immediately after an apply may not see the just-applied
    changes.
  - Receivers connected to ``post_save(sender=ObjectChange)`` fire
    inside the worker, not in the apply request thread. The worker
    re-establishes any per-request context (current user,
    request_id, and an optional ``active_branch`` from
    ``netbox-branching`` if installed) from the serialised payload
    so receivers see the same inputs they would have seen on the
    synchronous path.
  - Worker failures are caught by RQ ``Retry`` with exponential
    backoff; persistent failures land on the failed queue, visible
    via django_rq admin. The audit gap is observable and
    replayable.

Pre_delete is intentionally left untouched: NetBox's delete handler
runs protection-rule validation, which must keep firing, and deletes
are uncommon on the auto-apply path.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from core.choices import ObjectChangeActionChoices
from core.events import OBJECT_CREATED, OBJECT_UPDATED
from core.signals import handle_changed_object as _original_handler
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save
from extras.events import enqueue_event
from extras.models import Tag
from netbox.context import current_request, events_queue
from netbox.plugins import get_plugin_config

from .async_change_logging import enqueue_async_write, serialise_buffer_to_payload
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

# Request-scoped batch list. The value type is `list[dict] | None`.
# When set (via `request_change_logging_batch`), the per-entity
# context manager appends each entity's payload here instead of
# enqueueing an RQ job per entity. The outer context manager then
# enqueues a single consolidated job at request end - dramatically
# fewer worker invocations and bigger UNNEST batches at the
# `bulk_create` boundary.
_request_batch: ContextVar = ContextVar(
    "diode_request_change_logging_batch", default=None
)


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
    # how the eventsink plugin receives webhook payloads. Buffering
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


@contextmanager
def buffered_change_logging():
    """
    Collect ObjectChange writes during apply and enqueue them for async write via RQ.

    No-op when ``apply_buffer_change_logging`` is False (the default),
    which means the buffered handler still runs but delegates straight
    through to upstream NetBox without touching the buffer.

    On a successful apply the in-memory buffer is serialised to a
    plain-dict payload and the actual ``ObjectChange.objects.bulk_create``
    + ``post_save`` re-emit is enqueued onto NetBox's default RQ
    queue via ``transaction.on_commit``. The RQ worker writes the
    rows on its own time, off the apply request critical path.

    Raising inside the ``with`` block skips the enqueue entirely.
    ``transaction.on_commit`` callbacks only fire on successful
    commit of the surrounding atomic block, so if the apply raises
    (or any later receiver does), the payload is dropped and no
    audit rows are written - matches the upstream "transaction
    rolls back -> ObjectChange not visible" semantics.
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

        # Capture context once, while still on the request thread.
        # The RQ worker reconstitutes from this snapshot.
        request = current_request.get()
        user = request.user if request is not None else None
        request_id = request.id if request is not None else None
        payload = serialise_buffer_to_payload(buffer, user, request_id)

        # If a request-level batch is active (the view wrapped the
        # entity loop in `request_change_logging_batch`), append the
        # payload to the batch and let the outer context manager
        # enqueue ONE consolidated job at request end. Otherwise
        # (e.g. a single-changeset endpoint that doesn't loop),
        # enqueue per entity as the fallback.
        #
        # In both cases `transaction.on_commit` ensures the work is
        # deferred until the apply atomic block commits - a rollback
        # discards the callback so the payload never reaches the
        # batch list or the queue.
        batch = _request_batch.get()
        if batch is not None:
            transaction.on_commit(lambda: batch.append(payload))
        else:
            transaction.on_commit(lambda: enqueue_async_write(payload))
    finally:
        _apply_change_buffer.reset(token)


def _consolidate_payloads(payloads):
    """
    Merge per-entity payloads into a single payload with all rows.

    Per-entity payloads share the same ``user_id``/``user_name``/
    ``request_id``/``branch_schema_id`` (one HTTP request, one user,
    one request id, one branch context), so we take those fields
    from the first payload and concatenate the row lists.
    """
    if not payloads:
        return {"rows": []}
    rows = []
    for p in payloads:
        rows.extend(p.get("rows") or [])
    first = payloads[0]
    consolidated = {
        "rows": rows,
        "user_id": first.get("user_id"),
        "user_name": first.get("user_name", ""),
        "request_id": first.get("request_id"),
    }
    if first.get("branch_schema_id") is not None:
        consolidated["branch_schema_id"] = first["branch_schema_id"]
    return consolidated


@contextmanager
def request_change_logging_batch():
    """
    Wrap a request that runs multiple ``buffered_change_logging`` cycles.

    Without this wrapper each per-entity ``buffered_change_logging``
    enqueues its own RQ job at end-of-entity. For endpoints that
    process many entities per HTTP request (``/bulk-plan-apply/``,
    ``/bulk-apply/``), that's one job per entity - small payloads,
    high enqueue rate, and the worker pays the RQ overhead per
    entity.

    Entering this context redirects each per-entity payload into a
    shared list. On exit (after all entities are processed), the
    list is consolidated into a single payload and enqueued as one
    RQ job. The worker then runs a single ``bulk_create`` covering
    every entity's ObjectChange rows in one INSERT.

    Per-entity rollback semantics are preserved: each entity's
    payload is appended via ``transaction.on_commit`` on its own
    atomic block. A failed entity rolls back its atomic, its
    on_commit callback is discarded, and its rows never reach the
    batch list. Successful entities contribute; failed ones don't.

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
        # Register the consolidate-and-enqueue via `transaction.on_commit`
        # rather than calling it inline so the ordering matches the
        # per-entity appends. Per-entity payloads are appended via
        # `transaction.on_commit` callbacks queued at the outermost
        # atomic; Django fires queued callbacks FIFO. Registering ours
        # last guarantees we see a fully-populated batch.
        #
        # When the request has no surrounding atomic block (the typical
        # production case for these endpoints), `on_commit` with no
        # active transaction fires the callback immediately - same
        # effect as calling enqueue_async_write directly.
        #
        # Registration is in `finally` so partial batches from
        # successful entities still get enqueued even if a later
        # entity's apply raised an unhandled exception that
        # interrupted the loop. Each entity's per-entity atomic
        # commits or rolls back independently; failed entities'
        # appends are discarded by Django, so the batch contains
        # only rows that genuinely committed.
        def _flush_batch():
            if batch:
                enqueue_async_write(_consolidate_payloads(batch))
        transaction.on_commit(_flush_batch)
        _request_batch.reset(token)
