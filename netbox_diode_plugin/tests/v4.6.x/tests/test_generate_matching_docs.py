#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests for generate_matching_docs command."""

import io
import sys
from unittest import mock
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Q
from django.test import TestCase

from netbox_diode_plugin.api.matcher import (
    AutoSlugMatcher,
    CableTerminationSetMatcher,
    GlobalIPNetworkIPMatcher,
    ObjectMatchCriteria,
    RackReservationUnitOverlapMatcher,
    RackSiteNameMatcher,
    VirtualChassisNameMatcher,
)
from netbox_diode_plugin.management.commands.generate_matching_docs import (
    Command,
    MatcherInfo,
)


class MatcherInfoTestCase(TestCase):
    """Test case for MatcherInfo dataclass."""

    def test_matcher_info_creation(self):
        """Test creating MatcherInfo with all fields."""
        info = MatcherInfo(
            name="test_matcher",
            fields=["field1", "field2"],
            condition="field1 is NOT NULL",
            description="Test matcher description",
            matcher_type="ObjectMatchCriteria",
            version_constraints="≥4.3.0"
        )

        self.assertEqual(info.name, "test_matcher")
        self.assertEqual(info.fields, ["field1", "field2"])
        self.assertEqual(info.condition, "field1 is NOT NULL")
        self.assertEqual(info.description, "Test matcher description")
        self.assertEqual(info.matcher_type, "ObjectMatchCriteria")
        self.assertEqual(info.version_constraints, "≥4.3.0")

    def test_matcher_info_defaults(self):
        """Test creating MatcherInfo with default values."""
        info = MatcherInfo(name="test_matcher")

        self.assertEqual(info.name, "test_matcher")
        self.assertIsNone(info.fields)
        self.assertIsNone(info.condition)
        self.assertIsNone(info.description)
        self.assertEqual(info.matcher_type, "ObjectMatchCriteria")
        self.assertIsNone(info.version_constraints)


class GenerateMatchingDocsCommandTestCase(TestCase):
    """Test case for the generate_matching_docs command."""

    def setUp(self):
        """Set up the test case."""
        self.command = Command()
        self.command.stdout = io.StringIO()
        self.command.stderr = io.StringIO()

    def test_add_arguments(self):
        """Test that the command accepts the --output argument."""
        from django.core.management import get_commands
        from django.core.management.base import BaseCommand

        # Verify the command is registered
        commands = get_commands()
        self.assertIn('generate_matching_docs', commands)

    def test_extract_condition_description_none(self):
        """Test extracting condition description from None."""
        result = self.command.extract_condition_description(None)
        self.assertEqual(result, "None")

    def test_extract_condition_description_simple(self):
        """Test extracting condition description from a simple condition."""
        condition = Q(field1__isnull=True)
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "field1 is NULL")

    def test_extract_condition_description_complex(self):
        """Test extracting condition description from a complex condition."""
        condition = Q(field1__isnull=True) & Q(field2="value")
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "field1 is NULL AND field2 = value")

    def test_extract_condition_description_or(self):
        """Test extracting condition description with OR connector."""
        condition = Q(field1="value1") | Q(field2="value2")
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "field1 = value1 OR field2 = value2")

    def test_extract_condition_description_not_null(self):
        """Test extracting condition description for NOT NULL."""
        condition = Q(field1__isnull=False)
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "field1 is NOT NULL")

    def test_get_matcher_description_ip_address_global(self):
        """Test getting matcher description for IP address global matcher."""
        mock_matcher = mock.MagicMock()
        mock_matcher.name = "logical_ip_address_global_no_vrf"
        mock_matcher.ip_fields = ["address"]
        mock_matcher.vrf_field = "vrf"

        result = self.command.get_matcher_description(mock_matcher)
        self.assertEqual(result, "Matches IP address address in global namespace (no VRF)")

    def test_get_matcher_description_ip_address_vrf(self):
        """Test getting matcher description for IP address VRF matcher."""
        mock_matcher = mock.MagicMock()
        mock_matcher.name = "logical_ip_address_within_vrf"
        mock_matcher.ip_fields = ["address"]
        mock_matcher.vrf_field = "vrf"

        result = self.command.get_matcher_description(mock_matcher)
        self.assertEqual(result, "Matches IP address address within VRF")

    def test_get_matcher_description_ip_range(self):
        """Test getting matcher description for IP range matcher."""
        mock_matcher = mock.MagicMock()
        mock_matcher.name = "logical_ip_range_start_end_within_vrf"
        mock_matcher.ip_fields = ["start_address", "end_address"]
        mock_matcher.vrf_field = "vrf"

        result = self.command.get_matcher_description(mock_matcher)
        self.assertEqual(result, "Matches IP range start_address, end_address within VRF context")

    def test_get_matcher_description_ip_range_global_no_vrf(self):
        """Test getting matcher description for IP range global-no-VRF matcher."""
        mock_matcher = mock.MagicMock()
        mock_matcher.name = "logical_ip_range_start_end_global_no_vrf"
        mock_matcher.ip_fields = ["start_address", "end_address"]
        mock_matcher.vrf_field = "vrf"

        result = self.command.get_matcher_description(mock_matcher)
        self.assertIn("no VRF", result)
        self.assertEqual(
            result, "Matches IP range start_address, end_address in global namespace (no VRF)"
        )

    def test_get_matcher_fields_cable_termination_set(self):
        """Test deriving Fields for a real CableTerminationSetMatcher via a/b field names."""
        matcher = CableTerminationSetMatcher(model_class=None, name="x")

        result = self.command.get_matcher_fields(matcher)
        self.assertEqual(result, ["a_terminations", "b_terminations"])

    def test_get_matcher_fields_virtual_chassis_name(self):
        """Test deriving Fields for a real VirtualChassisNameMatcher via the class-name map."""
        matcher = VirtualChassisNameMatcher(model_class=None, name="x")

        result = self.command.get_matcher_fields(matcher)
        self.assertEqual(result, ["name"])

    def test_get_matcher_fields_rackreservation_unit_overlap(self):
        """Test deriving Fields for a real RackReservationUnitOverlapMatcher via the class-name map."""
        matcher = RackReservationUnitOverlapMatcher(model_class=None, name="x")

        result = self.command.get_matcher_fields(matcher)
        self.assertEqual(result, ["rack", "units"])

    def test_get_matcher_fields_rack_site_name(self):
        """Test deriving Fields for a real RackSiteNameMatcher via the class-name map."""
        matcher = RackSiteNameMatcher(model_class=None, name="x")

        result = self.command.get_matcher_fields(matcher)
        self.assertEqual(result, ["site", "name"])

    def test_get_matcher_fields_global_ip_network(self):
        """Test deriving Fields for a real GlobalIPNetworkIPMatcher via ip_fields."""
        matcher = GlobalIPNetworkIPMatcher(
            ip_fields=["address"], vrf_field="vrf", model_class=None, name="x"
        )

        result = self.command.get_matcher_fields(matcher)
        self.assertEqual(result, ["address"])

    def test_get_matcher_description_cable_termination_set_docstring(self):
        """Test that CableTerminationSetMatcher's description derives from its docstring."""
        matcher = CableTerminationSetMatcher(model_class=None, name="x")

        result = self.command.get_matcher_description(matcher)
        expected = CableTerminationSetMatcher.__doc__.strip().splitlines()[0].strip()
        self.assertEqual(result, expected)

    def test_get_matcher_description_virtual_chassis_name_docstring(self):
        """Test that VirtualChassisNameMatcher's description derives from its docstring."""
        matcher = VirtualChassisNameMatcher(model_class=None, name="x")

        result = self.command.get_matcher_description(matcher)
        expected = VirtualChassisNameMatcher.__doc__.strip().splitlines()[0].strip()
        self.assertEqual(result, expected)

    def test_get_matcher_description_rackreservation_unit_overlap_docstring(self):
        """Test that RackReservationUnitOverlapMatcher's description derives from its docstring."""
        matcher = RackReservationUnitOverlapMatcher(model_class=None, name="x")

        result = self.command.get_matcher_description(matcher)
        expected = RackReservationUnitOverlapMatcher.__doc__.strip().splitlines()[0].strip()
        self.assertEqual(result, expected)

    def test_get_matcher_description_standard_fields(self):
        """Test getting matcher description for standard field-based matcher."""
        matcher = ObjectMatchCriteria(
            fields=["name", "site"],
            name="test_matcher",
            model_class=None,
            condition=None
        )

        result = self.command.get_matcher_description(matcher)
        self.assertEqual(result, "Matches on unique constraint fields: name, site")

    def test_get_matcher_description_with_condition(self):
        """Test getting matcher description for matcher with condition."""
        matcher = ObjectMatchCriteria(
            fields=["name", "site"],
            name="test_matcher",
            model_class=None,
            condition=Q(site__isnull=False)
        )

        result = self.command.get_matcher_description(matcher)
        self.assertEqual(result, "Matches on unique constraint fields: name, site where site is NOT NULL")

    def test_get_matcher_description_custom(self):
        """Test getting matcher description for custom matcher."""
        matcher = ObjectMatchCriteria(
            fields=None,
            name="test_matcher",
            model_class=None,
            condition=None
        )

        result = self.command.get_matcher_description(matcher)
        self.assertEqual(result, "Custom matcher")

    def test_get_version_constraints_none(self):
        """Test getting version constraints when none are set."""
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = None
        mock_matcher.max_version = None

        result = self.command.get_version_constraints(mock_matcher)
        self.assertIsNone(result)

    def test_get_version_constraints_min_only(self):
        """Test getting version constraints with only min_version."""
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = "4.3.0"
        mock_matcher.max_version = None

        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≥4.3.0")

    def test_get_version_constraints_max_only(self):
        """Test getting version constraints with only max_version."""
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = None
        mock_matcher.max_version = "4.2.99"

        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≤4.2.99")

    def test_get_version_constraints_both(self):
        """Test getting version constraints with both min and max."""
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = "4.3.0"
        mock_matcher.max_version = "4.3.99"

        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≥4.3.0 ≤4.3.99")

    @mock.patch('netbox_diode_plugin.management.commands.generate_matching_docs._LOGICAL_MATCHERS')
    def test_analyze_logical_matchers(self, mock_logical_matchers):
        """Test analyzing logical matchers."""
        # Create mock matchers
        mock_matcher1 = mock.MagicMock()
        mock_matcher1.name = "test_matcher_1"
        mock_matcher1.fields = ["name"]
        mock_matcher1.condition = None
        mock_matcher1.min_version = "4.3.0"
        mock_matcher1.max_version = None

        mock_matcher2 = mock.MagicMock()
        mock_matcher2.name = "test_matcher_2"
        mock_matcher2.fields = ["name", "site"]
        mock_matcher2.condition = Q(site__isnull=False)
        mock_matcher2.min_version = None
        mock_matcher2.max_version = "4.2.99"

        # Mock the matcher factory
        mock_logical_matchers.items.return_value = [
            ("dcim.site", lambda: [mock_matcher1, mock_matcher2])
        ]

        result = self.command.analyze_logical_matchers()

        self.assertIn("dcim.site", result)
        self.assertEqual(len(result["dcim.site"]), 2)

        # Check first matcher
        matcher1_info = result["dcim.site"][0]
        self.assertEqual(matcher1_info.name, "test_matcher_1")
        self.assertEqual(matcher1_info.fields, ["name"])
        self.assertEqual(matcher1_info.condition, "None")
        self.assertEqual(matcher1_info.version_constraints, "≥4.3.0")
        self.assertEqual(matcher1_info.matcher_source, "logical")

        # Check second matcher
        matcher2_info = result["dcim.site"][1]
        self.assertEqual(matcher2_info.name, "test_matcher_2")
        self.assertEqual(matcher2_info.fields, ["name", "site"])
        self.assertEqual(matcher2_info.condition, "site is NOT NULL")
        self.assertEqual(matcher2_info.version_constraints, "≤4.2.99")
        self.assertEqual(matcher2_info.matcher_source, "logical")

    @mock.patch('netbox_diode_plugin.management.commands.generate_matching_docs.extract_supported_models')
    @mock.patch('netbox_diode_plugin.management.commands.generate_matching_docs.get_model_matchers')
    def test_analyze_builtin_matchers(self, mock_get_model_matchers, mock_supported_models):
        """Test analyzing builtin matchers."""
        # Create mock model class
        mock_model_class = mock.MagicMock()
        mock_model_class.__name__ = "TestModel"

        # Create mock builtin matchers
        mock_unique_matcher = mock.MagicMock()
        mock_unique_matcher.name = "unique_name"
        mock_unique_matcher.fields = ["name"]
        mock_unique_matcher.condition = None
        mock_unique_matcher.min_version = None
        mock_unique_matcher.max_version = None

        mock_constraint_matcher = mock.MagicMock()
        mock_constraint_matcher.name = "test_constraint"
        mock_constraint_matcher.fields = ["field1", "field2"]
        mock_constraint_matcher.condition = Q(field1__isnull=True)
        mock_constraint_matcher.min_version = None
        mock_constraint_matcher.max_version = None

        # Mock logical matcher (should be skipped)
        mock_logical_matcher = mock.MagicMock()
        mock_logical_matcher.name = "logical_test"
        mock_logical_matcher.fields = ["name"]
        mock_logical_matcher.condition = None
        mock_logical_matcher.min_version = None
        mock_logical_matcher.max_version = None

        # Mock the supported models and get_model_matchers
        mock_supported_models.return_value = {
            "dcim.site": {"model": mock_model_class}
        }
        mock_get_model_matchers.return_value = [
            mock_unique_matcher,
            mock_constraint_matcher,
            mock_logical_matcher  # This should be skipped
        ]

        result = self.command.analyze_builtin_matchers()

        self.assertIn("dcim.site", result)
        self.assertEqual(len(result["dcim.site"]), 2)  # Should only include builtin matchers

        # Check unique field matcher
        unique_matcher_info = result["dcim.site"][0]
        self.assertEqual(unique_matcher_info.name, "unique_name")
        self.assertEqual(unique_matcher_info.fields, ["name"])
        self.assertEqual(unique_matcher_info.matcher_source, "builtin")

        # Check constraint matcher
        constraint_matcher_info = result["dcim.site"][1]
        self.assertEqual(constraint_matcher_info.name, "test_constraint")
        self.assertEqual(constraint_matcher_info.fields, ["field1", "field2"])
        self.assertEqual(constraint_matcher_info.matcher_source, "builtin")

    def test_combine_matchers(self):
        """Test combining logical and builtin matchers."""
        # Create logical matchers
        logical_matcher = MatcherInfo(
            name="logical_test",
            fields=["name"],
            condition="N/A",
            description="Logical matcher",
            version_constraints="All versions",
            matcher_source="logical"
        )

        # Create builtin matchers
        builtin_matcher = MatcherInfo(
            name="builtin_test",
            fields=["name"],
            condition="N/A",
            description="Builtin matcher",
            version_constraints="All versions",
            matcher_source="builtin"
        )

        logical_docs = {
            "dcim.site": [logical_matcher],
            "dcim.device": [logical_matcher]
        }

        builtin_docs = {
            "dcim.site": [builtin_matcher],
            "ipam.prefix": [builtin_matcher]
        }

        result = self.command.combine_matchers(logical_docs, builtin_docs)

        # Check that all object types are included
        self.assertIn("dcim.site", result)
        self.assertIn("dcim.device", result)
        self.assertIn("ipam.prefix", result)

        # Check that dcim.site has both logical and builtin matchers
        site_matchers = result["dcim.site"]
        self.assertEqual(len(site_matchers), 2)

        # Check that logical matcher comes first (as it was added first)
        self.assertEqual(site_matchers[0].name, "logical_test")
        self.assertEqual(site_matchers[0].matcher_source, "logical")
        self.assertEqual(site_matchers[1].name, "builtin_test")
        self.assertEqual(site_matchers[1].matcher_source, "builtin")

        # Check that other object types have correct matchers
        self.assertEqual(len(result["dcim.device"]), 1)
        self.assertEqual(result["dcim.device"][0].matcher_source, "logical")

        self.assertEqual(len(result["ipam.prefix"]), 1)
        self.assertEqual(result["ipam.prefix"][0].matcher_source, "builtin")

    def test_get_matcher_description_builtin_types(self):
        """Test getting matcher description for different builtin matcher types."""
        # Test CustomFieldMatcher
        mock_custom_field_matcher = mock.MagicMock()
        mock_custom_field_matcher.custom_field = "test_field"
        mock_custom_field_matcher.fields = None
        mock_custom_field_matcher.ip_fields = None
        mock_custom_field_matcher.vrf_field = None

        result = self.command.get_matcher_description(mock_custom_field_matcher)
        self.assertEqual(result, "Matches on unique custom field: test_field")

    def test_get_matcher_description_autoslug(self):
        """Test getting matcher description for AutoSlugMatcher."""
        # Test AutoSlugMatcher
        autoslug_matcher = AutoSlugMatcher(
            name="test_autoslug",
            model_class=None,
            slug_field="slug"
        )

        result = self.command.get_matcher_description(autoslug_matcher)
        self.assertEqual(result, "Matches on auto-generated slug field: slug")

    def test_get_matcher_description_unique_field(self):
        """Test getting matcher description for unique field matcher."""
        # Test unique field matcher
        mock_unique_matcher = mock.MagicMock()
        mock_unique_matcher.name = "unique_name"
        mock_unique_matcher.fields = ["name"]
        mock_unique_matcher.ip_fields = None
        mock_unique_matcher.vrf_field = None
        mock_unique_matcher.custom_field = None
        mock_unique_matcher.slug_field = None

        result = self.command.get_matcher_description(mock_unique_matcher)
        self.assertEqual(result, "Matches on unique field(s): name")

    def test_get_matcher_description_unique_constraint(self):
        """Test getting matcher description for unique constraint matcher."""
        # Test unique constraint matcher
        mock_constraint_matcher = mock.MagicMock()
        mock_constraint_matcher.name = "test_constraint"
        mock_constraint_matcher.fields = ["field1", "field2"]
        mock_constraint_matcher.condition = None
        mock_constraint_matcher.custom_field = None
        mock_constraint_matcher.slug_field = None

        result = self.command.get_matcher_description(mock_constraint_matcher)
        self.assertEqual(result, "Matches on unique constraint fields: field1, field2")

    def test_generate_markdown_table_empty(self):
        """Test generating markdown table with empty documentation."""
        docs = {}
        result = self.command.generate_markdown_table(docs)

        expected_lines = [
            "# NetBox Diode Plugin - Object Matching Criteria",
            "",
            "This document describes how the Diode NetBox Plugin matches existing objects when applying changes.",
            ""
        ]

        for line in expected_lines:
            self.assertIn(line, result)

    def test_generate_markdown_table_with_matchers(self):
        """Test generating markdown table with matchers."""
        matcher_info1 = MatcherInfo(
            name="test_matcher_1",
            fields=["name"],
            condition="N/A",
            description="Test description 1",
            version_constraints="All versions",
            matcher_source="logical"
        )

        matcher_info2 = MatcherInfo(
            name="test_matcher_2",
            fields=["name", "site"],
            condition="site is NOT NULL",
            description="Test description 2",
            version_constraints="≥4.3.0",
            matcher_source="logical"
        )

        docs = {
            "dcim.site": [matcher_info1, matcher_info2]
        }

        result = self.command.generate_markdown_table(docs)

        # Check header
        self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", result)
        self.assertIn("## dcim.site", result)

        # Check table header
        self.assertIn("| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |", result)
        self.assertIn("|--------------|---------------------|------|--------|-----------|-------------|---------------------|", result)

        # Check table rows
        self.assertIn("| test_matcher_1 | 1 | logical | name | N/A | Test description 1 | All versions |", result)
        self.assertIn("| test_matcher_2 | 2 | logical | name, site | site is NOT NULL | Test description 2 | ≥4.3.0 |", result)

    def test_generate_markdown_table_with_pipe_escaping(self):
        """Test generating markdown table with pipe character escaping."""
        matcher_info = MatcherInfo(
            name="test|matcher",
            fields=["field|1", "field|2"],
            condition="field|1 is NOT NULL",
            description="Test|description",
            version_constraints="≥4.3.0|test",
            matcher_source="logical"
        )

        docs = {
            "dcim.site": [matcher_info]
        }

        result = self.command.generate_markdown_table(docs)

        # Check that pipe characters are escaped
        self.assertIn(
            "| test\\|matcher | 1 | logical | field\\|1, field\\|2 | field\\|1 is NOT NULL | Test\\|description | ≥4.3.0\\|test |",
            result,
        )

    def test_generate_markdown_table_no_matchers(self):
        """Test generating markdown table for object type with no matchers."""
        docs = {
            "dcim.site": []
        }

        result = self.command.generate_markdown_table(docs)

        self.assertIn("## dcim.site", result)
        self.assertIn("No specific matching criteria defined.", result)

    def test_generate_markdown_table_sorted_object_types(self):
        """Test that object types are sorted in the output."""
        matcher_info = MatcherInfo(
            name="test_matcher",
            fields=["name"],
            condition="N/A",
            description="Test description",
            version_constraints="All versions"
        )

        docs = {
            "dcim.device": [matcher_info],
            "dcim.site": [matcher_info],
            "ipam.prefix": [matcher_info]
        }

        result = self.command.generate_markdown_table(docs)

        # Check that sections appear in alphabetical order
        site_index = result.find("## dcim.site")
        device_index = result.find("## dcim.device")
        prefix_index = result.find("## ipam.prefix")

        self.assertLess(device_index, site_index)
        self.assertLess(site_index, prefix_index)

    def test_condition_extraction_with_none_Q_condition(self):
        """Test edge cases in condition extraction."""
        # Test with non-Q condition
        condition = "simple_string_condition"
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "simple_string_condition")

    def test_condition_extraction_with_Q_condition_with_no_children(self):
        """Test with Q condition that has no children."""
        condition = Q()
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "")

    def test_condition_extraction_with_Q_condition_with_children(self):
        """Test with complex nested condition."""
        condition = Q(field1__isnull=True) & (Q(field2="value1") | Q(field2="value2"))
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "field1 is NULL AND (OR: ('field2', 'value1'), ('field2', 'value2'))")

    def test_matcher_description_edge_cases(self):
        """Test edge cases in matcher description generation."""
        # Test with matcher that has fields but no condition
        matcher = ObjectMatchCriteria(
            fields=["name"],
            name="test_matcher",
            model_class=None,
            condition=None
        )

        result = self.command.get_matcher_description(matcher)
        self.assertEqual(result, "Matches on unique constraint fields: name")

        # Test with matcher that has condition but no fields
        matcher = ObjectMatchCriteria(
            fields=None,
            name="test_matcher",
            model_class=None,
            condition=Q(field1__isnull=True)
        )

        with mock.patch.object(self.command, 'extract_condition_description') as mock_extract:
            mock_extract.return_value = "field1 is NULL"
            result = self.command.get_matcher_description(matcher)

        self.assertEqual(result, "Custom matcher")

    def test_version_constraints_edge_cases(self):
        """Test edge cases in version constraints."""
        # Test with empty string versions
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = ""
        mock_matcher.max_version = ""

        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, None)

        # Test with whitespace versions
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = "  4.3.0  "
        mock_matcher.max_version = "  4.3.99  "

        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≥  4.3.0   ≤  4.3.99  ")

    def test_markdown_table_with_empty_values(self):
        """Test edge cases in markdown table generation."""
        # Test with None values
        matcher_info = MatcherInfo(
            name="test_matcher",
            fields=None,
            condition=None,
            description=None,
            version_constraints=None,
            matcher_source="logical"
        )

        docs = {
            "dcim.site": [matcher_info]
        }

        result = self.command.generate_markdown_table(docs)

        # Check that None values are handled gracefully
        self.assertIn("| test_matcher | 1 | logical |  | N/A | N/A | All versions |", result)

    def test_markdown_table_with_empty_fields(self):
        """Test with empty fields list."""
        matcher_info = MatcherInfo(
            name="test_matcher",
            fields=[],
            condition="N/A",
            description="Test description",
            version_constraints="All versions",
            matcher_source="logical"
        )

        docs = {
            "dcim.site": [matcher_info]
        }

        result = self.command.generate_markdown_table(docs)

        # Check that empty fields list is handled
        self.assertIn("| test_matcher | 1 | logical |  | N/A | Test description | All versions |", result)



class DocumentVersionScopeTestCase(TestCase):
    """The generated document is scoped to the NetBox release it was produced on."""

    def setUp(self):
        """Set up test."""
        self.command = Command()
        self.docs = {
            "dcim.site": [
                MatcherInfo(name="logical_site", matcher_source="logical",
                            fields=["name"], description="logical, no gate"),
                MatcherInfo(name="logical_gated", matcher_source="logical",
                            fields=["slug"], description="logical, gated", version_constraints="≥4.3.0"),
                MatcherInfo(name="dcim_site_unique_name", matcher_source="builtin",
                            fields=["name"], description="builtin"),
            ],
        }

    def test_header_and_builtin_rows_carry_the_release(self):
        """A builtin row without a plugin gate is stamped with the release, not "All versions"."""
        result = self.command.generate_markdown_table(self.docs, netbox_version="4.7.0")
        self.assertIn("Generated on NetBox 4.7.0.", result)
        self.assertIn("nulls_distinct=False", result)
        self.assertIn("| dcim_site_unique_name | 3 | builtin | name | N/A | builtin | NetBox 4.7.0 |", result)
        self.assertIn("| logical_site | 1 | logical | name | N/A | logical, no gate | All versions |", result)
        self.assertIn("| logical_gated | 2 | logical | slug | N/A | logical, gated | ≥4.3.0 |", result)

    def test_without_a_release_nothing_is_stamped(self):
        """Callers that pass no release keep the previous output."""
        result = self.command.generate_markdown_table(self.docs)
        self.assertNotIn("Generated on NetBox", result)
        self.assertIn("| dcim_site_unique_name | 3 | builtin | name | N/A | builtin | All versions |", result)

    def test_handle_stamps_the_running_release(self):
        """The command reads the release from settings and passes it through."""
        self.assertEqual(self.command.get_netbox_version(), str(settings.RELEASE.version))
        self.command.stdout = io.StringIO()
        with mock.patch.object(self.command, "get_netbox_version", return_value="9.9.9"):
            self.command.handle()
        self.assertIn("Generated on NetBox 9.9.9.", self.command.stdout.getvalue())
