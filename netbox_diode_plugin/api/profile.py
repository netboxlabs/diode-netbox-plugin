#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Profile middleware and instrumentation."""

import contextvars
import logging
import os
import time
from collections import defaultdict
from functools import wraps

from django.db import connection

logger = logging.getLogger("netbox.diode.profile")

PROFILE_ENABLED = os.environ.get("DIODE_PROFILE", "").lower() in ("1", "true", "yes")

_profile_ctx = contextvars.ContextVar("diode_profile", default=None)


def get_profile_ctx():
    """Get the current request's profile context, or None."""
    return _profile_ctx.get(None)


class QueryCounter:
    """Database execute wrapper that counts queries and measures total DB time."""

    def __init__(self):
        self.count = 0
        self.total_time = 0.0

    def __call__(self, execute, sql, params, many, context):
        start = time.monotonic()
        try:
            return execute(sql, params, many, context)
        finally:
            self.total_time += time.monotonic() - start
            self.count += 1


class ProfileContext:
    """Stores profiling data for a single request."""

    def __init__(self, query_counter):
        self.start_time = time.monotonic()
        self.timings = defaultdict(lambda: {"count": 0, "total_ms": 0.0})
        self.counters = defaultdict(int)
        self.query_counter = query_counter

    def record_timing(self, name, duration_ms):
        entry = self.timings[name]
        entry["count"] += 1
        entry["total_ms"] += duration_ms

    def increment(self, name, amount=1):
        self.counters[name] += amount

    def db_query_snapshot(self):
        """Returns current query count for computing deltas."""
        return self.query_counter.count

    def summary(self):
        total_ms = (time.monotonic() - self.start_time) * 1000
        parts = [
            f"total_ms={total_ms:.1f}",
            f"db_queries={self.query_counter.count}",
            f"db_time_ms={self.query_counter.total_time * 1000:.1f}",
        ]

        for name, data in sorted(self.timings.items()):
            if data["count"] == 1:
                parts.append(f"{name}_ms={data['total_ms']:.1f}")
            else:
                parts.append(
                    f"{name}_calls={data['count']} {name}_ms={data['total_ms']:.1f}"
                )

        for name, count in sorted(self.counters.items()):
            parts.append(f"{name}={count}")

        return " ".join(parts)


def profiled(name):
    """Decorator to time a function and record it in the profile context.

    No-op when no profile context exists (i.e. outside of a profiled request).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx = _profile_ctx.get(None)
            if ctx is None:
                return func(*args, **kwargs)

            start = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                duration_ms = (time.monotonic() - start) * 1000
                ctx.record_timing(name, duration_ms)

        return wrapper

    return decorator


class DiodeProfileMiddleware:
    """Django middleware for profiling Diode plugin API requests.

    Wraps each request to the plugin API with:
    - Total request timing
    - DB query count and total DB time (via execute_wrapper)
    - Per-function timing collected via @profiled decorators
    - Named counters for matcher iterations, cache hits, etc.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not PROFILE_ENABLED or "/plugins/diode/" not in request.path:
            return self.get_response(request)

        query_counter = QueryCounter()
        ctx = ProfileContext(query_counter)
        token = _profile_ctx.set(ctx)

        try:
            with connection.execute_wrapper(query_counter):
                response = self.get_response(request)

            endpoint = request.path.rsplit("/plugins/diode/", 1)[-1]
            logger.info(
                "DIODE_PROFILE %s %s status=%d %s",
                request.method,
                endpoint,
                response.status_code,
                ctx.summary(),
            )
            return response
        finally:
            _profile_ctx.reset(token)
