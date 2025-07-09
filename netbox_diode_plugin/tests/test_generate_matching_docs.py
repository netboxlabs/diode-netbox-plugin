#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests for generate_matching_docs command."""

import io
import sys
from unittest import mock
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Q
from django.test import TestCase

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
        from django.core.management.base import BaseCommand
        from django.core.management import get_commands
        
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

    def test_get_matcher_description_standard_fields(self):
        """Test getting matcher description for standard field-based matcher."""
        mock_matcher = mock.MagicMock()
        mock_matcher.fields = ["name", "site"]
        mock_matcher.condition = None
        
        result = self.command.get_matcher_description(mock_matcher)
        self.assertEqual(result, "Matches on fields: name, site")

    def test_get_matcher_description_with_condition(self):
        """Test getting matcher description for matcher with condition."""
        mock_matcher = mock.MagicMock()
        mock_matcher.fields = ["name", "site"]
        mock_matcher.condition = Q(site__isnull=False)
        
        with mock.patch.object(self.command, 'extract_condition_description') as mock_extract:
            mock_extract.return_value = "site is NOT NULL"
            result = self.command.get_matcher_description(mock_matcher)
        
        self.assertEqual(result, "Matches on fields: name, site where site is NOT NULL")

    def test_get_matcher_description_custom(self):
        """Test getting matcher description for custom matcher."""
        mock_matcher = mock.MagicMock()
        # No special attributes
        del mock_matcher.fields
        
        result = self.command.get_matcher_description(mock_matcher)
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
        
        with mock.patch.object(self.command, 'extract_condition_description') as mock_extract, \
             mock.patch.object(self.command, 'get_matcher_description') as mock_desc, \
             mock.patch.object(self.command, 'get_version_constraints') as mock_version:
            
            mock_extract.return_value = "site is NOT NULL"
            mock_desc.return_value = "Test description"
            mock_version.return_value = "≥4.3.0"
            
            result = self.command.analyze_logical_matchers()
        
        self.assertIn("dcim.site", result)
        self.assertEqual(len(result["dcim.site"]), 2)
        
        # Check first matcher
        matcher1_info = result["dcim.site"][0]
        self.assertEqual(matcher1_info.name, "test_matcher_1")
        self.assertEqual(matcher1_info.fields, ["name"])
        self.assertEqual(matcher1_info.condition, "None")
        self.assertEqual(matcher1_info.version_constraints, "≥4.3.0")
        
        # Check second matcher
        matcher2_info = result["dcim.site"][1]
        self.assertEqual(matcher2_info.name, "test_matcher_2")
        self.assertEqual(matcher2_info.fields, ["name", "site"])
        self.assertEqual(matcher2_info.condition, "site is NOT NULL")
        self.assertEqual(matcher2_info.version_constraints, "≥4.3.0")

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
            version_constraints="All versions"
        )
        
        matcher_info2 = MatcherInfo(
            name="test_matcher_2",
            fields=["name", "site"],
            condition="site is NOT NULL",
            description="Test description 2",
            version_constraints="≥4.3.0"
        )
        
        docs = {
            "dcim.site": [matcher_info1, matcher_info2]
        }
        
        result = self.command.generate_markdown_table(docs)
        
        # Check header
        self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", result)
        self.assertIn("## dcim.site", result)
        
        # Check table header
        self.assertIn("| Matcher Name | Fields | Condition | Description | Version Constraints |", result)
        self.assertIn("|--------------|--------|-----------|-------------|-------------------|", result)
        
        # Check table rows
        self.assertIn("| test_matcher_1 | name | N/A | Test description 1 | All versions |", result)
        self.assertIn("| test_matcher_2 | name, site | site is NOT NULL | Test description 2 | ≥4.3.0 |", result)

    def test_generate_markdown_table_with_pipe_escaping(self):
        """Test generating markdown table with pipe character escaping."""
        matcher_info = MatcherInfo(
            name="test|matcher",
            fields=["field|1", "field|2"],
            condition="field|1 is NOT NULL",
            description="Test|description",
            version_constraints="≥4.3.0|test"
        )
        
        docs = {
            "dcim.site": [matcher_info]
        }
        
        result = self.command.generate_markdown_table(docs)
        
        # Check that pipe characters are escaped
        self.assertIn("| test\\|matcher | field\\|1, field\\|2 | field\\|1 is NOT NULL | Test\\|description | ≥4.3.0\\|test |", result)

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

    @mock.patch('netbox_diode_plugin.management.commands.generate_matching_docs._LOGICAL_MATCHERS')
    def test_handle_success(self, mock_logical_matchers):
        """Test successful command execution."""
        # Mock the matcher factory to return empty list
        mock_logical_matchers.items.return_value = []
        
        # Capture stdout
        with patch('sys.stdout', new=io.StringIO()) as mock_stdout:
            self.command.handle()
            
            output = mock_stdout.getvalue()
            self.assertIn("Analyzing matching criteria...", output)
            self.assertIn("Generating markdown documentation...", output)
            self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", output)

    @mock.patch('netbox_diode_plugin.management.commands.generate_matching_docs._LOGICAL_MATCHERS')
    def test_handle_with_output_file(self, mock_logical_matchers):
        """Test command execution with output file."""
        import tempfile
        import os
        
        # Mock the matcher factory to return empty list
        mock_logical_matchers.items.return_value = []
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            temp_file_path = temp_file.name
        
        try:
            # Test with --output argument
            with patch('sys.stdout', new=io.StringIO()):
                self.command.handle(output=temp_file_path)
            
            # Check that file was created and contains expected content
            with open(temp_file_path, 'r') as f:
                content = f.read()
                self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", content)
        
        finally:
            # Clean up
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_call_command_success(self):
        """Test calling the command via call_command."""
        with patch('sys.stdout', new=io.StringIO()) as mock_stdout:
            call_command('generate_matching_docs')
            
            output = mock_stdout.getvalue()
            self.assertIn("Analyzing matching criteria...", output)
            self.assertIn("Generating markdown documentation...", output)
            self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", output)

    def test_call_command_with_output(self):
        """Test calling the command with output file."""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            temp_file_path = temp_file.name
        
        try:
            call_command('generate_matching_docs', output=temp_file_path)
            
            # Check that file was created and contains expected content
            with open(temp_file_path, 'r') as f:
                content = f.read()
                self.assertIn("# NetBox Diode Plugin - Object Matching Criteria", content)
        
        finally:
            # Clean up
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_condition_extraction_edge_cases(self):
        """Test edge cases in condition extraction."""
        # Test with non-Q condition
        condition = "simple_string_condition"
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "simple_string_condition")
        
        # Test with Q condition that has no children
        condition = Q()
        result = self.command.extract_condition_description(condition)
        self.assertEqual(result, "Q()")
        
        # Test with complex nested condition
        condition = Q(field1__isnull=True) & (Q(field2="value1") | Q(field2="value2"))
        result = self.command.extract_condition_description(condition)
        # The exact format may vary, but it should contain the field names
        self.assertIn("field1", result)
        self.assertIn("field2", result)

    def test_matcher_description_edge_cases(self):
        """Test edge cases in matcher description generation."""
        # Test with matcher that has fields but no condition
        mock_matcher = mock.MagicMock()
        mock_matcher.fields = []
        mock_matcher.condition = None
        
        result = self.command.get_matcher_description(mock_matcher)
        self.assertEqual(result, "Matches on fields: ")
        
        # Test with matcher that has condition but no fields
        mock_matcher = mock.MagicMock()
        mock_matcher.fields = None
        mock_matcher.condition = Q(field1__isnull=True)
        
        with mock.patch.object(self.command, 'extract_condition_description') as mock_extract:
            mock_extract.return_value = "field1 is NULL"
            result = self.command.get_matcher_description(mock_matcher)
        
        self.assertEqual(result, "Custom matcher")

    def test_version_constraints_edge_cases(self):
        """Test edge cases in version constraints."""
        # Test with empty string versions
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = ""
        mock_matcher.max_version = ""
        
        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≥ ≤")
        
        # Test with whitespace versions
        mock_matcher = mock.MagicMock()
        mock_matcher.min_version = "  4.3.0  "
        mock_matcher.max_version = "  4.3.99  "
        
        result = self.command.get_version_constraints(mock_matcher)
        self.assertEqual(result, "≥  4.3.0   ≤  4.3.99  ")

    def test_markdown_table_edge_cases(self):
        """Test edge cases in markdown table generation."""
        # Test with None values
        matcher_info = MatcherInfo(
            name="test_matcher",
            fields=None,
            condition=None,
            description=None,
            version_constraints=None
        )
        
        docs = {
            "dcim.site": [matcher_info]
        }
        
        result = self.command.generate_markdown_table(docs)
        
        # Check that None values are handled gracefully
        self.assertIn("| test_matcher | N/A | N/A | Custom matcher | All versions |", result)
        
        # Test with empty fields list
        matcher_info = MatcherInfo(
            name="test_matcher",
            fields=[],
            condition="N/A",
            description="Test description",
            version_constraints="All versions"
        )
        
        docs = {
            "dcim.site": [matcher_info]
        }
        
        result = self.command.generate_markdown_table(docs)
        
        # Check that empty fields list is handled
        self.assertIn("| test_matcher |  | N/A | Test description | All versions |", result) 