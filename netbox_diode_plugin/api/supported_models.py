#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""NetBox Diode Data - API supported models."""

import importlib
import logging
import time
from functools import lru_cache
from typing import List, Type

from django.apps import apps
from django.db import models
from django.db.models import ManyToOneRel
from django.db.models.fields import NOT_PROVIDED
from rest_framework import serializers
from utilities.api import get_serializer_for_model as netbox_get_serializer_for_model

logger = logging.getLogger(__name__)

# Supported apps
SUPPORTED_APPS = [
    "circuits",
    "dcim",
    "extras",
    "ipam",
    "virtualization",
    "vpn",
    "wireless",
    "tenancy",
]

# Models that are not supported
EXCLUDED_MODELS = [
    "TaggedItem",
    "Subscription",
    "ScriptModule",
    "Dashboard",
    "Notification",
]


def extract_supported_models() -> dict[str, dict]:
    """Extract supported models from NetBox."""
    supported_models = discover_models(SUPPORTED_APPS)

    logger.debug(f"Supported models: {supported_models}")

    models_to_process = supported_models
    extracted_models: dict[str, dict] = {}

    start_ts = time.time()
    while models_to_process:
        model = models_to_process.pop()
        try:
            fields, related_models = get_model_fields(model)
            if not fields:
                continue

            prerequisites = get_prerequisites(model, fields)
            object_type = f"{model._meta.app_label}.{model._meta.model_name}"
            extracted_models[object_type] = {
                "fields": fields,
                "prerequisites": prerequisites,
                "model": model,
            }
            for related_model in related_models:
                related_object_type = f"{related_model._meta.app_label}.{related_model._meta.model_name}"
                if (
                    related_object_type not in extracted_models
                    and related_object_type not in models_to_process
                ):
                    models_to_process.append(related_model)
        except Exception as e:
            logger.error(f"extract_supported_models: {model.__name__} error: {e}")

    finish_ts = time.time()
    lapsed_millis = (finish_ts - start_ts) * 1000
    logger.info(
        f"done extracting supported models in {lapsed_millis:.2f} milliseconds - extracted_models: {len(extracted_models)}"
    )

    return extracted_models


def get_prerequisites(model_class, fields) -> List[dict[str, str]]:
    """Get the prerequisite models for the model."""
    prerequisites: List[dict[str, str]] = []
    prerequisite_models = getattr(model_class, "prerequisite_models", [])

    for prereq in prerequisite_models:
        prereq_model = apps.get_model(prereq)

        for field_name, field_info in fields.items():
            related_model = field_info.get("related_model")
            prerequisite_info = {
                "field_name": field_name,
                "prerequisite_model": prereq_model,
            }
            if (
                prerequisite_info not in prerequisites
                and related_model
                and related_model.get("model_class_name") == prereq_model.__name__
            ):
                prerequisites.append(prerequisite_info)
                break

    return prerequisites


@lru_cache(maxsize=128)
def get_model_fields(model_class) -> tuple[dict, list]:
    """Get the fields for the model ordered as they are in the serializer."""
    related_models_to_process = []

    # Skip unsupported apps and excluded models
    if (
        model_class._meta.app_label not in SUPPORTED_APPS
        or model_class.__name__ in EXCLUDED_MODELS
    ):
        return {}, []

    try:
        # Get serializer fields to maintain order
        serializer_class = get_serializer_for_model(model_class)
        serializer_fields = serializer_class().get_fields()
        serializer_fields_names = list(serializer_fields.keys())
    except Exception as e:
        logger.error(f"Error getting serializer fields for model {model_class}: {e}")
        return {}, []

    # Get all model fields
    model_fields = {
        field.name: field
        for field in model_class._meta.get_fields()
        if field.__class__.__name__ not in ["CounterCacheField", "GenericRelation"]
    }

    # Reorder fields to match serializer order
    ordered_fields = {
        field_name: model_fields[field_name]
        for field_name in serializer_fields_names
        if field_name in model_fields
    }

    # Add remaining fields
    ordered_fields.update(
        {
            field_name: field
            for field_name, field in model_fields.items()
            if field_name not in ordered_fields
        }
    )

    fields_info = {}

    for field_name, field in ordered_fields.items():
        field_info = {
            "type": field.get_internal_type(),
            "required": not field.null and not field.blank,
            "is_many_to_one_rel": isinstance(field, ManyToOneRel),
            "is_numeric": field.get_internal_type()
            in [
                "IntegerField",
                "FloatField",
                "DecimalField",
                "PositiveIntegerField",
                "PositiveSmallIntegerField",
                "SmallIntegerField",
                "BigIntegerField",
            ],
        }

        # Handle default values
        default_value = None
        if hasattr(field, "default"):
            default_value = (
                field.default if field.default not in (NOT_PROVIDED, dict) else None
            )
        field_info["default"] = default_value

        # Handle related fields
        if field.is_relation:
            related_model = field.related_model
            if related_model:
                related_model_key = (
                    f"{related_model._meta.app_label}.{related_model._meta.model_name}"
                )
                related_model_info = {
                    "app_label": related_model._meta.app_label,
                    "model_name": related_model._meta.model_name,
                    "model_class_name": related_model.__name__,
                    "object_type": related_model_key,
                    "filters": get_field_filters(model_class, field_name),
                }
                field_info["related_model"] = related_model_info
                if (
                    related_model.__name__ not in EXCLUDED_MODELS
                    and related_model not in related_models_to_process
                ):
                    related_models_to_process.append(related_model)

        fields_info[field_name] = field_info

    return fields_info, related_models_to_process


@lru_cache(maxsize=128)
def get_field_filters(model_class, field_name):
    """Get filters for a field."""
    if hasattr(model_class, "_netbox_private"):
        return None

    try:
        filterset_name = f"{model_class.__name__}FilterSet"
        filterset_module = importlib.import_module(
            f"{model_class._meta.app_label}.filtersets"
        )
        filterset_class = getattr(filterset_module, filterset_name)

        _filters = set()
        field_filters = []
        for filter_name, filter_instance in filterset_class.get_filters().items():
            filter_by = getattr(filter_instance, "field_name", None)
            filter_field_extra = getattr(filter_instance, "extra", None)

            if not filter_name.startswith(field_name) or filter_by.endswith("_id"):
                continue

            if filter_by and filter_by not in _filters:
                _filters.add(filter_by)
                field_filters.append(
                    {
                        "filter_by": filter_by,
                        "filter_to_field_name": (
                            filter_field_extra.get("to_field_name", None)
                            if filter_field_extra
                            else None
                        ),
                    }
                )
        return list(field_filters) if field_filters else None
    except Exception as e:
        logger.error(
            f"Error getting field filters for model {model_class.__name__} and field {field_name}: {e}"
        )
        return None


@lru_cache(maxsize=128)
def get_serializer_for_model(model, prefix=""):
    """Cached wrapper for NetBox's get_serializer_for_model function."""
    return netbox_get_serializer_for_model(model, prefix)


def discover_models(root_packages: List[str]) -> list[Type[models.Model]]:
    """Discovers all model classes in specified root packages."""
    discovered_models = []

    # Look through all modules that might contain serializers
    module_names = [
        "api.serializers",
    ]

    for root_package in root_packages:
        logger.debug(f"Searching in root package: {root_package}")

        for module_name in module_names:
            full_module_path = f"{root_package}.{module_name}"
            try:
                module = __import__(full_module_path, fromlist=["*"])
            except ImportError:
                logger.error(f"Could not import {full_module_path}")
                continue

            # Find all serializer classes in the module
            for serializer_name in dir(module):
                serializer = getattr(module, serializer_name)
                if (
                    isinstance(serializer, type)
                    and issubclass(serializer, serializers.Serializer)
                    and serializer != serializers.Serializer
                    and serializer != serializers.ModelSerializer
                    and hasattr(serializer, "Meta")
                    and hasattr(serializer.Meta, "model")
                ):
                    model = serializer.Meta.model
                    if model not in discovered_models:
                        discovered_models.append(model)
                    logger.debug(
                        f"Discovered model: {model.__module__}.{model.__name__}"
                    )

    return discovered_models
