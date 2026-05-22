#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Bypass NetBox's per-write ObjectChange (audit log) signal during diode applies.

NetBox writes one row into core_objectchange per save() of every tracked
model — a Device save fires post_save -> handle_changed_object ->
to_objectchange() -> ObjectChange.save() — plus the same flow on every
m2m_changed event. Under bulk auto-apply with N entities × ~6 changes
each, this is hundreds of thousands of per-write audit INSERTs per
batch, accumulating both Python signal-handler cost and a long tail of
small Postgres INSERTs.

The bypass is opt-in via the plugin setting
``apply_bypass_change_logging`` (default False). When enabled, this
module swaps NetBox's ObjectChange receiver out of the post_save and
m2m_changed registries at import time and replaces it with a wrapper
that consults a per-context flag (``contextvars.ContextVar``). When
``bypass_change_logging()`` is active in the current execution
context the wrapper returns immediately; otherwise it delegates to
the original. The pre_delete receiver is intentionally left
untouched: it also enforces protection-rule validation, which we
must keep firing, and deletes are rare in the auto-apply path.

When the setting is False (the default), this module performs no
mutation of the signal registry and ``bypass_change_logging()``
becomes a no-op context manager — the plugin behaves exactly like
upstream NetBox. Toggling the setting requires a worker restart.

Why not disconnect/reconnect per request? WSGI runtimes that serve
multiple requests per worker via a thread pool — granian (the one we
run with) and uWSGI with ``--threads`` — race on the global signal
registry. In practice we observed ~10% of disconnect calls return
False because another thread had already removed the receiver, and
reconnect() can leave duplicate or missing entries. The one-time swap
below happens before any request is served and is not subject to
that race.

Why ``contextvars.ContextVar`` rather than ``threading.local``?
ContextVar works correctly under plain threads, asyncio tasks, and
modern (1.5+) gevent greenlets — the latter is relevant if anyone
runs the plugin under ``uwsgi --gevent``, where threading.local would
be shared across greenlets in the same OS thread.

Operational note: this drops audit-log entries for CREATE/UPDATE made
via diode-driven apply. Deployments that need a full audit trail for
those operations need to record provenance some other way (e.g.,
ChangeSet rows on the diode side already capture intended changes).
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from core.signals import handle_changed_object as _original_handler
from django.db.models.signals import m2m_changed, post_save
from netbox.plugins import get_plugin_config

_bypass_active: ContextVar[bool] = ContextVar("diode_change_log_bypass_active", default=False)

_enabled = bool(get_plugin_config("netbox_diode_plugin", "apply_bypass_change_logging"))


@wraps(_original_handler)
def _guarded_handler(sender, instance, **kwargs):
    if _bypass_active.get():
        return None
    return _original_handler(sender, instance, **kwargs)


if _enabled:
    # One-time swap of the connected receiver: do this at import in
    # each granian worker process, before any request is served, so
    # it is not subject to the concurrency hazard the per-request
    # disconnect/reconnect approach hit. Keep a strong module-level
    # reference to the wrapper so Django's weakref does not die.
    post_save.disconnect(_original_handler)
    m2m_changed.disconnect(_original_handler)
    post_save.connect(_guarded_handler)
    m2m_changed.connect(_guarded_handler)


@contextmanager
def bypass_change_logging():
    """
    Suppress ObjectChange creation for CREATE/UPDATE/M2M in this context.

    No-op when ``apply_bypass_change_logging`` is False.
    """
    if not _enabled:
        yield
        return
    token = _bypass_active.set(True)
    try:
        yield
    finally:
        _bypass_active.reset(token)
