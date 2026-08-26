#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""NetBox Diode Data - API supported models."""

import importlib
import logging
import time
from functools import cache, lru_cache

from django.apps import apps
from django.db import models
from django.db.models import ManyToOneRel
from django.db.models.fields import NOT_PROVIDED
from netbox.api.exceptions import SerializerNotFound
from rest_framework import serializers
from utilities.api import get_serializer_for_model as netbox_get_serializer_for_model

from .plugin_utils import legal_fields, legal_object_types

logger = logging.getLogger(__name__)


@cache
def extract_supported_models() -> dict[str, dict]:
    """Extract supported models from installed NetBox apps / version."""
    start_ts = time.time()

    extracted_models: dict[str, dict] = {}
    possible_object_types = legal_object_types()

    for object_type in possible_object_types:
        try:
            app_label, model_name = object_type.split(".")
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        try:
            fields, serializer_only = _get_model_fields(model)
            if not fields:
                continue

            extracted_models[object_type] = {
                "fields": fields,
                "model": model,
                "serializer_only_fields": serializer_only,
            }
        except Exception as e:
            logger.error(f"extract_supported_models: {model.__name__} error: {e}")

    finish_ts = time.time()
    elapsed_millis = (finish_ts - start_ts) * 1000
    logger.info(
        f"done extracting supported diode models in {elapsed_millis:.2f} milliseconds - extracted_models: {len(extracted_models)}"
    )

    return extracted_models

def _get_model_fields(model_class) -> tuple[dict, set]:
    """
    Classify this model's legal wire fields for the running NetBox version.

    Returns (fields_info, serializer_only). fields_info maps each ingestable
    wire name to type/default metadata plus the model attribute ("source")
    backing it — the wire name and model field name differ for serializer
    aliases (e.g. a serializer field declared with source=...). serializer_only
    collects wire names the serializer accepts for write that have no backing
    model field (create-time options); they are rejected with a specific
    warning instead of silently dropped.
    """
    legal = legal_fields(model_class)

    try:
        serializer_fields = get_serializer_for_model(model_class)().get_fields()
    except SerializerNotFound:
        # e.g. core.managedfile — keep the legacy model-walk behavior wholesale
        return _model_walk_fields(model_class), set()

    fields_info: dict[str, dict] = {}
    serializer_only: set[str] = set()

    # The serializer exposes "id" read-only, but it is required downstream
    # (prechange identity, Change.object_id); seed it from the model PK.
    pk = model_class._meta.pk
    fields_info["id"] = {
        "type": pk.get_internal_type(),
        "default": _field_default(pk),
        "source": "id",
    }

    for wire_name in legal:
        if wire_name == "custom_fields":
            # owned end-to-end by the dedicated custom-fields path
            continue
        field = serializer_fields.get(wire_name)
        if field is None or field.read_only:
            # not writable on this NetBox version (stale union entry)
            continue
        source = field.source or wire_name
        if source == "*":
            serializer_only.add(wire_name)
            continue
        source = source.split(".")[0]
        try:
            model_field = model_class._meta.get_field(source)
            fields_info[wire_name] = {
                "type": model_field.get_internal_type(),
                "default": _field_default(model_field),
                "source": source,
            }
        except Exception:
            # writable serializer field with no (introspectable) model field:
            # surfaced as a specific warning, never silently dropped
            serializer_only.add(wire_name)

    return fields_info, serializer_only


def _model_walk_fields(model_class) -> dict:
    """Legacy gate: intersect model attribute names with the legal fields."""
    legal = legal_fields(model_class)
    fields_info: dict[str, dict] = {}
    for field in model_class._meta.get_fields():
        field_name = field.name
        if field_name not in legal and field_name != 'id':
            continue
        fields_info[field_name] = {
            "type": field.get_internal_type(),
            "default": _field_default(field),
            "source": field_name,
        }
    return fields_info


def _field_default(field):
    """Extract a field's default value the way the gate always has."""
    default_value = None
    if hasattr(field, "default"):
        default_value = (
            field.default if field.default not in (NOT_PROVIDED, dict) else None
        )
    return default_value

@lru_cache(maxsize=128)
def get_serializer_for_model(model, prefix=""):
    """Cached wrapper for NetBox's get_serializer_for_model function."""
    return netbox_get_serializer_for_model(model, prefix)
