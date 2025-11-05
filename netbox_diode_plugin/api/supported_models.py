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
from rest_framework import serializers
from utilities.api import get_serializer_for_model as netbox_get_serializer_for_model

from netbox_diode_plugin.api.plugin_utils import legal_fields, legal_object_types

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
            fields = _get_model_fields(model)
            if not fields:
                continue

            extracted_models[object_type] = {
                "fields": fields,
                "model": model,
            }
        except Exception as e:
            logger.error(f"extract_supported_models: {model.__name__} error: {e}")

    finish_ts = time.time()
    elapsed_millis = (finish_ts - start_ts) * 1000
    logger.info(
        f"done extracting supported diode models in {elapsed_millis:.2f} milliseconds - extracted_models: {len(extracted_models)}"
    )

    return extracted_models

def _get_model_fields(model_class) -> dict:
    """Get the fields for the model."""
    legal = legal_fields(model_class)
    fields_info: dict[str, dict] = {}
    for field in model_class._meta.get_fields():
        field_name = field.name
        if field_name not in legal and field_name != 'id':
            continue

        field_info = {
            "type": field.get_internal_type(),
        }

        # Collect default values
        default_value = None
        if hasattr(field, "default"):
            default_value = (
                field.default if field.default not in (NOT_PROVIDED, dict) else None
            )
        field_info["default"] = default_value
        fields_info[field_name] = field_info

    return fields_info

@lru_cache(maxsize=128)
def get_serializer_for_model(model, prefix=""):
    """Cached wrapper for NetBox's get_serializer_for_model function."""
    return netbox_get_serializer_for_model(model, prefix)
