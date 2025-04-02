#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Common types and utilities."""

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum

from django.apps import apps
from django.core.exceptions import ValidationError
from rest_framework import status

logger = logging.getLogger("netbox.diode_data")

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


class ChangeType(Enum):
    """Change type enum."""

    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"


@dataclass
class Change:
    """A change to a model instance."""

    change_type: ChangeType
    object_type: str
    object_id: int | None = field(default=None)
    object_primary_value: str | None = field(default=None)
    ref_id: str | None = field(default=None)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    before: dict | None = field(default=None)
    data: dict | None = field(default=None)
    new_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert the change to a dictionary."""
        return {
            "id": self.id,
            "change_type": self.change_type.value,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "ref_id": self.ref_id,
            "object_primary_value": self.object_primary_value,
            "before": self.before,
            "data": self.data,
            "new_refs": self.new_refs,
        }


@dataclass
class ChangeSet:
    """A set of changes to a model instance."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    changes: list[Change] = field(default_factory=list)
    branch: dict[str, str] | None = field(default=None)  # {"id": str, "name": str}
    _refs: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert the change set to a dictionary."""
        return {
            "id": self.id,
            "changes": [change.to_dict() for change in self.changes],
            "branch": self.branch,
        }

    def _update_refs(self, model, data):
        for k, v in data.items():
            field = model._meta.get_field(k) if hasattr(model._meta, 'get_field') else None

            if field and field.is_relation:
                field_model = field.related_model
                if field.many_to_one:
                    if isinstance(v, (int, str)) and v in self._refs:
                        data[k] = field_model(**self._refs[v])
                elif field.many_to_many:
                    if isinstance(v, list):
                        data[k] = [field_model(**self._refs[item]) for item in v]

    def validate(self) -> dict[str, list[str]]:
        """Validate the change set data."""
        errors = {}

        for change in self.changes:
            object_id = change.ref_id or change.object_id

            model = apps.get_model(change.object_type)

            change_data = change.data.copy()

            if object_id and object_id not in self._refs:
                self._refs[object_id] = change_data

            self._update_refs(model, change_data)

            try:
                instance = model(**change_data)

                # Get all required relation fields (non-null, non-blank, non-m2m)
                required_relation_fields = [
                    field.name for field in model._meta.get_fields()
                    if field.is_relation and not field.null and not field.blank and not field.many_to_many
                ]

                # Create a list of relation fields that have values and should be excluded from validation
                excluded_relation_fields = [
                    field.name for field in instance._meta.fields
                    if field.name in required_relation_fields and getattr(instance, field.name, None) is not None
                ]

                instance.clean_fields(exclude=excluded_relation_fields)
            except ValidationError as e:
                errors[change.object_type] = e.error_dict

        return errors


@dataclass
class ChangeSetResult:
    """A result of applying a change set."""

    id: str | None = field(default_factory=lambda: str(uuid.uuid4()))
    change_set: ChangeSet | None = field(default=None)
    errors: dict | None = field(default=None)

    def to_dict(self) -> dict:
        """Convert the result to a dictionary."""
        return {
            "id": self.id,
            "errors": self.errors,
            "change_set": self.change_set.to_dict() if self.change_set else None,
        }

    def get_status_code(self) -> int:
        """Get the status code for the result."""
        return status.HTTP_200_OK


class ChangeSetException(Exception):
    """ChangeSetException is raised when an error occurs while generating or applying a change set."""

    def __init__(self, message, errors=None):
        """Initialize the exception."""
        super().__init__(message)
        self.message = message
        self.errors = errors or {}

    def __str__(self):
        """Return the string representation of the exception."""
        if self.errors:
            return f"{self.message}: {self.errors}"
        return self.message
