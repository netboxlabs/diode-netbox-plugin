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

The bypass is opt-in via the plugin setting
``apply_bypass_counter_updates`` (default False). When enabled, this
module replaces ``utilities.counters.update_counter`` at import time
with a wrapper that consults a per-context flag
(``contextvars.ContextVar``). When ``bypass_counter_updates()`` is
active in the current execution context the wrapper returns
immediately; otherwise it delegates to the original. Counters drift
while the bypass is active.

When the setting is False (the default), this module performs no
monkey-patch and ``bypass_counter_updates()`` becomes a no-op context
manager — the plugin behaves exactly like upstream NetBox. Toggling
the setting requires a worker restart.

Why not patch and restore the module attribute per request? WSGI
runtimes that serve multiple requests per worker via a thread pool —
granian (the one we run with) and uWSGI with ``--threads`` — race on
the module attribute. The classic save/restore pattern can leave the
attribute pointing at the no-op after a concurrent enter/exit
sequence, breaking counter updates for the rest of the worker's life.
The one-time swap below happens before any request is served; the
per-request toggle is a ContextVar with no shared state.

ContextVar works correctly under plain threads, asyncio tasks, and
modern (1.5+) gevent greenlets — the latter is relevant if anyone
runs the plugin under ``uwsgi --gevent``.

Operational note: deployments that rely on accurate counters in the UI
should periodically run utilities.counters.update_counts(model, field,
related_query) to reconcile, or apply a similar background refresh.
Diode-driven writes are the primary source of bulk inserts so drift
between refreshes is bounded by ingest volume between refreshes.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from netbox.plugins import get_plugin_config
from utilities import counters

_bypass_active: ContextVar[bool] = ContextVar("diode_counter_bypass_active", default=False)

_enabled = bool(get_plugin_config("netbox_diode_plugin", "apply_bypass_counter_updates"))

_original_update_counter = counters.update_counter


@wraps(_original_update_counter)
def _guarded_update_counter(*args, **kwargs):
    if _bypass_active.get():
        return None
    return _original_update_counter(*args, **kwargs)


if _enabled:
    # One-time swap of the module attribute. Granian imports modules
    # once per worker before serving traffic, so this is not subject
    # to per-request races. Keep a module-level reference to the
    # original so we can call it on the non-bypassed path.
    counters.update_counter = _guarded_update_counter


@contextmanager
def bypass_counter_updates():
    """
    Suppress NetBox's per-write counter UPDATE in this context.

    No-op when ``apply_bypass_counter_updates`` is False.
    """
    if not _enabled:
        yield
        return
    token = _bypass_active.set(True)
    try:
        yield
    finally:
        _bypass_active.reset(token)
