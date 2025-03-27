#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Applier."""


import logging
from dataclasses import dataclass, field

from django.apps import apps
from django.db import models

from .differ import Change, ChangeSet, ChangeType

logger = logging.getLogger(__name__)


@dataclass
class ApplyChangeSetResult:
    """A result of applying a change set."""

    id: str
    success: bool
    errors: dict | None = field(default=None)

    def to_dict(self) -> dict:
        """Convert the result to a dictionary."""
        return {
            "id": self.id,
            "success": self.success,
            "errors": self.errors,
        }


class ApplyChangeSetException(Exception):
    """ApplyChangeSetException is raised when an error occurs while applying a change set."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}

    def __str__(self):
        if self.errors:
            return f"{self.message}: {self.errors}"
        return self.message


def apply_changeset(change_set: ChangeSet) -> ApplyChangeSetResult:
    """Apply a change set."""

    created = {}

    for change in change_set.changes:
        change_type = change.change_type
        object_type = change.object_type
        data = change.data
        new_refs = change.new_refs

        app_label, model_name = object_type.split(".")
        model_class = apps.get_model(app_label, model_name)
        
        fk_fields = {
            field.name: field.related_model
            for field in model_class._meta.get_fields()
            if field.is_relation
        }

        for ref_field in new_refs:
                data[ref_field] = created[data[ref_field]]

        # get model fields matching data keys if foreign key
        for key, value in data.items():
            if fk_model := fk_fields.get(key):
                if isinstance(value, int):
                    data[key] = fk_model.objects.get(id=value)
                elif isinstance(value, models.Model):
                    data[key] = value

        if change_type == ChangeType.CREATE.value:
            new_object = model_class.objects.create(**data)
            created[change.ref_id] = new_object
        
        elif change_type == ChangeType.UPDATE.value:
            object_id = change.object_id
            if object_id is None:
                raise ApplyChangeSetException(f"Object ID is required for update")

            model_class.objects.filter(id=object_id).update(**data)
        elif change_type == ChangeType.NOOP.value:
            pass
        
        else:
            raise ApplyChangeSetException(f"Unknown change type: {change.type}")

    return ApplyChangeSetResult(
        id=change_set.id,
        success=True,
        errors=None,
    )

