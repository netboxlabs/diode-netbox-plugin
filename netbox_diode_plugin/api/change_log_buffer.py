#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Buffer NetBox's ObjectChange writes during diode applies and flush as a single bulk_create.

NetBox's ``handle_changed_object`` receiver (``core/signals.py``) does up
to three DB round-trips per saved model: one INSERT into
``core_objectchange`` for non-m2m saves, and for m2m_changed events a
SELECT to find any prior ObjectChange recorded for the same instance in
the current request followed by an UPDATE if one is found. Under bulk
auto-apply (a 50-entity batch with a couple of m2m fields per entity)
that lands around 150-300 round-trips per HTTP request, which costs the
plugin roughly 50% of its throughput compared to the existing
``apply_bypass_change_logging`` shortcut.

This module preserves the audit trail (and the downstream consumers
that depend on it - the branching plugin's ChangeDiff machinery and
the NBC eventsink stream) while collapsing those round-trips into one.
It does so in two pieces:

  1. A request-scoped buffer (a ``contextvars.ContextVar`` holding a
     dict keyed by ``(content_type_id, changed_object_id)``) gathers
     ObjectChange instances in memory instead of saving each one
     individually. m2m_changed events for an object already in the
     buffer merge their ``postchange_data`` in memory, which replaces
     the upstream SELECT-then-UPDATE pair with a dict lookup.
  2. On successful exit of ``buffered_change_logging()`` the buffered
     rows are flushed via ``ObjectChange.objects.bulk_create(...)`` and
     a ``post_save`` signal is manually re-emitted for each created
     row. The re-emit is what keeps the branching plugin's
     ``record_change_diff`` receiver firing - ``bulk_create`` does not
     fire ``post_save`` on its own.

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

The flush happens *inside* the apply transaction (the ``with`` block
in ``_apply_one_changeset`` runs the context manager nested inside
``transaction.atomic()``), so if the apply raises after the flush has
written its bulk_create rows, the outer ``atomic`` block rolls those
rows back along with everything else.

Pre_delete is intentionally left untouched: NetBox's delete handler
runs protection-rule validation, which must keep firing, and deletes
are uncommon on the auto-apply path.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from core.choices import ObjectChangeActionChoices
from core.events import OBJECT_CREATED, OBJECT_UPDATED
from core.models import ObjectChange
from core.signals import handle_changed_object as _original_handler
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_save
from extras.events import enqueue_event
from extras.models import Tag
from netbox.context import current_request, events_queue
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

# Request-scoped buffer. The value type is `dict[tuple[int, int], ObjectChange]`
# keyed by (content_type_id, changed_object_id). A value of None
# means the context manager is not active and we should delegate.
_apply_change_buffer: ContextVar = ContextVar(
    "diode_apply_change_buffer", default=None
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
    Collect ObjectChange writes during apply and flush them as a single bulk_create.

    No-op when ``apply_buffer_change_logging`` is False (the default),
    which means the buffered handler still runs but delegates straight
    through to upstream NetBox without touching the buffer.

    The flush runs inside the caller's transaction, so if the caller's
    outer transaction rolls back the bulk_create rolls back with it.
    Raising inside the ``with`` block skips the flush entirely - the
    buffered ObjectChange instances are dropped without being
    persisted.
    """
    if not get_plugin_config("netbox_diode_plugin", "apply_buffer_change_logging"):
        yield
        return

    token = _apply_change_buffer.set({})
    flushed_buffer = None
    try:
        yield
        # Capture the buffer for a successful exit. Reading via .get()
        # after a successful yield gives us the populated dict; we
        # defer the actual flush until after `reset(token)` so any
        # post_save signals re-emitted during the flush do not
        # re-enter the buffer path.
        flushed_buffer = _apply_change_buffer.get()
    finally:
        _apply_change_buffer.reset(token)

    if not flushed_buffer:
        return

    created = ObjectChange.objects.bulk_create(list(flushed_buffer.values()))
    # bulk_create does not fire post_save. Manually re-emit so that
    # receivers connected to ObjectChange (notably the branching
    # plugin's `record_change_diff`) see each ObjectChange exactly
    # once, with `created=True` matching the action that produced it.
    #
    # Pass the full kwargs set that Django's own `Model._save_table`
    # would pass: some NetBox receivers (e.g. `update_denormalized_fields`)
    # declare `raw` as a required positional argument and would raise
    # TypeError if we sent the signal with only `instance`/`created`.
    for obj in created:
        post_save.send(
            sender=ObjectChange,
            instance=obj,
            created=True,
            update_fields=None,
            raw=False,
            using=obj._state.db,
        )
