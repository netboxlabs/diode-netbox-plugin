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

This module disconnects the post_save and m2m_changed receivers for the
duration of the apply. The pre_delete receiver is intentionally left
connected: it also enforces protection-rule validation, which we must
keep firing, and deletes are rare in the auto-apply path.

Granian's WSGI workers serve one request per process at a time, so the
disconnect/reconnect is scoped to this request — concurrent requests in
the same process can't observe an unsignalled save.

Operational note: this drops audit-log entries for CREATE/UPDATE made
via diode-driven apply. Deployments that need a full audit trail for
those operations need to record provenance some other way (e.g.,
ChangeSet rows on the diode side already capture intended changes).
"""

from contextlib import contextmanager

from core.signals import handle_changed_object
from django.db.models.signals import m2m_changed, post_save


@contextmanager
def bypass_change_logging():
    """Disable per-save ObjectChange creation for CREATE/UPDATE/M2M for the block."""
    post_save.disconnect(handle_changed_object)
    m2m_changed.disconnect(handle_changed_object)
    try:
        yield
    finally:
        post_save.connect(handle_changed_object)
        m2m_changed.connect(handle_changed_object)
