#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Applier."""


import logging
from dataclasses import dataclass, field

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
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
        """Initialize the exception."""
        super().__init__(message)
        self.message = message
        self.errors = errors or {}

    def __str__(self):
        """Return the string representation of the exception."""
        if self.errors:
            return f"{self.message}: {self.errors}"
        return self.message


def apply_changeset(change_set: ChangeSet) -> ApplyChangeSetResult:
    """Apply a change set."""
    created = {}

    def pre_apply(model_class: models.Model, change: Change) -> tuple[dict, list]:
        """Pre-apply the data."""

        data = change.data.copy()

        # get foreign key fields with model
        fk_fields = {
            field.name: field.related_model
            for field in model_class._meta.get_fields()
            if field.is_relation
        }
        
        # resolve foreign key references
        for ref_field in change.new_refs:
            if isinstance(data[ref_field], (list, tuple)):
                ref_list = []
                for ref in data[ref_field]:
                    if isinstance(ref, str):
                        ref_list.append(created[ref])
                    elif isinstance(ref, models.Model):
                        ref_list.append(ref)
                data[ref_field] = ref_list
            else:
                data[ref_field] = created[data[ref_field]]
        
        tags = data.pop("tags", None)
        if tags:
            tags_model_class = fk_fields.get("tags")
            if isinstance(tags, list) and isinstance(tags[0], models.Model):
                tags = [tag.pk for tag in tags]
            tags = tags_model_class.objects.filter(id__in=tags)

        # resolve contenttype fields
        for key, value in data.items():
            field_type = fk_fields.get(key)
            if field_type and field_type == ContentType:
                data[key] = ContentType.objects.get(app_label=value.split(".")[0], model=value.split(".")[1])
                # If the field name ends with _type, extract the base field name for the ID field
                content_type_id_field = f"{key[:-5]}_id"
                content_type_id_value = data[content_type_id_field]
                if isinstance(content_type_id_value, str):
                    data[content_type_id_field] = int(content_type_id_value)
        
        # get model fields matching data keys if foreign key
        # TODO: consider use of existing model serializers accepting PKs
        for key, value in data.items():
            if fk_model := fk_fields.get(key):
                if isinstance(value, int):
                    # ensure the value is an integer
                    data[key] = fk_model.objects.get(id=value)
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], models.Model):
                    data[key] = [ref.pk for ref in value]
                elif isinstance(value, models.Model):
                    data[key] = value

        return data, tags
    
    def post_apply(instance: models.Model, tags: list[models.Model]):
        """Post-apply the data."""

        # set tags
        if tags and hasattr(instance, "tags"):
            instance.tags.set(tags)

    for change in change_set.changes:
        change_type = change.change_type
        object_type = change.object_type

        app_label, model_name = object_type.split(".")
        model_class = apps.get_model(app_label, model_name)

        data, tags = pre_apply(model_class, change)
        instance = None

        if change_type == ChangeType.CREATE.value:
            instance = model_class.objects.create(**data)
            created[change.ref_id] = instance

        elif change_type == ChangeType.UPDATE.value:
            if object_id := change.object_id:
                model_class.objects.filter(id=object_id).update(**data)
                instance = model_class.objects.get(id=object_id)

            # # MACAddress case (create and update in a same change set)
            # elif instance := created[change.ref_id]:
            #     instance.update(**data)
            #     if tags:
            #         instance.tags.set(tags)
            else:
                raise ApplyChangeSetException("Object ID or ref_id is required for update")

        elif change_type == ChangeType.NOOP.value:
            pass

        else:
            raise ApplyChangeSetException(f"Unknown change type: {change.type}")
        
        post_apply(instance, tags)

    return ApplyChangeSetResult(
        id=change_set.id,
        success=True,
        errors=None,
    )

