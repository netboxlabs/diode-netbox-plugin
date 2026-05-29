#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Async ObjectChange creation via NetBox's RQ workers.

Companion module to ``change_log_buffer``. During apply, the buffer
collects ObjectChange instances in memory. Instead of running
``bulk_create`` + ``post_save`` re-emit in the apply request thread
(which keeps the FK-lock SELECTs, Python serialisation, and signal
fan-out on the request critical path), this module serialises the
buffer to a plain-dict payload and enqueues a background job via
``django_rq``. The apply transaction commits without paying any of
the change-logging-write cost; an RQ worker drains the queue and
performs the writes on its own time.

The trade-offs are deliberate:

  - Audit log becomes eventually-consistent. Reads of
    ``core_objectchange`` immediately after an apply may miss the
    just-applied changes until the worker drains. Typical lag with
    a healthy queue is sub-second.

  - Receivers connected to ``post_save(sender=ObjectChange)`` fire
    inside the worker rather than in the apply request thread. The
    worker re-establishes per-request context (user, request_id,
    and the ``active_branch`` contextvar if ``netbox-branching`` is
    installed) from the serialised payload before re-emitting
    ``post_save`` so receivers see the same inputs they would on
    the synchronous path.

  - Worker failures are caught by RQ's retry semantics
    (``rq.Retry`` with exponential backoff). After retries exhaust,
    the job lands on the RQ failed queue, visible via
    ``rq-dashboard`` or the django_rq admin. The audit gap is
    observable and can be replayed manually.

  - We use ``django_rq.get_queue(...).enqueue(...)`` directly rather
    than ``netbox.jobs.JobRunner.enqueue(...)`` because JobRunner
    persists one ``core_job`` row per execution; at 30 e/s (~50k
    apply requests/day) that creates significant table bloat with no
    operational value per ObjectChange. The same lightweight pattern
    is what NetBox itself uses for webhook delivery
    (``extras/events.py``).

The worker function ``write_object_changes_async`` is the entry
point RQ calls. It MUST stay backward-compatible at the payload
boundary - older queued jobs created by previous code versions can
arrive at a freshly-deployed worker. Add fields, never remove or
rename.
"""

import logging
from contextvars import copy_context
from typing import Any

import django_rq
from core.models import ObjectChange
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rq import Retry

logger = logging.getLogger(__name__)

# NetBox ships an RQ queue named "default" and one named "low".
# Webhook delivery goes via "default". We use the same so this
# inherits the same worker pool and scaling story.
_QUEUE_NAME = "default"

# Retry tuning. RQ executes attempt #1 immediately, then waits
# `intervals[N-1]` seconds before attempt N+1. After all attempts
# exhaust, the job moves to the failed queue.
#
# Total dead-letter window: 5 + 30 + 120 = 155 seconds. Tuned for
# transient DB / cache issues, not for sustained outages (where the
# audit gap is going to be visible regardless).
_RETRY_INTERVALS = [5, 30, 120]


def enqueue_async_write(payload: dict[str, Any]) -> None:
    """
    Enqueue an RQ job to write the buffered ObjectChange rows.

    Called from ``transaction.on_commit`` so that the job is enqueued
    only if the apply transaction succeeded. A rolled-back apply
    drops the buffered payload without ever reaching this function.
    """
    if not payload.get("rows"):
        return
    queue = django_rq.get_queue(_QUEUE_NAME)
    queue.enqueue(
        write_object_changes_async,
        payload,
        retry=Retry(max=len(_RETRY_INTERVALS), interval=_RETRY_INTERVALS),
        # Job timeout: 60s is plenty for a few hundred ObjectChange
        # writes including branching/eventsink downstream work.
        job_timeout=60,
        # Tag the job for observability via rq-dashboard / django_rq.
        description=f"diode change-log flush ({len(payload['rows'])} rows)",
    )


def write_object_changes_async(payload: dict[str, Any]) -> None:
    """
    RQ worker entry point. Rebuilds the ObjectChange rows and emits signals.

    ``payload`` shape (kept stable for cross-deploy compatibility):

      {
        "rows": [
          {
            "time": "2026-05-29T07:00:00+00:00",     # ISO8601 string
            "action": "create" | "update" | "delete",
            "changed_object_type_id": <int>,
            "changed_object_id": <int>,
            "related_object_type_id": <int> | None,
            "related_object_id": <int> | None,
            "object_repr": "<str>",
            "prechange_data": {...} | None,           # JSON-able
            "postchange_data": {...} | None,
          },
          ...
        ],
        "user_id": <int> | None,
        "user_name": "<str>",
        "request_id": "<uuid string>",
        "branch_schema_id": "<str>" | None,           # for netbox-branching
      }
    """
    rows = payload.get("rows") or []
    if not rows:
        return

    # Re-establish the optional `active_branch` contextvar (used by
    # `netbox-branching` when installed) so any receiver that
    # depends on it sees the same context the apply request thread
    # would have. Import inside the block so deployments without
    # the plugin do not pay the import cost.
    branch_token = None
    branch_schema_id = payload.get("branch_schema_id")
    if branch_schema_id:
        try:
            from netbox_branching.contextvars import active_branch
            from netbox_branching.models import Branch
            branch = Branch.objects.filter(schema_id=branch_schema_id).first()
            if branch is not None:
                branch_token = active_branch.set(branch)
        except ImportError:
            # active_branch consumer isn't installed in this
            # deployment; the payload carried a value but we cannot
            # honour it. Info-level because it's likely a config
            # mismatch rather than an error.
            logger.info(
                "branch_schema_id %s in payload but active_branch consumer "
                "is not installed; ObjectChange rows will be created without "
                "branch attribution",
                branch_schema_id,
            )

    try:
        instances = [_payload_row_to_objectchange(row, payload) for row in rows]
        created = ObjectChange.objects.bulk_create(instances)

        # bulk_create does NOT fire post_save. Manually re-emit so
        # any receiver connected to `post_save(sender=ObjectChange)`
        # sees each row exactly once.
        #
        # Pass the full kwargs set Django's own `Model._save_table`
        # would pass: some NetBox receivers (e.g.
        # `update_denormalized_fields`) declare `raw` as a required
        # positional argument.
        for obj in created:
            post_save.send(
                sender=ObjectChange,
                instance=obj,
                created=True,
                update_fields=None,
                raw=False,
                using=obj._state.db,
            )

        logger.info(
            "wrote %d ObjectChange rows via async path (request_id=%s)",
            len(created),
            payload.get("request_id"),
        )
    finally:
        if branch_token is not None:
            from netbox_branching.contextvars import active_branch
            active_branch.reset(branch_token)


def _payload_row_to_objectchange(row: dict[str, Any], payload: dict[str, Any]) -> ObjectChange:
    """Build an unsaved ObjectChange instance from a serialised payload row."""
    # `time` is auto_now_add on the field. When we bulk_create an
    # instance that already has `time` set, Django uses the provided
    # value (per Django docs on auto_now_add + bulk_create). This
    # preserves the actual moment the change happened, not when the
    # worker got around to writing it. Without this, the audit log
    # would show all changes at "worker drain time" rather than
    # "apply time" - useless for debugging.
    return ObjectChange(
        time=row["time"],
        user_id=payload.get("user_id"),
        user_name=payload.get("user_name", ""),
        request_id=payload.get("request_id"),
        action=row["action"],
        changed_object_type_id=row["changed_object_type_id"],
        changed_object_id=row["changed_object_id"],
        related_object_type_id=row.get("related_object_type_id"),
        related_object_id=row.get("related_object_id"),
        object_repr=row.get("object_repr", ""),
        prechange_data=row.get("prechange_data"),
        postchange_data=row.get("postchange_data"),
    )


def serialise_buffer_to_payload(buffer: dict, user, request_id) -> dict[str, Any]:
    """
    Convert an in-memory buffer of ObjectChange instances to a job payload.

    Called by ``change_log_buffer.buffered_change_logging`` at end of
    the apply context. The resulting dict is what gets enqueued onto
    the RQ queue; it must be picklable and contain no live model
    instances or non-serialisable references.
    """
    rows = []
    for objectchange in buffer.values():
        rows.append({
            # Serialise time as ISO8601 string; psycopg+Django will
            # parse it back on the worker side.
            "time": objectchange.time.isoformat() if objectchange.time else None,
            "action": objectchange.action,
            "changed_object_type_id": objectchange.changed_object_type_id,
            "changed_object_id": objectchange.changed_object_id,
            "related_object_type_id": objectchange.related_object_type_id,
            "related_object_id": objectchange.related_object_id,
            "object_repr": objectchange.object_repr,
            "prechange_data": objectchange.prechange_data,
            "postchange_data": objectchange.postchange_data,
        })

    payload: dict[str, Any] = {
        "rows": rows,
        "user_id": user.pk if user and user.is_authenticated else None,
        "user_name": user.username if user and user.is_authenticated else "",
        "request_id": str(request_id) if request_id else None,
    }

    # Branching context, captured if active.
    try:
        from netbox_branching.contextvars import active_branch
        branch = active_branch.get()
        if branch is not None:
            payload["branch_schema_id"] = branch.schema_id
    except ImportError:
        pass

    return payload
