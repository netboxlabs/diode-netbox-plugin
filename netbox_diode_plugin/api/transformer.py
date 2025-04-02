#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Object resolution for diffing."""

import copy
import json
import logging
import re
from collections import defaultdict
from functools import lru_cache
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .common import UnresolvedReference
from .matcher import find_existing_object, fingerprint, merge_data
from .plugin_utils import get_json_ref_info, get_primary_value

logger = logging.getLogger("netbox.diode_data")

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
    output = _handle_post_creates(resolved)
    logger.error(f"_merge_post_creates: {json.dumps(output, default=lambda o: str(o), indent=4)}")

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

    post_create = {}

    for key, value in proto_json.items():
        ref_info = get_json_ref_info(object_type, key)
        if ref_info is None:
            transformed[_camel_to_snake_case(key)] = copy.deepcopy(value)
            continue

        nested_context = _nested_context(object_type, uuid, ref_info.field_name)
        field_name = ref_info.field_name
        is_circular = _is_circular_reference(object_type, field_name)

        if ref_info.is_generic:
            transformed[field_name + "_type"] = ref_info.object_type
            field_name = field_name + "_id"

        nested_refs = []
        ref_value = None
        if isinstance(value, list):
            ref_value = []
            for item in value:
                nested = _transform_proto_json_1(item, ref_info.object_type, nested_context)
                nested_refs += nested
                ref = nested[-1]
                ref_value.append(UnresolvedReference(
                    object_type=ref_info.object_type,
                    uuid=ref['_uuid'],
                ))
        else:
            nested_refs = _transform_proto_json_1(value, ref_info.object_type, nested_context)
            ref = nested_refs[-1]
            ref_value = UnresolvedReference(
                object_type=ref_info.object_type,
                uuid=ref['_uuid'],
            )
        if is_circular:
            post_create[field_name] = ref_value
            entities = entities + nested_refs
        else:
            transformed[field_name] = ref_value
            entities = nested_refs + entities

    # if there are fields that must be deferred until after the object is created,
    # add a new entity with the post-create data. eg a child object that references
    # this object and is also referenced by this object such as primary mac address
    # on an interface.
    # if this object already exists, two steps are not needed, and this will be
    # simplified in a later pass.
    if len(post_create) > 0:
        post_create_uuid = str(uuid4())
        post_create['_uuid'] = post_create_uuid
        post_create['_instance'] = uuid
        post_create['_object_type'] = object_type
        transformed['_post_create'] = post_create_uuid
        entities.append(post_create)

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
    source_value = get_primary_value(data, object_type)
    if source_value is not None:
        return slugify(str(source_value))
    return None

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

def _handle_post_creates(entities: list[dict]) -> list[str]:
    """Merges any unnecessary post-create steps for existing objects."""
    by_uuid = {x['_uuid']: x for x in entities}
    out = []
    for entity in entities:
        post_create = entity.pop('_post_create', None)
        if post_create is None:
            out.append(entity)
            continue

        post_create = by_uuid[post_create]
        if entity.get('_instance') is not None:
            # this entity has a post-create, but it has already been
            # created. in this case we can just merge this entity into
            # the post-create entity and skip it without worrying about
            # references to it.
            post_create.update(entity)
        else:
            # this entity will be created.
            # in this case we need to fix up the identifier in the post-create
            # to refer to the created object.
            post_create['id'] = entity['id']
            out.append(entity)
    return out

def _check_unresolved_refs(entities: list[dict]) -> list[str]:
    seen = set()
    for e in entities:
        seen.add((e['_object_type'], e['_uuid']))
        for k, v in e.items():
            if isinstance(v, UnresolvedReference):
                if (v.object_type, v.uuid) not in seen:
                    raise ValueError(f"Unresolved reference {v} in {e} does not refer to a prior created object (circular reference?)")
