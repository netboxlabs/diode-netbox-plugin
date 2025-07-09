#!/usr/bin/env python
"""Django management command to generate markdown documentation for NetBox Diode Plugin matching criteria."""

from django.core.management.base import BaseCommand
from typing import Dict, List, Optional
from dataclasses import dataclass

from netbox_diode_plugin.api.matcher import _LOGICAL_MATCHERS


@dataclass
class MatcherInfo:
    """Information about a matcher for documentation."""
    name: str
    fields: Optional[List[str]] = None
    condition: Optional[str] = None
    description: Optional[str] = None
    matcher_type: str = "ObjectMatchCriteria"
    version_constraints: Optional[str] = None


class Command(BaseCommand):
    help = "Generate markdown documentation for NetBox Diode Plugin matching criteria"

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            type=str,
            help='Output file path (default: stdout)',
        )

    def extract_condition_description(self, condition) -> str:
        """Extract a human-readable description of a Q condition."""
        if condition is None:
            return "None"
        
        # Handle simple conditions
        if hasattr(condition, 'children'):
            conditions = []
            for child in condition.children:
                if isinstance(child, tuple):
                    field, value = child
                    if field.endswith('__isnull'):
                        field_name = field[:-8]
                        if value:
                            conditions.append(f"{field_name} is NULL")
                        else:
                            conditions.append(f"{field_name} is NOT NULL")
                    else:
                        conditions.append(f"{field} = {value}")
                else:
                    conditions.append(str(child))
            
            connector = " AND " if condition.connector == "AND" else " OR "
            return connector.join(conditions)
        
        return str(condition)

    def get_matcher_description(self, matcher) -> str:
        """Generate a human-readable description of what the matcher does."""
        if hasattr(matcher, 'ip_fields') and hasattr(matcher, 'vrf_field'):
            # IP Network matcher
            ip_fields_str = ", ".join(matcher.ip_fields)
            if matcher.name.startswith('logical_ip_address_global_no_vrf'):
                return f"Matches IP address {ip_fields_str} in global namespace (no VRF)"
            elif matcher.name.startswith('logical_ip_address_within_vrf'):
                return f"Matches IP address {ip_fields_str} within VRF"
            elif matcher.name.startswith('logical_ip_range'):
                return f"Matches IP range {ip_fields_str} within VRF context"
        
        # Standard field-based matcher
        if hasattr(matcher, 'fields') and matcher.fields:
            fields_str = ", ".join(matcher.fields)
            if hasattr(matcher, 'condition') and matcher.condition:
                condition_desc = self.extract_condition_description(matcher.condition)
                return f"Matches on fields: {fields_str} where {condition_desc}"
            else:
                return f"Matches on fields: {fields_str}"
        
        return "Custom matcher"

    def get_version_constraints(self, matcher) -> Optional[str]:
        """Get version constraints as a string."""
        constraints = []
        if hasattr(matcher, 'min_version') and matcher.min_version:
            constraints.append(f"≥{matcher.min_version}")
        if hasattr(matcher, 'max_version') and matcher.max_version:
            constraints.append(f"≤{matcher.max_version}")
        
        return " ".join(constraints) if constraints else None

    def analyze_logical_matchers(self) -> Dict[str, List[MatcherInfo]]:
        """Analyze the logical matchers and extract documentation information."""
        documentation = {}
        
        for object_type, matcher_factory in _LOGICAL_MATCHERS.items():
            matchers = matcher_factory()
            matcher_infos = []
            
            for matcher in matchers:
                info = MatcherInfo(
                    name=matcher.name,
                    fields=list(matcher.fields) if hasattr(matcher, 'fields') and matcher.fields else None,
                    condition=self.extract_condition_description(matcher.condition) if hasattr(matcher, 'condition') else None,
                    description=self.get_matcher_description(matcher),
                    matcher_type=matcher.__class__.__name__,
                    version_constraints=self.get_version_constraints(matcher)
                )
                matcher_infos.append(info)
            
            documentation[object_type] = matcher_infos
        
        return documentation

    def generate_markdown_table(self, docs: Dict[str, List[MatcherInfo]]) -> str:
        """Generate a markdown table from the documentation."""
        markdown = []
        markdown.append("# NetBox Diode Plugin - Object Matching Criteria")
        markdown.append("")
        markdown.append("This document describes how the Diode NetBox Plugin matches existing objects when applying changes.")
        markdown.append("")
        
        # Sort object types for consistent output
        sorted_object_types = sorted(docs.keys())
        
        for object_type in sorted_object_types:
            matchers = docs[object_type]
            
            markdown.append(f"## {object_type}")
            markdown.append("")
            
            if not matchers:
                markdown.append("No specific matching criteria defined.")
                markdown.append("")
                continue
            
            # Create table header
            markdown.append("| Matcher Name | Fields | Condition | Description | Version Constraints |")
            markdown.append("|--------------|--------|-----------|-------------|-------------------|")
            
            for matcher in matchers:
                fields_str = ", ".join(matcher.fields) if matcher.fields else "N/A"
                condition_str = matcher.condition if matcher.condition and matcher.condition != "None" else "N/A"
                version_str = matcher.version_constraints if matcher.version_constraints else "All versions"
                
                # Escape pipe characters in table cells
                name = matcher.name.replace("|", "\\|")
                fields_str = fields_str.replace("|", "\\|")
                condition_str = condition_str.replace("|", "\\|")
                description = matcher.description.replace("|", "\\|")
                version_str = version_str.replace("|", "\\|")
                
                markdown.append(f"| {name} | {fields_str} | {condition_str} | {description} | {version_str} |")
            
            markdown.append("")
        
        return "\n".join(markdown)

    def handle(self, *args, **options):
        """Handle the command execution."""
        self.stdout.write("Analyzing matching criteria...")
        docs = self.analyze_logical_matchers()
        
        self.stdout.write("Generating markdown documentation...")
        markdown_content = self.generate_markdown_table(docs)
        
        # Output to file or stdout
        if options['output']:
            with open(options['output'], 'w') as f:
                f.write(markdown_content)
            self.stdout.write(
                self.style.SUCCESS(f"Documentation generated and saved to: {options['output']}")
            )
        else:
            self.stdout.write(markdown_content) 