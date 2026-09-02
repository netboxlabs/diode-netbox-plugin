#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Buffer NetBox's per-write counter UPDATEs during a diode apply and flush them once per transaction.

NetBox updates denormalized parent counters (``Device.interface_count``,
``DeviceType.device_count``, ...) with one row UPDATE per child write.
Postgres holds that row lock until COMMIT, so a parent row stays locked
from the first child save to the end of the apply -- measured mean 2.9s
on this statement, ~99% of NetBox's postgres time under concurrent load.

``counter_bypass`` answers that by skipping the update entirely and
leaving drift to a periodic ``update_counts``. This module takes the
other trade: deltas accumulate in memory, sum (so N children of one
parent collapse to ``+N``, and net-zero drops out), and flush as one
statement per counter at the end of the apply. Counters stay exact; what
goes away is holding the lock for the apply's whole duration.

Gated by ``apply_buffer_counter_updates`` (default False). If
``apply_bypass_counter_updates`` is also active, bypass wins.

Two constraints are load-bearing and not visible from the code:

- **The flush must stay inside the transaction.** Deferring it to
  ``transaction.on_commit`` (as ``change_log_buffer`` does) would look
  like a further optimisation, but an ObjectChange is an append-only
  audit row while a counter delta is not: running it after COMMIT means
  a crash in between silently loses the increment, which is the drift
  this module exists to avoid.

- **One sorted statement per counter.** Splitting the flush by delta
  value would be simpler, but it lets two workers walk the same parent
  rows in different orders -- which is how a batched counter update
  manufactures deadlocks. Groups are iterated sorted and pks sorted
  within each statement (chunks are sorted slices) so every worker
  approaches the same rows the same way.
"""

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db.models import BigIntegerField, Case, F, When
from netbox.plugins import get_plugin_config
from utilities import counters

from .counter_bypass import _bypass_active

# Bounds the generated CASE expression on very large applies.
_CHUNK_SIZE = 1000

# `dict[tuple[model, counter_name], dict[pk, delta]]`, or None when no
# buffer is active in this context and the wrapper should delegate.
_apply_counter_buffer: ContextVar = ContextVar(
    "diode_apply_counter_buffer", default=None
)

# Importing `counter_bypass` above has already run that module's swap, so
# this picks up its guarded wrapper when the bypass is enabled rather than
# the untouched original.
_previous_update_counter = counters.update_counter


@wraps(_previous_update_counter)
def _buffered_update_counter(model, pk, counter_name, value, using=None, **kwargs):
    """Record the delta while a buffer is active; otherwise delegate upstream.

    NetBox 4.7 passes ``using`` through the counter signal handlers; older
    versions do not accept it, so it is forwarded only when set. A delta
    bound for a non-default database is never buffered - the flush writes
    via the default alias, so buffering it would apply the delta to the
    wrong database.
    """
    if using is not None:
        kwargs["using"] = using
    buffer = _apply_counter_buffer.get()
    if buffer is None or using not in (None, "default"):
        return _previous_update_counter(model, pk, counter_name, value, **kwargs)

    # Bypass wins: no update, and nothing buffered to apply later either.
    if _bypass_active.get():
        return None

    if pk is None:
        return None

    buffer[(model, counter_name)][pk] += value
    return None


# Installed unconditionally -- the wrapper gates itself on the contextvar,
# so one import-time swap is cheaper than re-wiring per request, and runs
# before any request is served.
counters.update_counter = _buffered_update_counter


def _flush_counter_deltas(buffer):
    """
    Apply the accumulated deltas as one UPDATE per ``(model, counter_name)``.

    Ordering here is load-bearing; see the module docstring. Rows whose net
    delta is zero are skipped, and rows deleted earlier in the transaction
    simply match nothing.
    """
    for (model, counter_name), deltas in sorted(
        buffer.items(), key=lambda item: (item[0][0]._meta.label_lower, item[0][1])
    ):
        pks = sorted(pk for pk, delta in deltas.items() if delta)
        if not pks:
            continue

        for start in range(0, len(pks), _CHUNK_SIZE):
            chunk = pks[start:start + _CHUNK_SIZE]
            model.objects.filter(pk__in=chunk).update(
                **{
                    counter_name: Case(
                        *[
                            When(pk=pk, then=F(counter_name) + deltas[pk])
                            for pk in chunk
                        ],
                        default=F(counter_name),
                        output_field=BigIntegerField(),
                    )
                }
            )


@contextmanager
def buffered_counter_updates():
    """
    Accumulate counter deltas during an apply and flush them just before commit.

    No-op when ``apply_buffer_counter_updates`` is False (the default).

    Must be nested inside the apply's ``transaction.atomic()`` -- the flush
    runs on the way out of this context manager, while that transaction is
    still open. A raising body skips the flush and rolls back with everything
    else.
    """
    if not get_plugin_config("netbox_diode_plugin", "apply_buffer_counter_updates"):
        yield
        return

    buffer = defaultdict(lambda: defaultdict(int))
    token = _apply_counter_buffer.set(buffer)
    try:
        yield
        if buffer:
            _flush_counter_deltas(buffer)
    finally:
        _apply_counter_buffer.reset(token)
