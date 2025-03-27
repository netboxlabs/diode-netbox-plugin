"""Differ."""

import copy
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from dataclasses import dataclass, field
from enum import Enum
import copy
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from utilities.data import shallow_compare_dict

from .supported_models import extract_supported_models
from .transformer import transform_proto_json, cleanup_unresolved_references
from .plugin_utils import get_primary_value

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = extract_supported_models()

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
    object_id: int | None
    object_primary_value: str
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

    def to_dict(self) -> dict:
        """Convert the change set to a dictionary."""
        return {
            "id": self.id,
            "changes": [change.to_dict() for change in self.changes],
            "branch": self.branch,
        }

def prechange_data_from_instance(instance) -> dict:
    """Convert model instance data to a dictionary format for comparison."""
    prechange_data = {}

    if instance is None:
        return prechange_data

    model_class = instance.__class__
    object_type = f"{model_class._meta.app_label}.{model_class._meta.model_name}"

    model = SUPPORTED_MODELS.get(object_type)
    if not model:
        raise ValidationError(f"Model {model_class.__name__} is not supported")

    fields = model.get("fields", {})
    if not fields:
        raise ValidationError(f"Model {model_class.__name__} has no fields")

    for field_name, field_info in fields.items():
        if not hasattr(instance, field_name):
            continue

        value = getattr(instance, field_name)
        if hasattr(value, "all"):  # Handle many-to-many and many-to-one relationships
            # For any relationship that has an 'all' method, get all related objects' primary keys
            prechange_data[field_name] = (
                [item.pk for item in value.all()] if value is not None else []
            )
        elif hasattr(
            value, "pk"
        ):  # Handle regular related fields (ForeignKey, OneToOne)
            # Handle ContentType fields
            if isinstance(value, ContentType):
                prechange_data[field_name] = f"{value.app_label}.{value.model}"
            else:
                # For regular related fields, get the primary key
                prechange_data[field_name] = value.pk if value is not None else None
        else:
            prechange_data[field_name] = value

    return prechange_data


def clean_diff_data(data: dict, exclude_empty_values: bool = True) -> dict:
    """Clean diff data by removing null values."""
    result = {}
    for k, v in data.items():
        if exclude_empty_values:
            if v is None:
                continue
            if isinstance(v, list) and len(v) == 0:
                continue
            if isinstance(v, dict) and len(v) == 0:
                continue
            if isinstance(v, str) and v == "":
                continue
        result[k] = v
    return result


def diff_to_change(
    object_type: str,
    prechange_data: dict,
    postchange_data: dict,
    changed_attrs: list[str],
    unresolved_references: list[str],
) -> Change:
    """Convert a diff to a change."""
    change_type = ChangeType.UPDATE if prechange_data.get("id") else ChangeType.CREATE
    if change_type == ChangeType.UPDATE and not len(changed_attrs) > 0:
        change_type = ChangeType.NOOP

    primary_value = get_primary_value(postchange_data, object_type)
    if primary_value is None:
        primary_value = "(unnamed)"

    change = Change(
        change_type=change_type,
        object_type=object_type,
        object_id=prechange_data.get("id"),
        object_primary_value=primary_value,
        new_refs=unresolved_references,
    )
    if change.object_id is None:
        change.ref_id = postchange_data.get("id")

    postchange_data_clean = clean_diff_data(postchange_data)

    if change_type == ChangeType.UPDATE:
        # remove null values
        prechange_data_clean = clean_diff_data(prechange_data)

        merged_data = copy.deepcopy(prechange_data_clean)

        merged_data.update({
            attr: postchange_data_clean[attr]
            for attr in changed_attrs
            if attr in postchange_data_clean
        })
        change.before = sort_dict_recursively(prechange_data_clean)
        change.data = sort_dict_recursively(merged_data)
    else:
        change.data = sort_dict_recursively(postchange_data_clean)

    return change

def sort_dict_recursively(d):
    """Recursively sorts a dictionary by keys."""
    if isinstance(d, dict):
        return {k: sort_dict_recursively(v) for k, v in sorted(d.items())}
    if isinstance(d, list):
        return sorted([sort_dict_recursively(item) for item in d])
    return d



def generate_changeset(entity: dict, object_type: str) -> ChangeSet:
    """Generate a changeset for an entity."""
    change_set = ChangeSet()

    entities = transform_proto_json(entity, object_type, SUPPORTED_MODELS)
    for entity in entities:
        prechange_data = {}
        changed_attrs = []
        new_refs = cleanup_unresolved_references(entity)
        object_type = entity.pop("_object_type")
        _ = entity.pop("_uuid")
        instance = entity.pop("_instance", None)

        if instance:
            prechange_data = prechange_data_from_instance(instance)
            changed_data = shallow_compare_dict(
                prechange_data, entity,
            )
            changed_attrs = sorted(changed_data.keys())
        change = diff_to_change(
            object_type,
            prechange_data,
            entity,
            changed_attrs,
            new_refs,
        )
        change_set.changes.append(change)
    logger.error(f"change_set: {json.dumps(change_set.to_dict(), default=str, indent=4)}")
    return change_set
