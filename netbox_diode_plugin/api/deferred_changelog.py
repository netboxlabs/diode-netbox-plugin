#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Deferred ObjectChange writes for diode apply paths.

PR 3 of BULK-ORM Sprint 1, with the V3 atomicity fix layered on top.

NetBox's ``core.signals.handle_changed_object`` fires per-instance on
``post_save`` / ``m2m_changed`` and persists each ``ObjectChange`` row
with its own INSERT. For a multi-thousand-entity changeset that's the
dominant write cost.

This module installs a module-level monkey-patch on
``core.models.ObjectChange.save`` that, when a thread-local flag is
active, buffers the instance instead of writing it. The
``deferred_changelog()`` context manager activates the flag and yields
a ``_Deferred`` accumulator with three methods:

* ``commit_pending()`` — promote the per-changeset *pending* buffer
  into the cross-changeset *batch* buffer. Called after a changeset's
  inner ``transaction.atomic()`` block completes successfully.
* ``rollback_pending()`` — discard the per-changeset *pending* buffer.
  Called when a changeset's inner atomic block raises and its writes
  are rolled back. Without this the buffered ``ObjectChange`` rows
  would still get flushed, leaking audit-log entries pointing at
  rolled-back data.
* ``flush()`` — issue ``ObjectChange.objects.bulk_create()`` for the
  accumulated batch buffer. Caller invokes this once at the end of
  the loop, **inside** the outer ``transaction.atomic()`` so a flush
  failure rolls back the whole batch.

m2m merge dedup is preserved by ``(content_type_id, object_id,
request_id)`` keys spanning both buffers: a follow-up m2m_changed
fire collapses onto whichever buffer holds the prior row, yielding
a single audit row per object regardless of when within the batch
the dedup happens.

Nested usage is a no-op (outer wins).
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

from core.models import ObjectChange

logger = logging.getLogger(__name__)

_state = threading.local()
_BATCH_SIZE = 500


def _is_active() -> bool:
    return getattr(_state, "active", False)


def _get_deferred():
    return getattr(_state, "deferred", None)


class _Deferred:
    """Per-window accumulator for buffered ``ObjectChange`` writes."""

    def __init__(self) -> None:
        self.pending_buffer: list[ObjectChange] = []
        self.pending_index: dict[tuple, int] = {}
        self.batch_buffer: list[ObjectChange] = []
        self.batch_index: dict[tuple, int] = {}

    def _add(self, obj: ObjectChange) -> None:
        """Buffer ``obj`` into pending; merge onto pending or batch on dedup."""
        key = (
            obj.changed_object_type_id,
            obj.changed_object_id,
            str(obj.request_id) if obj.request_id is not None else None,
        )
        pos = self.pending_index.get(key)
        if pos is not None:
            self.pending_buffer[pos].postchange_data = obj.postchange_data
            return
        batch_pos = self.batch_index.get(key)
        if batch_pos is not None:
            self.batch_buffer[batch_pos].postchange_data = obj.postchange_data
            return
        self.pending_index[key] = len(self.pending_buffer)
        self.pending_buffer.append(obj)

    def commit_pending(self) -> None:
        """Promote pending entries onto the batch buffer; clear pending."""
        for key, pos in self.pending_index.items():
            obj = self.pending_buffer[pos]
            existing = self.batch_index.get(key)
            if existing is not None:
                self.batch_buffer[existing].postchange_data = obj.postchange_data
            else:
                self.batch_index[key] = len(self.batch_buffer)
                self.batch_buffer.append(obj)
        self.pending_buffer = []
        self.pending_index = {}

    def rollback_pending(self) -> None:
        """Discard the pending buffer (e.g. after a savepoint rollback)."""
        self.pending_buffer = []
        self.pending_index = {}

    def flush(self) -> None:
        """Drain pending into batch, then bulk-insert and clear the batch buffer.

        Calling ``flush()`` without first calling ``commit_pending()`` is the
        legacy single-window pattern (``with deferred_changelog(): ...`` with
        no per-changeset accumulator). It commits any leftover pending entries
        before issuing the INSERT so callers that don't use the V3 lifecycle
        API still get a single bulk_create.
        """
        if self.pending_buffer:
            self.commit_pending()
        if self.batch_buffer:
            ObjectChange.objects.bulk_create(self.batch_buffer, batch_size=_BATCH_SIZE)
        self.batch_buffer = []
        self.batch_index = {}


_original_save = ObjectChange.save


def _patched_save(self, *args, **kwargs):
    """ObjectChange.save replacement: buffers when deferred_changelog() is active."""
    if not _is_active():
        return _original_save(self, *args, **kwargs)

    deferred = _get_deferred()
    if deferred is None:
        return _original_save(self, *args, **kwargs)

    if self.pk is not None:
        # Already in DB (e.g. m2m post_clear updating an externally-saved
        # change row). Fall back to the real save for correctness.
        return _original_save(self, *args, **kwargs)

    if not self.user_name and getattr(self, "user_id", None):
        self.user_name = self.user.username
    if not self.object_repr:
        try:
            self.object_repr = str(self.changed_object) if self.changed_object_id else ""
        except Exception:
            self.object_repr = ""

    deferred._add(self)
    return None


# Install the monkey-patch at import time; views.py imports this module.
ObjectChange.save = _patched_save


@contextmanager
def deferred_changelog():
    """
    Activate deferred ``ObjectChange`` persistence; yield a ``_Deferred``.

    Two usage modes are supported:

    * Legacy (single-window) — ``with deferred_changelog(): ...``. All saves
      go to the pending buffer; on clean context exit the manager auto-calls
      ``flush()``, draining pending into batch and issuing one
      ``bulk_create``. If the body raises, no flush happens, so an unhandled
      exception cannot leak audit-log rows.

    * V3 (per-changeset accumulator) — ``with deferred_changelog() as defc:``
      with explicit ``defc.commit_pending()`` / ``defc.rollback_pending()``
      after each savepoint and ``defc.flush()`` once at the end inside the
      outer ``transaction.atomic()``. The auto-flush at clean context exit
      is a no-op because ``flush()`` already cleared the buffers.

    Nested invocation is a no-op (outer wins).
    """
    if _is_active():
        yield _get_deferred()
        return

    deferred = _Deferred()
    _state.active = True
    _state.deferred = deferred
    raised = False
    try:
        yield deferred
    except Exception:
        raised = True
        raise
    finally:
        _state.active = False
        _state.deferred = None
        if not raised:
            deferred.flush()
