#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Bypass NetBox's per-write search-index caching signal during diode applies.

NetBox maintains a global search index in ``extras_cachedvalue``: one
row per indexed field per object. Every model save fires a post_save
receiver — ``search_backend.caching_handler`` — that inserts a fresh
batch of CachedValue rows for the saved instance. Under bulk
auto-apply this is the second-largest write source on NetBox-postgres
after the actual data tables (e.g. ~11% of pg total time at our
benchmark profile, ~5 indexed fields per Interface × hundreds of
thousands of inserts per bench run).

NetBox does *not* periodically reindex on its own. The daily
SystemHousekeepingJob runs prune/sessions/census tasks — no reindex.
The only ways to repopulate ``extras_cachedvalue`` after enabling
this bypass are:

- ``manage.py reindex [app_label[.ModelName] ...]`` run as a cron or
  k8s CronJob on whatever cadence matches the deployment's tolerance
  for stale search results, or
- a custom NetBox JobRunner registered via ``@system_job(interval=...)``
  that calls ``search_backend.cache(...)`` on diode-touched models.

This is opt-in via the plugin setting
``apply_bypass_search_indexing`` (default False). When enabled the
module swaps ``caching_handler`` out of the post_save registry once
at import time and replaces it with a wrapper that consults a
per-context flag (``contextvars.ContextVar``); when
``bypass_search_indexing()`` is active in the current execution
context the wrapper returns immediately. When the setting is False
the module performs no signal-registry mutation and the context
manager is a no-op. ``post_delete``'s ``removal_handler`` is
intentionally left untouched: deletes are rare on the auto-apply
path and removing stale CachedValue rows is correct.

Same one-time-swap-plus-ContextVar shape as
``change_log_bypass``/``counter_bypass`` — chosen for the same
reasons: per-request disconnect/reconnect races on the global signal
registry under granian/uwsgi worker thread pools, and
``threading.local`` is not greenlet-safe under ``uwsgi --gevent``.

Operational note: search index correctness is the user's
responsibility while this bypass is active — schedule a periodic
``manage.py reindex`` or accept stale search results.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db.models.signals import post_save
from netbox.plugins import get_plugin_config
from netbox.search.backends import search_backend

_bypass_active: ContextVar[bool] = ContextVar("diode_search_index_bypass_active", default=False)

_enabled = bool(get_plugin_config("netbox_diode_plugin", "apply_bypass_search_indexing"))

# caching_handler is a bound method on the search_backend singleton.
# Django's _make_id treats bound methods as (id(self), id(func)) so the
# disconnect call below matches the receiver registered when
# netbox.search.backends was imported.
_original_handler = search_backend.caching_handler


@wraps(_original_handler)
def _guarded_handler(sender, instance, created, **kwargs):
    if _bypass_active.get():
        return None
    return _original_handler(sender, instance, created, **kwargs)


if _enabled:
    # One-time swap of the connected receiver: done at import in each
    # granian worker process, before any request is served, so it is
    # not subject to the concurrency hazard the per-request
    # disconnect/reconnect approach hit. Keep a module-level reference
    # to the wrapper so Django's weakref does not die.
    post_save.disconnect(_original_handler)
    post_save.connect(_guarded_handler)


@contextmanager
def bypass_search_indexing():
    """
    Suppress NetBox's per-write search index UPDATE in this context.

    No-op when ``apply_bypass_search_indexing`` is False.
    """
    if not _enabled:
        yield
        return
    token = _bypass_active.set(True)
    try:
        yield
    finally:
        _bypass_active.reset(token)
