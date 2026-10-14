#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Cross-process invalidation of the plugin's custom field definition caches.

Four apply-path modules keep CustomField-derived state in process memory:
the unique custom field matchers in ``matcher``, the per-model custom field
tuples in ``transformer`` and ``common``, and the opt-in
``customfield_cache`` wrapper. Each used to clear itself on CustomField
``post_save`` / ``post_delete``. Django signals fire only in the process that
performed the save, so with several worker processes (gunicorn, uwsgi, NGINX
Unit, or containers behind a load balancer alike) the one that served the
"edit custom field" request dropped its cache and the others kept the old
definition until a restart. Toggling "must be unique" therefore changed Diode
matching in one worker process and not the rest.

The caches stay process-level; only their invalidation is shared, through a
version token in the Django cache (Redis in NetBox):

- ``bump()`` runs on CustomField save and delete and on changes to its
  ``object_types`` membership. It publishes a fresh token and clears this
  process's caches at once, as the signals did before.
- ``sync()`` runs once per plugin API request (``CustomFieldVersionMiddleware``):
  one cache GET, and when the token moved since this process last looked,
  every registered cache is cleared before the request reads a definition.

Caches take part by handing ``register()`` a zero-argument clear callable.
The token is compared for inequality only. A missing key (never bumped, or
evicted) reads as a change, which costs one re-read of definitions from the
DB and nothing else.

Cache trouble never fails a request or a custom field save: both paths log a
warning and keep the per-process behaviour.
"""

import logging
import uuid
from collections.abc import Callable
from threading import Lock

from django.core.cache import cache as django_cache
from django.db.models.signals import m2m_changed, post_delete, post_save
from extras.models import CustomField

logger = logging.getLogger("netbox.diode.customfield_version")

CACHE_KEY = "diode:customfield:version"

_UNSEEN = object()
_lock = Lock()
_seen = _UNSEEN
_clearers: list[Callable[[], None]] = []


def register(clear: Callable[[], None]) -> None:
    """Enrol a zero-argument callable that drops one process-level custom field cache."""
    with _lock:
        _clearers.append(clear)


def _clear_local() -> None:
    for clear in _clearers:
        clear()


def bump(**kwargs) -> None:
    """Publish a new version for every process and clear this process's caches now."""
    global _seen
    token = uuid.uuid4().hex
    with _lock:
        _clear_local()
        try:
            django_cache.set(CACHE_KEY, token, timeout=None)
        except Exception:
            logger.warning(
                "custom field version not published; other workers keep their cached definitions until restart",
                exc_info=True,
            )
        else:
            _seen = token


def sync() -> None:
    """Drop this process's caches when another process published a newer version."""
    global _seen
    try:
        remote = django_cache.get(CACHE_KEY)
    except Exception:
        logger.warning("custom field version not readable; keeping this process's cached definitions", exc_info=True)
        return
    with _lock:
        if remote == _seen:
            return
        _clear_local()
        _seen = remote


def _on_object_types_changed(action, **kwargs) -> None:
    """A custom field joined or left a model, so that model's definitions moved."""
    if action.startswith("post_"):
        bump()


post_save.connect(bump, sender=CustomField, dispatch_uid="netbox_diode_plugin.customfield_version.post_save")
post_delete.connect(bump, sender=CustomField, dispatch_uid="netbox_diode_plugin.customfield_version.post_delete")
m2m_changed.connect(
    _on_object_types_changed,
    sender=CustomField.object_types.through,
    dispatch_uid="netbox_diode_plugin.customfield_version.object_types",
)


class CustomFieldVersionMiddleware:
    """Bring this process's custom field caches up to date before each plugin API request."""

    def __init__(self, get_response):
        """Keep the next handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Sync on plugin paths only; the rest of NetBox never reads these caches."""
        if "/plugins/diode/" in request.path:
            sync()
        return self.get_response(request)
