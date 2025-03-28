#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Object resolution for diffing."""

from collections import defaultdict
import copy
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import re
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .common import UnresolvedReference
from .plugin_utils import get_json_ref_info, get_primary_value_field
from .matcher import fingerprint, merge_data, find_existing_object


logger = logging.getLogger("netbox.diode_data")

_DEFAULT_SLUG_SOURCE_FIELD_NAME = "name"

@lru_cache(maxsize=128)
def _camel_to_snake_case(name):
    """Convert camelCase string to snake_case."""
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


# these are implied values pushed down to referenced objects.
_NESTED_CONTEXT = {
    "dcim.interface": {
        # interface.primary_mac_address -> mac_address.assigned_object = interface
        "primary_mac_address": lambda object_type, uuid: {
            "assigned_object_type": object_type,
            "assigned_object_id": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
    "virtualization.vminterface": {
        # interface.primary_mac_address -> mac_address.assigned_object = vinterface
        "primary_mac_address": lambda object_type, uuid: {
            "assigned_object_type": object_type,
            "assigned_object_id": UnresolvedReference(object_type=object_type, uuid=uuid),
        },
    },
}

def _no_context(object_type, uuid):
    return None

def _nested_context(object_type, uuid, field_name):
    return _NESTED_CONTEXT.get(object_type, {}).get(field_name, _no_context)(object_type, uuid)

_IS_CIRCULAR_REFERENCE = {
    "dcim.interface": frozenset(["primary_mac_address"]),
    "virtualization.vminterface": frozenset(["primary_mac_address"]),
}

def _is_circular_reference(object_type, field_name):
    return field_name in _IS_CIRCULAR_REFERENCE.get(object_type, frozenset())

def transform_proto_json(proto_json: dict, object_type: str, supported_models: dict) -> list[dict]:
    """
    Transform keys of proto json dict to flattened dictionaries with model field keys.

    This also handles placing `_type` fields for generic references,
    a certain form of deduplication and resolution of existing objects.
    """
    entities = _transform_proto_json_1(proto_json, object_type)
    logger.error(f"_transform_proto_json_1: {json.dumps(entities, default=lambda o: str(o), indent=4)}")
    deduplicated = _fingerprint_dedupe(entities)
    logger.error(f"_fingerprint_dedupe: {json.dumps(deduplicated, default=lambda o: str(o), indent=4)}")
    _set_slugs(deduplicated, supported_models)
    logger.error(f"_set_slugs: {json.dumps(deduplicated, default=lambda o: str(o), indent=4)}")
    resolved = _resolve_existing_references(deduplicated)
    logger.error(f"_resolve_references: {json.dumps(resolved, default=lambda o: str(o), indent=4)}")
    _set_defaults(resolved, supported_models)
    logger.error(f"_set_defaults: {json.dumps(resolved, default=lambda o: str(o), indent=4)}")
    output = _move_if_unresolved(resolved)
    logger.error(f"_move_if_unresolved: {json.dumps(output, default=lambda o: str(o), indent=4)}")

    _check_unresolved_refs(output)
    return output

def _transform_proto_json_1(proto_json: dict, object_type: str, context=None, existing=None) -> list[dict]:
    uuid = str(uuid4())
    transformed = {
        "_object_type": object_type,
        "_uuid": uuid,
    }
    if context is not None:
        transformed.update(context)
    existing = existing or {}
    entities = [transformed]

    move_if_unresolved = defaultdict(list)

    for key, value in proto_json.items():
        ref_info = get_json_ref_info(object_type, key)
        if ref_info is None:
            transformed[_camel_to_snake_case(key)] = copy.deepcopy(value)
            continue

        nested_context = _nested_context(object_type, uuid, ref_info.field_name)

        # nested reference
        field_name = ref_info.field_name

        # if this is potentially a circular reference, we need to mark this for
        # later checking.
        is_circular = _is_circular_reference(object_type, field_name)

        if ref_info.is_generic:
            transformed[field_name + "_type"] = ref_info.object_type
            field_name = field_name + "_id"

        if isinstance(value, list):
            ref_values = []
            for item in value:
                nested_refs = _transform_proto_json_1(item, ref_info.object_type, nested_context)
                ref = nested_refs[-1]
                if is_circular:
                    move_if_unresolved[field_name].append(ref['_uuid'])
                ref_values.append(UnresolvedReference(
                    object_type=ref_info.object_type,
                    uuid=ref['_uuid'],
                ))
                entities = nested_refs + entities
            transformed[field_name] = ref_values
        else:
            nested_refs = _transform_proto_json_1(value, ref_info.object_type, nested_context)
            ref = nested_refs[-1]
            if is_circular:
                move_if_unresolved[field_name].append(ref['_uuid'])
            transformed[field_name] = UnresolvedReference(
                object_type=ref_info.object_type,
                uuid=ref['_uuid'],
            )
            entities = nested_refs + entities
    if len(move_if_unresolved) > 0:
        transformed['_move_if_unresolved'] = move_if_unresolved
    return entities

def _set_defaults(entities: list[dict], supported_models: dict):
    for entity in entities:
        model_fields = supported_models.get(entity['_object_type'])
        if model_fields is None:
            raise ValidationError(f"Model for object type {entity['_object_type']} is not supported")

        for field_name, field_info in model_fields.get('fields', {}).items():
            if entity.get(field_name) is None and field_info.get("default") is not None:
                entity[field_name] = field_info["default"]

def _set_slugs(entities: list[dict], supported_models: dict):
    for entity in entities:
        model_fields = supported_models.get(entity['_object_type'])
        if model_fields is None:
            raise ValidationError(f"Model for object type {entity['_object_type']} is not supported")

        for field_name, field_info in model_fields.get('fields', {}).items():
            if field_info["type"] == "SlugField" and entity.get(field_name) is None:
                entity[field_name] = _generate_slug(entity['_object_type'], entity)

def _generate_slug(object_type, data):
    """Generate a slug for a model instance."""
    source_field = get_field_to_slugify(object_type)
    if source_field in data and data[source_field]:
        return slugify(str(data[source_field]))

    return None

def get_field_to_slugify(object_type):
    """Get the field to use as the source for the slug."""
    return get_primary_value_field(object_type, _DEFAULT_SLUG_SOURCE_FIELD_NAME)

def _fingerprint_dedupe(entities: list[dict]) -> list[dict]:
    by_fp = {}
    deduplicated = []
    new_refs = {} # uuid -> uuid

    for entity in entities:
        fp = fingerprint(entity, entity['_object_type'])
        existing = by_fp.get(fp)
        if existing is None:
            logger.debug("  * entity is new.")
            new_entity = copy.deepcopy(entity)
            _update_unresolved_refs(new_entity, new_refs)
            by_fp[fp] = new_entity
            deduplicated.append(fp)
        else:
            logger.debug("  * entity already exists.")
            new_refs[entity['_uuid']] = existing['_uuid']
            merged = merge_data(existing, entity)
            _update_unresolved_refs(merged, new_refs)
            by_fp[fp] = merged

    return [by_fp[fp] for fp in deduplicated]

def _update_unresolved_refs(entity, new_refs):
    for k, v in entity.items():
        if isinstance(v, UnresolvedReference) and v.uuid in new_refs:
            v.uuid = new_refs[v.uuid]
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, UnresolvedReference) and item.uuid in new_refs:
                    item.uuid = new_refs[item.uuid]
        # TODO maps ...

def _resolve_existing_references(entities: list[dict]) -> list[dict]:
    seen = {}
    new_refs = {}
    resolved = []
    for data in entities:
        object_type = data['_object_type']
        data = copy.deepcopy(data)
        _update_resolved_refs(data, new_refs)
        existing = find_existing_object(data, object_type)
        if existing is not None:
            logger.error(f"existing {data} -> {existing}")
            fp = (object_type, existing.id)
            if fp in seen:
                logger.warning(f"objects resolved to the same existing id after deduplication: {seen[fp]} and {data}")
            else:
                seen[fp] = data
            data['id'] = existing.id
            data['_instance'] = existing
            new_refs[data['_uuid']] = existing.id
            resolved.append(data)
        else:
            data['id'] = UnresolvedReference(object_type, data['_uuid'])
            _update_resolved_refs(data, new_refs)
            resolved.append(data)
    return resolved

def _update_resolved_refs(data, new_refs):
    for k, v in data.items():
        if isinstance(v, UnresolvedReference) and v.uuid in new_refs:
            data[k] = new_refs[v.uuid]
        elif isinstance(v, (list, tuple)):
            new_items = []
            for item in v:
                if isinstance(item, UnresolvedReference) and item.uuid in new_refs:
                    new_items.append(new_refs[item.uuid])
                else:
                    new_items.append(item)
            data[k] = new_items
        # TODO maps ...

def cleanup_unresolved_references(data: dict) -> list[str]:
    """Find and stringify unresolved references in fields."""
    unresolved = set()
    for k, v in data.items():
        if isinstance(v, UnresolvedReference):
            if k != 'id':
                unresolved.add(k)
            data[k] = str(v)
        elif isinstance(v, (list, tuple)):
            items = []
            for item in v:
                if isinstance(item, UnresolvedReference):
                    unresolved.add(k)
                    items.append(str(item))
                else:
                    items.append(item)
            data[k] = items
        # TODO maps
    return sorted(unresolved)

def _move_if_unresolved(entities: list[dict]) -> list[str]:
    min_index = {}
    by_uuid = {x['_uuid']: x for x in entities}

    cur = 1
    for entity in entities:
        min_index[entity['_uuid']] = cur
        cur += 1

        moves = entity.pop('_move_if_unresolved', None)
        if moves is None or entity.get('_instance') is not None:
            continue

        logger.debug(f"  * {entity} needs circular reference moves: {moves}")
        entity2 = entity.copy()
        entity2['_uuid'] = str(uuid4())
        by_uuid[entity2['_uuid']] = entity2
        for field_name, uuids in moves.items():
            entity.pop(field_name, None)
            for uuid in uuids:
                min_index[uuid] = cur
                cur += 1

        entity2['_instance'] = entity['_uuid']
        min_index[entity2['_uuid']] = cur
        cur += 1

    in_order = sorted((min_index[x], x) for x in min_index)
    return [by_uuid[x[1]] for x in in_order]


def _check_unresolved_refs(entities: list[dict]) -> list[str]:
    seen = set()
    for e in entities:
        seen.add((e['_object_type'], e['_uuid']))
        for k, v in e.items():
            if isinstance(v, UnresolvedReference):
                if (v.object_type, v.uuid) not in seen:
                    raise ValueError(f"Unresolved reference {v} in {e} does not refer to a prior created object (circular reference?)")
