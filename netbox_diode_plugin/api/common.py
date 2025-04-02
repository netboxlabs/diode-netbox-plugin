#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Common types and utilities."""

from dataclasses import dataclass


@dataclass
class UnresolvedReference:
    """unresolved reference to an object."""

    object_type: str
    uuid: str

    def __str__(self):
        """String representation of the unresolved reference."""
        return f"new_object:{self.object_type}:{self.uuid}"

    def __eq__(self, other):
        """Equality operator."""
        if not isinstance(other, UnresolvedReference):
            return False
        return self.object_type == other.object_type and self.uuid == other.uuid

    def __hash__(self):
        """Hash function."""
        return hash((self.object_type, self.uuid))

    def __lt__(self, other):
        """Less than operator."""
        return self.object_type < other.object_type or (self.object_type == other.object_type and self.uuid < other.uuid)
