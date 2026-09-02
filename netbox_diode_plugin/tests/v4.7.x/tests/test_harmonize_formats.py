#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - harmonize_formats Tests."""

import datetime
import decimal

from django.db.backends.postgresql.psycopg_any import DateRange, DateTimeTZRange, NumericRange
from django.test import TestCase

from netbox_diode_plugin.api.common import harmonize_formats


class HarmonizeFormatsNumericRangeTestCase(TestCase):
    """Test NumericRange normalization to inclusive [lower, upper] pairs."""

    def test_half_open_range_from_postgres(self):
        """A range read back from Postgres is canonicalized half-open."""
        self.assertEqual(harmonize_formats(NumericRange(1, 4095, bounds="[)")), [1, 4094])

    def test_inclusive_range_from_python(self):
        """
        An inclusive range keeps its upper bound.

        This is the shape of NetBox's own model default for
        ``ipam.VLANGroup.vid_ranges`` (``default_vid_ranges`` returns
        ``NumericRange(1, 4094, bounds="[]")``). The transformer applies that
        default whenever the caller omits the field, so treating it as half-open
        made every VLAN group created through Diode permit only 1-4093 and
        reject VID 4094 for the lifetime of the group.
        """
        self.assertEqual(harmonize_formats(NumericRange(1, 4094, bounds="[]")), [1, 4094])

    def test_operator_narrowed_range_is_preserved_either_way(self):
        """A deliberately narrowed range round-trips from both bound styles."""
        self.assertEqual(harmonize_formats(NumericRange(1, 1001, bounds="[)")), [1, 1000])
        self.assertEqual(harmonize_formats(NumericRange(1, 1000, bounds="[]")), [1, 1000])

    def test_exclusive_lower_bound(self):
        """An exclusive lower bound is advanced to the first included value."""
        self.assertEqual(harmonize_formats(NumericRange(0, 4095, bounds="()")), [1, 4094])

    def test_unbounded_ends_do_not_raise(self):
        """An unbounded end stays None rather than raising on None arithmetic."""
        self.assertEqual(harmonize_formats(NumericRange(None, 4095, bounds="[)")), [None, 4094])
        self.assertEqual(harmonize_formats(NumericRange(1, None, bounds="[)")), [1, None])

    def test_non_integer_ranges_do_not_raise(self):
        """
        Date, datetime and decimal ranges must not blow up on integer arithmetic.

        ``NumericRange`` is an alias of psycopg's generic ``Range``, so every
        range type matches the same branch. Shifting a bound by one is only
        meaningful for discrete integers, so other bound types are passed
        through. ``vid_ranges`` is NetBox's only range field today, so none of
        these are reachable, but the branch should stay total.
        """
        self.assertEqual(
            harmonize_formats(DateRange(datetime.date(2020, 1, 1), datetime.date(2020, 2, 1))),
            [datetime.date(2020, 1, 1), datetime.date(2020, 2, 1)],
        )
        self.assertEqual(
            harmonize_formats(
                DateTimeTZRange(datetime.datetime(2020, 1, 1), datetime.datetime(2020, 2, 1)),
            ),
            [datetime.datetime(2020, 1, 1), datetime.datetime(2020, 2, 1)],
        )
        self.assertEqual(
            harmonize_formats(NumericRange(decimal.Decimal("1.5"), decimal.Decimal("9.5"))),
            [decimal.Decimal("1.5"), decimal.Decimal("9.5")],
        )

    def test_nested_in_list_and_dict(self):
        """Ranges are normalized through the containers the transformer emits."""
        self.assertEqual(
            harmonize_formats({"vid_ranges": [NumericRange(1, 4094, bounds="[]")]}),
            {"vid_ranges": [[1, 4094]]},
        )
