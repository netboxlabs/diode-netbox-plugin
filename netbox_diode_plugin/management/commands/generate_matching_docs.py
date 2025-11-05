#!/usr/bin/env python
"""Django management command to generate markdown documentation for NetBox Diode Plugin matching criteria."""

from dataclasses import dataclass
from typing import Optional

from django.core.management.base import BaseCommand

from netbox_diode_plugin.api.differ import extract_supported_models
from netbox_diode_plugin.api.matcher import _LOGICAL_MATCHERS, get_model_matchers


@dataclass
class MatcherInfo:
    """Information about a matcher for documentation."""

    name: str
    fields: list[str] | None = None
    condition: str | None = None
    description: str | None = None
    matcher_type: str = "ObjectMatchCriteria"
    version_constraints: str | None = None
    matcher_source: str = "logical"  # "logical" or "builtin"


class Command(BaseCommand):
    """Django management command to generate markdown documentation for NetBox Diode Plugin matching criteria."""

    help = "Generate markdown documentation for NetBox Diode Plugin matching criteria"

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

    def get_matcher_description(self, matcher) -> str:  # noqa: C901
        """Generate a human-readable description of what the matcher does."""
        # Handle IP Network matchers
        if hasattr(matcher, 'ip_fields') and matcher.ip_fields and hasattr(matcher, 'vrf_field') and matcher.vrf_field:
            ip_fields_str = ", ".join(matcher.ip_fields)
            if matcher.name.startswith('logical_ip_address_global_no_vrf'):
                return f"Matches IP address {ip_fields_str} in global namespace (no VRF)"
            if matcher.name.startswith('logical_ip_address_within_vrf'):
                return f"Matches IP address {ip_fields_str} within VRF"
            if matcher.name.startswith('logical_ip_range'):
                return f"Matches IP range {ip_fields_str} within VRF context"

        # Handle CustomFieldMatcher
        if hasattr(matcher, 'custom_field') and matcher.custom_field:
            return f"Matches on unique custom field: {matcher.custom_field}"

        # Handle AutoSlugMatcher
        if hasattr(matcher, 'slug_field') and matcher.slug_field:
            return f"Matches on auto-generated slug field: {matcher.slug_field}"

        # Handle builtin unique field matchers
        if matcher.name.startswith('unique_') and hasattr(matcher, 'fields') and matcher.fields:
            field_name = matcher.fields[0] if len(matcher.fields) == 1 else ", ".join(matcher.fields)
            if matcher.name.startswith('unique_'):
                return f"Matches on unique field(s): {field_name}"

        # Handle builtin UniqueConstraint matchers
        if hasattr(matcher, 'fields') and matcher.fields and not matcher.name.startswith('logical_'):
            fields_str = ", ".join(matcher.fields)
            if hasattr(matcher, 'condition') and matcher.condition:
                condition_desc = self.extract_condition_description(matcher.condition)
                return f"Matches on unique constraint fields: {fields_str} where {condition_desc}"
            return f"Matches on unique constraint fields: {fields_str}"

        # Standard field-based matcher
        if hasattr(matcher, 'fields') and matcher.fields:
            fields_str = ", ".join(matcher.fields)
            if hasattr(matcher, 'condition') and matcher.condition:
                condition_desc = self.extract_condition_description(matcher.condition)
                return f"Matches on fields: {fields_str} where {condition_desc}"
            return f"Matches on fields: {fields_str}"

        return "Custom matcher"

    def get_version_constraints(self, matcher) -> str | None:
        """Get version constraints as a string."""
        constraints = []
        if hasattr(matcher, 'min_version') and matcher.min_version:
            constraints.append(f"≥{matcher.min_version}")
        if hasattr(matcher, 'max_version') and matcher.max_version:
            constraints.append(f"≤{matcher.max_version}")

        return " ".join(constraints) if constraints else None

    def analyze_logical_matchers(self) -> dict[str, list[MatcherInfo]]:
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
                    version_constraints=self.get_version_constraints(matcher),
                    matcher_source="logical"
                )
                matcher_infos.append(info)

            documentation[object_type] = matcher_infos

        return documentation

    def analyze_builtin_matchers(self) -> dict[str, list[MatcherInfo]]:
        """Analyze the builtin matchers and extract documentation information."""
        documentation = {}

        for object_type, model_info in extract_supported_models().items():
            model_class = model_info["model"]
            matchers = get_model_matchers(model_class)
            matcher_infos = []

            for matcher in matchers:
                # Skip logical matchers as they're already handled
                if matcher.name.startswith('logical_'):
                    continue

                # Extract fields for builtin matchers
                fields = None
                if hasattr(matcher, 'fields') and matcher.fields:
                    fields = list(matcher.fields)
                elif hasattr(matcher, 'custom_field'):
                    fields = [f"custom_fields.{matcher.custom_field}"]
                elif hasattr(matcher, 'slug_field'):
                    fields = [matcher.slug_field]

                info = MatcherInfo(
                    name=matcher.name,
                    fields=fields,
                    condition=self.extract_condition_description(matcher.condition) if hasattr(matcher, 'condition') else None,
                    description=self.get_matcher_description(matcher),
                    matcher_type=matcher.__class__.__name__,
                    version_constraints=self.get_version_constraints(matcher),
                    matcher_source="builtin"
                )
                matcher_infos.append(info)

            if matcher_infos:  # Only add if there are builtin matchers
                documentation[object_type] = matcher_infos

        return documentation

    def combine_matchers(
        self,
        logical_docs: dict[str, list[MatcherInfo]],
        builtin_docs: dict[str, list[MatcherInfo]],
    ) -> dict[str, list[MatcherInfo]]:
        """Combine logical and builtin matchers into a single documentation structure."""
        combined = {}

        # Get all object types
        all_object_types = set(logical_docs.keys()) | set(builtin_docs.keys())

        for object_type in all_object_types:
            matchers = []

            # Add logical matchers
            if object_type in logical_docs:
                matchers.extend(logical_docs[object_type])

            # Add builtin matchers
            if object_type in builtin_docs:
                matchers.extend(builtin_docs[object_type])

            if matchers:
                combined[object_type] = matchers

        return combined

    def generate_markdown_table(self, docs: dict[str, list[MatcherInfo]]) -> str:
        """Generate a markdown table from the documentation."""
        markdown = []
        markdown.append("# NetBox Diode Plugin - Object Matching Criteria")
        markdown.append("")
        markdown.append(
            "This document describes how the Diode NetBox Plugin matches existing objects when applying changes. "
            "The matchers will be applied in the order of their precedence, unttil one of them matches."
        )
        markdown.append("")
        markdown.append("## Matcher Types")
        markdown.append("")
        markdown.append("- **Logical Matchers**: Custom matching criteria that represent likely user intent")
        markdown.append(
            "- **Builtin Matchers**: Automatically generated from NetBox model constraints "
            "(unique fields, unique constraints, custom fields, auto-slugs)"
        )
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
            markdown.append("| Matcher Name | Order of Precedence | Type | Fields | Condition | Description | Version Constraints |")
            markdown.append("|--------------|---------------------|------|--------|-----------|-------------|---------------------|")

            for precedence, matcher in enumerate(matchers, start=1):
                # Escape pipe characters in table cells
                name = matcher.name.replace("|", "\\|") if matcher.name else "N/A"
                matcher_type = matcher.matcher_source.replace("|", "\\|")
                fields_str = ", ".join(matcher.fields).replace("|", "\\|") if matcher.fields else ""
                condition_str = matcher.condition.replace("|", "\\|") if matcher.condition and matcher.condition != "None" else "N/A"
                description = matcher.description.replace("|", "\\|") if matcher.description else "N/A"
                version_str = matcher.version_constraints.replace("|", "\\|") if matcher.version_constraints else "All versions"

                markdown.append(
                    f"| {name} | {precedence} | {matcher_type} | {fields_str} | {condition_str} | {description} | {version_str} |"
                )

            markdown.append("")

        return "\n".join(markdown)

    def handle(self, *args, **options):
        """Handle the command execution."""
        self.stdout.write("Analyzing logical matching criteria...")
        logical_docs = self.analyze_logical_matchers()

        self.stdout.write("Analyzing builtin matching criteria...")
        builtin_docs = self.analyze_builtin_matchers()

        self.stdout.write("Combining matchers...")
        combined_docs = self.combine_matchers(logical_docs, builtin_docs)

        self.stdout.write("Generating markdown documentation...")
        markdown_content = self.generate_markdown_table(combined_docs)
        self.stdout.write(markdown_content)
