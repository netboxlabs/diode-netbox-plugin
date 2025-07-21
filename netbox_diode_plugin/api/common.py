#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - API - Common types and utilities."""

import datetime
import decimal
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from zoneinfo import ZoneInfo

import netaddr
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.backends.postgresql.psycopg_any import NumericRange
from extras.models import CustomField
from netaddr.eui import EUI
from rest_framework import status

logger = logging.getLogger("netbox.diode_data")

NON_FIELD_ERRORS = "__all__"

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
        if not isinstance(other, UnresolvedReference):
            return False
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
    warnings: dict | None = field(default=None)

    def to_dict(self) -> dict:
        """Convert the change set to a dictionary."""
        d = {
            "id": self.id,
            "changes": [change.to_dict() for change in self.changes],
            "branch": self.branch,
        }
        if self.warnings:
            d["warnings"] = self.warnings
        return d

    def validate(self) -> dict[str, list[str]]:
        """Validate basics of the change set data."""
        errors = defaultdict(dict)

        for change in self.changes:
            model = apps.get_model(change.object_type)

            change_data = change.data.copy()
            if change.before:
                change_data.update(change.before)

            excluded_relation_fields, rel_errors = self._validate_relations(change_data, model)
            if rel_errors:
                errors[change.object_type] = rel_errors

            try:
                custom_fields = change_data.pop('custom_fields', None)
                if custom_fields:
                    self._validate_custom_fields(custom_fields, model)

                instance = model(**change_data)
                instance.clean_fields(exclude=excluded_relation_fields)
            except ValidationError as e:
                errors[change.object_type].update(_error_dict(e))

        return errors or None

    def _validate_custom_fields(self, data: dict, model: models.Model) -> None:
        custom_fields = {
            cf.name: cf for cf in CustomField.objects.get_for_model(model)
        }

        unknown_errors = []
        for field_name, value in data.items():
            if field_name not in custom_fields:
                unknown_errors.append(f"Unknown field name '{field_name}' in custom field data.")
                continue
        if unknown_errors:
            raise ValidationError({
                "custom_fields": unknown_errors
            })

        req_errors = []
        for field_name, cf in custom_fields.items():
            if cf.required and field_name not in data:
                req_errors.append(f"Custom field '{field_name}' is required.")
        if req_errors:
            raise ValidationError({
                "custom_fields": req_errors
            })

    def _validate_relations(self, change_data: dict, model: models.Model) -> tuple[list[str], dict]:
        # check that there is some value for every required
        # reference field, but don't validate the actual reference.
        # the fields are removed from the change_data so that other
        # fields can be validated by instantiating the model.
        excluded_relation_fields = []
        rel_errors = defaultdict(list)
        for f in model._meta.get_fields():
            if isinstance(f, GenericRelation | GenericForeignKey):
                excluded_relation_fields.append(f.name)
                continue
            if not f.is_relation:
                continue
            field_name = f.name
            excluded_relation_fields.append(field_name)

            if hasattr(f, "related_model") and f.related_model == ContentType:
                change_data.pop(field_name, None)
                base_field = field_name[:-5]
                excluded_relation_fields.append(base_field + "_id")
                value = change_data.pop(base_field + "_id", None)
            else:
                value = change_data.pop(field_name, None)

            if not f.null and not f.blank and not f.many_to_many:
                # this field is a required relation...
                if value is None:
                    rel_errors[f.name].append(f"Field {f.name} is required")
        return excluded_relation_fields, rel_errors


@dataclass
class ChangeSetResult:
    """A result of applying a change set."""

    id: str | None = field(default_factory=lambda: str(uuid.uuid4()))
    change_set: ChangeSet | None = field(default=None)
    errors: dict | None = field(default=None)

    def to_dict(self) -> dict:
        """Convert the result to a dictionary."""
        result = {
            "id": self.id,
            "errors": self.errors,
        }

        if self.change_set:
            result["change_set"] = self.change_set.to_dict()

        return result

    def get_status_code(self) -> int:
        """Get the status code for the result."""
        return status.HTTP_200_OK if not self.errors else status.HTTP_400_BAD_REQUEST


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

def _error_dict(e: ValidationError) -> dict:
    """Convert a ValidationError to a dictionary."""
    if hasattr(e, "error_dict"):
        return e.error_dict
    return {
        "__all__": e.error_list
    }

@dataclass
class AutoSlug:
    """A class that marks an auto-generated slug."""

    field_name: str
    value: str

def error_from_validation_error(e, object_name):
    """Convert a from DRF ValidationError to a ChangeSetException."""
    errors = {}
    if e.detail:
        if isinstance(e.detail, dict):
            errors[object_name] = e.detail
        elif isinstance(e.detail, list | tuple):
            errors[object_name] = {
                NON_FIELD_ERRORS: e.detail
            }
        else:
            errors[object_name] = {
                NON_FIELD_ERRORS: [e.detail]
            }
    return ChangeSetException("validation error", errors=errors)

def harmonize_formats(data):
    """Puts all data in a format that can be serialized and compared."""
    match data:
        case None:
            return None
        case str() | int() | float() | bool() | decimal.Decimal() | UnresolvedReference():
            return data
        case dict():
            return {k: harmonize_formats(v) if not k.startswith("_") else v for k, v in data.items()}
        case list() | tuple():
            return [harmonize_formats(v) for v in data]
        case datetime.datetime():
            return data.strftime("%Y-%m-%dT%H:%M:%SZ")
        case datetime.date():
            return data.strftime("%Y-%m-%d")
        case NumericRange():
            return [data.lower, data.upper-1]
        case netaddr.IPNetwork() | EUI() | ZoneInfo():
            return str(data)
        case _:
            logger.warning(f"Unknown type in harmonize_formats: {type(data)}")
            return data

def sort_ints_first(data):
  """Sort a mixed list of ints and other types, putting ints first."""
  return sorted(data, key=lambda x: (not isinstance(x, int), x))
