#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Process-level cache for ``CustomField.objects.get_for_model()``.

NetBox ships a request-scoped cache for ``CustomFieldManager.get_for_model``
in ``extras.models.customfields``, but the hit check uses Python's walrus
operator with truthiness:

    if custom_fields := cache['custom_fields'].get(model._meta.model):
        return custom_fields

An empty QuerySet (model with no custom fields defined) is **falsy**, so
the cache hit branch is skipped and the DB query runs every time. For
Diode bulk-apply workloads against NetBox deployments where most models
have no custom fields, this re-queries ``extras_customfield`` on every
``instance.clean()`` and ``instance.save()`` - observed at ~558 calls/sec
returning ~0 rows/sec during sustained ``/bulk-plan-apply`` load on
RDS Performance Insights.

This module installs a process-level wrapper around
``CustomFieldManager.get_for_model`` that:

  - Caches the QuerySet object per ``model._meta.model`` for the lifetime
    of the worker process (not per request).
  - Force-evaluates the QuerySet inside the wrapper so its internal
    ``_result_cache`` is populated before it enters the cache. Subsequent
    iterations across requests use the in-memory cache, never the DB.
  - Uses an explicit sentinel for the "not cached" check, so empty
    QuerySets cache correctly (the bug above).
  - Returns the same QuerySet object on every hit. Callers that chain
    ``.filter()`` / ``.exclude()`` (e.g. ``matcher.py``) continue to work
    unchanged - those chains create new QuerySets that hit the DB on
    iteration, same as before.
  - Invalidates the entire cache on ``post_save`` / ``post_delete`` of
    CustomField, matching the per-call-site lru_cache pattern already
    used by ``transformer.py``, ``matcher.py``, and ``common.py``.

Opt-in via the plugin setting ``apply_bypass_customfield_query_cache``
(default False). When False the module performs no method-table mutation.

This is install-once-at-import (per worker process) like the other
apply-path bypasses; threading.Lock guards concurrent first-fill from
multiple worker threads within a single process.
"""

from threading import Lock

from django.db.models.signals import post_delete, post_save
from extras.models import CustomField
from netbox.plugins import get_plugin_config

_enabled = bool(get_plugin_config("netbox_diode_plugin", "apply_bypass_customfield_query_cache"))

# Sentinel distinguishes "not yet cached" from "cached as empty QuerySet".
_MISSING = object()

_cache: dict = {}
_cache_lock = Lock()

_original_get_for_model = CustomField.objects.__class__.get_for_model


def _cached_get_for_model(self, model):
    """Process-level cached replacement for CustomFieldManager.get_for_model.

    Returns the same QuerySet object on cache hit, with its internal result
    cache pre-populated so iteration does not hit the DB.
    """
    model_class = model._meta.model
    with _cache_lock:
        cached = _cache.get(model_class, _MISSING)
    if cached is not _MISSING:
        return cached

    queryset = _original_get_for_model(self, model)
    # Force evaluation now so the QuerySet's _result_cache is populated
    # before any other thread starts iterating it from the cache.
    list(queryset)
    with _cache_lock:
        _cache[model_class] = queryset
    return queryset


def _invalidate(**kwargs):
    with _cache_lock:
        _cache.clear()


if _enabled:
    # One-time swap at import - done in each worker process before any
    # request is served. The signal handlers stay connected for the
    # process lifetime.
    CustomField.objects.__class__.get_for_model = _cached_get_for_model
    post_save.connect(_invalidate, sender=CustomField)
    post_delete.connect(_invalidate, sender=CustomField)
