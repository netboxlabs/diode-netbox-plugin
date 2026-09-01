#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - buffered_counter_updates tests."""

import uuid
from types import SimpleNamespace
from unittest import mock

from dcim.models import (
    ConsolePortTemplate,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    InterfaceTemplate,
    Manufacturer,
    Site,
)
from django.db import connection, transaction
from django.db.models import When
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import counter_buffer
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.counter_buffer import (
    _flush_counter_deltas,
    buffered_counter_updates,
)
from netbox_diode_plugin.plugin_config import get_diode_user


def _enabled():
    """Turn the `apply_buffer_counter_updates` gate on for the duration of a block."""
    return mock.patch.object(counter_buffer, "get_plugin_config", return_value=True)


class BufferedCounterUpdatesTestCase(TestCase):
    """Behaviour of the `buffered_counter_updates` context manager over real ORM writes."""

    def setUp(self):
        """Two DeviceTypes sharing a manufacturer - cheap parents with several counters each."""
        mfr = Manufacturer.objects.create(name="bcu-mfr", slug="bcu-mfr")
        self.dt = DeviceType.objects.create(manufacturer=mfr, model="bcu-dt", slug="bcu-dt")
        self.other_dt = DeviceType.objects.create(
            manufacturer=mfr, model="bcu-dt2", slug="bcu-dt2"
        )

    def _stored(self, device_type=None, field="interface_template_count"):
        """Read a counter column straight from the DB, bypassing any in-memory instance."""
        dt = device_type or self.dt
        return DeviceType.objects.filter(pk=dt.pk).values_list(field, flat=True)[0]

    def _add_interface_templates(self, count, device_type=None):
        """Create `count` InterfaceTemplates, each of which increments the parent's counter."""
        dt = device_type or self.dt
        return [
            InterfaceTemplate.objects.create(
                device_type=dt, name=f"eth{i}-{uuid.uuid4().hex[:6]}", type="1000base-t"
            )
            for i in range(count)
        ]

    @staticmethod
    def _counter_updates(captured):
        """Return the UPDATE statements captured against the DeviceType table."""
        return [
            q["sql"]
            for q in captured.captured_queries
            if q["sql"].lstrip().upper().startswith("UPDATE")
            and "dcim_devicetype" in q["sql"]
        ]

    # --- Setting OFF: pass-through, upstream updates synchronously ---

    def test_setting_false_updates_counter_synchronously(self):
        """With the setting off (default), each child write updates the counter immediately."""
        with buffered_counter_updates():
            self._add_interface_templates(2)
            # No buffer: upstream's per-write UPDATE has already landed.
            self.assertEqual(self._stored(), 2)
        self.assertEqual(self._stored(), 2)

    def test_setting_false_issues_one_update_per_child(self):
        """The unbuffered path is exactly what it was before: one statement per child write."""
        with CaptureQueriesContext(connection) as captured:
            with buffered_counter_updates():
                self._add_interface_templates(3)
        self.assertEqual(len(self._counter_updates(captured)), 3)

    # --- Setting ON: deltas accumulate, flush lands at context exit ---

    def test_deltas_are_withheld_until_the_flush(self):
        """Inside the context the parent row is untouched; on exit the total is applied."""
        with _enabled(), buffered_counter_updates():
            self._add_interface_templates(3)
            # The whole point: the parent row has not been locked yet.
            self.assertEqual(self._stored(), 0)
        self.assertEqual(self._stored(), 3)

    def test_deltas_coalesce_into_one_statement(self):
        """N children of one parent collapse to a single UPDATE carrying +N."""
        with CaptureQueriesContext(connection) as captured:
            with _enabled(), buffered_counter_updates():
                self._add_interface_templates(5)

        self.assertEqual(len(self._counter_updates(captured)), 1)
        self.assertEqual(self._stored(), 5)

    def test_net_zero_delta_issues_no_statement(self):
        """A child created and deleted in the same apply nets to zero and is never written."""
        with CaptureQueriesContext(connection) as captured:
            with _enabled(), buffered_counter_updates():
                created = self._add_interface_templates(1)
                created[0].delete()

        self.assertEqual(self._counter_updates(captured), [])
        self.assertEqual(self._stored(), 0)

    def test_delete_decrements(self):
        """post_delete deltas are buffered and flushed the same way as post_save ones."""
        templates = self._add_interface_templates(3)
        self.assertEqual(self._stored(), 3)

        with _enabled(), buffered_counter_updates():
            templates[0].delete()
            templates[1].delete()
            self.assertEqual(self._stored(), 3)
        self.assertEqual(self._stored(), 1)

    def test_reparent_splits_delta_across_both_parents(self):
        """Moving a child buffers -1 on the old parent and +1 on the new one."""
        template = self._add_interface_templates(1)[0]
        self.assertEqual(self._stored(), 1)

        with _enabled(), buffered_counter_updates():
            template.device_type = self.other_dt
            template.save()
            self.assertEqual(self._stored(), 1)
            self.assertEqual(self._stored(self.other_dt), 0)

        self.assertEqual(self._stored(), 0)
        self.assertEqual(self._stored(self.other_dt), 1)

    def test_distinct_deltas_share_one_statement(self):
        """Two parents with different deltas are carried by one CASE expression, not two UPDATEs."""
        with CaptureQueriesContext(connection) as captured:
            with _enabled(), buffered_counter_updates():
                self._add_interface_templates(1)
                self._add_interface_templates(3, device_type=self.other_dt)

        self.assertEqual(len(self._counter_updates(captured)), 1)
        self.assertEqual(self._stored(), 1)
        self.assertEqual(self._stored(self.other_dt), 3)

    def test_separate_counters_get_one_statement_each(self):
        """Grouping is per (model, counter field): two counters on one model, two statements."""
        with CaptureQueriesContext(connection) as captured:
            with _enabled(), buffered_counter_updates():
                self._add_interface_templates(2)
                ConsolePortTemplate.objects.create(device_type=self.dt, name="con0")

        self.assertEqual(len(self._counter_updates(captured)), 2)
        self.assertEqual(self._stored(), 2)
        self.assertEqual(self._stored(field="console_port_template_count"), 1)

    # --- The safety property: the flush is inside the transaction ---

    def test_flush_is_not_deferred_to_on_commit(self):
        """
        The deltas must land pre-commit, not via `transaction.on_commit`.

        Deferring to on_commit would put the counter write outside the
        transaction that justified it, so a crash between COMMIT and the
        callback would silently lose the increment. `captureOnCommitCallbacks`
        holds every registered callback back; the counter must already be
        correct without any of them running.
        """
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with _enabled(), buffered_counter_updates():
                self._add_interface_templates(2)
            self.assertEqual(self._stored(), 2)

        self.assertEqual(callbacks, [])

    def test_rollback_discards_deltas(self):
        """A failed apply rolls the flush back with the writes that produced it."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                with _enabled(), buffered_counter_updates():
                    self._add_interface_templates(2)
                    raise RuntimeError("forced rollback")

        self.assertEqual(self._stored(), 0)
        self.assertEqual(InterfaceTemplate.objects.filter(device_type=self.dt).count(), 0)

    def test_flush_failure_rolls_back_the_apply(self):
        """If the flush itself raises, the exception reaches the enclosing atomic block."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                with mock.patch.object(
                    counter_buffer,
                    "_flush_counter_deltas",
                    side_effect=RuntimeError("deadlock"),
                ):
                    with _enabled(), buffered_counter_updates():
                        self._add_interface_templates(1)

        self.assertEqual(self._stored(), 0)
        self.assertEqual(InterfaceTemplate.objects.filter(device_type=self.dt).count(), 0)

    # --- Interaction with the bypass ---

    def test_bypass_takes_precedence_when_both_active(self):
        """With apply_bypass_counter_updates active nothing is written or buffered."""
        token = counter_buffer._bypass_active.set(True)
        try:
            with CaptureQueriesContext(connection) as captured:
                with _enabled(), buffered_counter_updates():
                    self._add_interface_templates(2)
        finally:
            counter_buffer._bypass_active.reset(token)

        self.assertEqual(self._counter_updates(captured), [])
        self.assertEqual(self._stored(), 0)
        # The children themselves were still created; only the counter is skipped.
        self.assertEqual(InterfaceTemplate.objects.filter(device_type=self.dt).count(), 2)

    # --- Nothing outside an apply is affected ---

    def test_writes_outside_the_context_are_unbuffered(self):
        """With no buffer active the wrapper delegates, so ordinary NetBox writes are unchanged."""
        with _enabled():
            self._add_interface_templates(2)
        self.assertEqual(self._stored(), 2)

    def test_buffer_does_not_leak_past_the_context(self):
        """The contextvar is reset on exit, so a later write is synchronous again."""
        with _enabled(), buffered_counter_updates():
            self._add_interface_templates(1)

        self.assertIsNone(counter_buffer._apply_counter_buffer.get())
        with _enabled():
            self._add_interface_templates(1)
        self.assertEqual(self._stored(), 2)


class FlushCounterDeltasTestCase(TestCase):
    """`_flush_counter_deltas` statement shape: per-row deltas, sorted, chunked."""

    def setUp(self):
        """Three DeviceTypes to carry independent per-row deltas."""
        mfr = Manufacturer.objects.create(name="fcd-mfr", slug="fcd-mfr")
        self.dts = [
            DeviceType.objects.create(manufacturer=mfr, model=f"fcd-dt{i}", slug=f"fcd-dt{i}")
            for i in range(3)
        ]

    def _stored(self, dt):
        """Read the interface_template_count column straight from the DB."""
        return DeviceType.objects.filter(pk=dt.pk).values_list(
            "interface_template_count", flat=True
        )[0]

    def test_per_row_deltas_are_applied_independently(self):
        """One statement carries a different delta for each row via its CASE expression."""
        a, b, c = self.dts
        DeviceType.objects.filter(pk=c.pk).update(interface_template_count=10)

        buffer = {
            (DeviceType, "interface_template_count"): {a.pk: 1, b.pk: 4, c.pk: -3},
        }
        with CaptureQueriesContext(connection) as captured:
            _flush_counter_deltas(buffer)

        updates = [q for q in captured.captured_queries if "UPDATE" in q["sql"].upper()]
        self.assertEqual(len(updates), 1)
        self.assertEqual(self._stored(a), 1)
        self.assertEqual(self._stored(b), 4)
        self.assertEqual(self._stored(c), 7)

    def test_deltas_are_relative_not_absolute(self):
        """The CASE uses `F(counter) + delta`, so a concurrent baseline is preserved."""
        a = self.dts[0]
        DeviceType.objects.filter(pk=a.pk).update(interface_template_count=100)

        _flush_counter_deltas({(DeviceType, "interface_template_count"): {a.pk: 5}})
        self.assertEqual(self._stored(a), 105)

    @staticmethod
    def _when_spy():
        """Patch `When` with a pass-through spy so the emitted pk order can be read back."""
        return mock.patch.object(counter_buffer, "When", side_effect=When)

    def test_primary_keys_are_sorted_within_a_statement(self):
        """
        Consistent lock ordering is the reason batching is safe.

        Two workers flushing the same parent rows must approach them in the
        same order or they deadlock each other, so the pks are emitted
        ascending regardless of the order they were buffered in.
        """
        a, b, c = self.dts
        shuffled = {c.pk: 1, a.pk: 1, b.pk: 1}

        with self._when_spy() as spy:
            _flush_counter_deltas({(DeviceType, "interface_template_count"): shuffled})

        emitted = [call.kwargs["pk"] for call in spy.call_args_list]
        self.assertEqual(emitted, sorted([a.pk, b.pk, c.pk]))

    def test_counter_groups_are_emitted_in_a_stable_order(self):
        """Groups are ordered by (model label, counter name) so workers agree across counters."""
        a = self.dts[0]
        buffer = {
            (DeviceType, "interface_template_count"): {a.pk: 1},
            (DeviceType, "console_port_template_count"): {a.pk: 1},
        }
        with CaptureQueriesContext(connection) as captured:
            _flush_counter_deltas(buffer)

        updates = [
            q["sql"] for q in captured.captured_queries if "UPDATE" in q["sql"].upper()
        ]
        self.assertEqual(len(updates), 2)
        # Alphabetical by counter name: console_port before interface_template.
        self.assertIn("console_port_template_count", updates[0])
        self.assertIn("interface_template_count", updates[1])

    def test_large_batches_are_chunked_in_sorted_order(self):
        """
        Chunking bounds the CASE expression without disturbing the global pk order.

        Splitting into several statements is only safe because the chunks are
        contiguous ascending slices: workers still walk the rows in one
        direction, which is the property that keeps the batched flush from
        deadlocking against itself.
        """
        pks = list(range(1, 2501))
        buffer = {(DeviceType, "interface_template_count"): dict.fromkeys(reversed(pks), 1)}

        with self._when_spy() as spy, CaptureQueriesContext(connection) as captured:
            _flush_counter_deltas(buffer)

        updates = [q["sql"] for q in captured.captured_queries if "UPDATE" in q["sql"].upper()]
        self.assertEqual(len(updates), 3)
        self.assertEqual([call.kwargs["pk"] for call in spy.call_args_list], pks)

    def test_chunk_size_bounds_each_statement(self):
        """The chunk boundary is what splits the statements, not anything incidental."""
        a, b, c = self.dts
        buffer = {(DeviceType, "interface_template_count"): {a.pk: 1, b.pk: 1, c.pk: 1}}

        with mock.patch.object(counter_buffer, "_CHUNK_SIZE", 2), \
             CaptureQueriesContext(connection) as captured:
            _flush_counter_deltas(buffer)

        updates = [q for q in captured.captured_queries if "UPDATE" in q["sql"].upper()]
        self.assertEqual(len(updates), 2)
        self.assertEqual(self._stored(a), 1)
        self.assertEqual(self._stored(b), 1)
        self.assertEqual(self._stored(c), 1)

    def test_zero_delta_rows_are_skipped(self):
        """Rows whose deltas cancelled out are dropped before the statement is built."""
        a, b = self.dts[0], self.dts[1]
        with CaptureQueriesContext(connection) as captured:
            _flush_counter_deltas(
                {(DeviceType, "interface_template_count"): {a.pk: 0, b.pk: 0}}
            )
        self.assertEqual(
            [q for q in captured.captured_queries if "UPDATE" in q["sql"].upper()], []
        )

    def test_missing_row_is_a_no_op(self):
        """A parent deleted earlier in the transaction simply matches nothing."""
        a = self.dts[0]
        missing_pk = max(dt.pk for dt in self.dts) + 10_000
        _flush_counter_deltas(
            {(DeviceType, "interface_template_count"): {a.pk: 2, missing_pk: 5}}
        )
        self.assertEqual(self._stored(a), 2)


class BufferedCounterUpdatesApplyTestCase(APITestCase):
    """End-to-end: the buffer is wired into the `/bulk-plan-apply/` apply path."""

    def setUp(self):
        """
        Auth plus a Device to hang interfaces off.

        `Device.interface_count` is the counter the buffer exists for -- the
        hot parent row in the contention this feature addresses -- and it is
        present across every NetBox version the plugin supports, unlike the
        DeviceType counters, which vary.
        """
        super().setUp()
        self.url = "/netbox/api/plugins/diode/bulk-plan-apply/"
        self.auth = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.site = Site.objects.create(name="bcu-site", slug="bcu-site")
        mfr = Manufacturer.objects.create(name="bcu-api-mfr", slug="bcu-api-mfr")
        self.dt = DeviceType.objects.create(
            manufacturer=mfr, model="bcu-api-dt", slug="bcu-api-dt"
        )
        self.role = DeviceRole.objects.create(name="bcu-role", slug="bcu-role")
        self.device = Device.objects.create(
            name="bcu-device", site=self.site, device_type=self.dt, role=self.role
        )

    def _payload(self, names):
        """Build a bulk-plan-apply payload creating one Interface per name."""
        return {
            "entities": [
                {
                    "id": f"entity-{name}",
                    "object_type": "dcim.interface",
                    "entity": {
                        "interface": {
                            "name": name,
                            "type": "1000base-t",
                            "device": {
                                "name": "bcu-device",
                                "site": {"name": "bcu-site"},
                                "role": {"name": "bcu-role"},
                                "device_type": {
                                    "manufacturer": {"name": "bcu-api-mfr"},
                                    "model": "bcu-api-dt",
                                },
                            },
                        }
                    },
                }
                for name in names
            ]
        }

    def _interface_count(self):
        """Read Device.interface_count straight from the DB."""
        return Device.objects.filter(pk=self.device.pk).values_list(
            "interface_count", flat=True
        )[0]

    def test_apply_keeps_the_counter_exact_with_the_buffer_on(self):
        """Three interfaces applied with the buffer on leave interface_count at 3."""
        suffix = uuid.uuid4().hex[:6]
        names = [f"eth-{suffix}-{i}" for i in range(3)]

        with _enabled():
            response = self.client.post(
                self.url, data=self._payload(names), format="json", **self.auth
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(Interface.objects.filter(name__in=names).count(), 3)
        self.assertEqual(self._interface_count(), 3)

    def test_buffered_and_unbuffered_applies_agree(self):
        """The buffer is a performance change only: the counter lands where it would have."""
        suffix = uuid.uuid4().hex[:6]

        unbuffered = [f"eth-off-{suffix}-{i}" for i in range(2)]
        response = self.client.post(
            self.url, data=self._payload(unbuffered), format="json", **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        baseline = self._interface_count()

        buffered = [f"eth-on-{suffix}-{i}" for i in range(2)]
        with _enabled():
            response = self.client.post(
                self.url, data=self._payload(buffered), format="json", **self.auth
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

        self.assertEqual(baseline, 2)
        self.assertEqual(self._interface_count(), 4)
        self.assertEqual(
            self._interface_count(), Interface.objects.filter(device=self.device).count()
        )
