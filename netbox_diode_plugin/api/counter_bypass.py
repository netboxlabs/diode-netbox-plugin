#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Bypass NetBox's per-write denormalized counter UPDATE during diode applies.

NetBox keeps cached counters on parent objects (Device.interface_count,
DeviceType.device_count, ...) up-to-date via post_save signals on the
child models. Each child write triggers a row-level UPDATE on the
parent's counter (see utilities.counters.update_counter).

Under concurrent auto-apply load this is a hot-row lock contention
problem: dozens of granian workers writing Devices that share the same
DeviceType all serialize on a row-level lock on that one DeviceType row.
pg_stat_statements observed mean=2.9s per UPDATE and ~99% of pg total
time in this one query.

This module monkey-patches utilities.counters.update_counter to a no-op
inside a context manager. Counters drift while the bypass is active.

Operational note: deployments that rely on accurate counters in the UI
should periodically run utilities.counters.update_counts(model, field,
related_query) to reconcile, or apply a similar background refresh.
Diode-driven writes are the primary source of bulk inserts so drift
between refreshes is bounded by ingest volume between refreshes.
"""

from contextlib import contextmanager

from utilities import counters


def _noop(*_args, **_kwargs):
    """Replacement for utilities.counters.update_counter that does nothing."""
    return None


@contextmanager
def bypass_counter_updates():
    """Disable NetBox's per-write counter UPDATE for the duration of the block.

    The patch is a module-attribute swap, so it affects only the current
    granian worker process for the duration of the request. Granian's WSGI
    interface serves one request per worker at a time, so the swap is
    effectively scoped to this request.
    """
    original = counters.update_counter
    counters.update_counter = _noop
    try:
        yield
    finally:
        counters.update_counter = original
