#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Views."""
import logging
import re

from django.apps import apps
from django.db import transaction
from rest_framework import views
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import (
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeSetResult,
)
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.permissions import (
    SCOPE_NETBOX_READ,
    SCOPE_NETBOX_WRITE,
    IsAuthenticated,
    require_scopes,
)

logger = logging.getLogger("netbox.diode_data")

# Try to import Branch model at module level
Branch = None
try:
    if apps.is_installed("netbox_branching"):
        from netbox_branching.models import Branch
except ImportError:
    logger.warning(
        "netbox_branching plugin is installed but models could not be imported"
    )


def get_valid_entity_keys(model_name):
    """
    Get the valid entity keys for a model name.

    This can be snake or lowerCamel case (both are valid for protoJSON)
    """
    s = re.sub(r"([A-Z0-9]{2,})([A-Z])([a-z])", r"\1_\2\3", model_name)
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    snake = re.sub(r"_+", "_", s.lower())  # snake
    upperCamel = "".join(
        [word.capitalize() for word in snake.split("_")]
    )  # upperCamelCase
    lowerCamel = upperCamel[0].lower() + upperCamel[1:]  # lowerCamelCase

    return (snake, lowerCamel)


class GenerateDiffView(views.APIView):
    """GenerateDiff view."""

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_READ)]

    def post(self, request, *args, **kwargs):
        """Generate diff for entity."""
        try:
            return self._post(request, *args, **kwargs)
        except ChangeSetException as e:
            result = ChangeSetResult(
                errors=e.errors,
            )
            return Response(result.to_dict(), status=result.get_status_code())
        except Exception:
            import traceback
            traceback.print_exc()
            raise

    def _post(self, request, *args, **kwargs):
        entity = request.data.get("entity")
        object_type = request.data.get("object_type")

        if not entity:
            raise ChangeSetException(
                "validation error",
                errors={
                    "request": {
                        "entity": ["entity is required"]
                    }
                }
            )
        if not object_type:
            raise ChangeSetException(
                "validation error",
                errors={
                    "request": {
                        "object_type": ["object_type is required"]
                    }
                }
            )

        app_label, model_name = object_type.split(".")
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            raise ChangeSetException(
                "validation error",
                errors={
                    "request": {
                        "object_type": [f"{object_type} is not supported in this version."]
                    }
                }
            )

        for entity_key in get_valid_entity_keys(model_class.__name__):
            original_entity_data = entity.get(entity_key)
            if original_entity_data:
                break

        if original_entity_data is None:
            raise ChangeSetException(
                "validation error",
                errors={
                    "entity": {
                        entity_key: [f"No data found in expected entity key, got: {entity.keys()}"]
                    }
                }
            )

        result = generate_changeset(original_entity_data, object_type)
        branch_schema_id = request.headers.get("X-NetBox-Branch")

        # If branch schema ID is provided and branching plugin is installed, get branch name
        if branch_schema_id and Branch is not None:
            try:
                branch = Branch.objects.get(schema_id=branch_schema_id)
                result.change_set.branch = {"id": branch.schema_id, "name": branch.name}
            except Branch.DoesNotExist:
                sanitized_branch_id = branch_schema_id.replace('\n', '').replace('\r', '')
                logger.warning(f"Branch with ID {sanitized_branch_id} does not exist")

        return Response(result.to_dict(), status=result.get_status_code())


class ApplyChangeSetView(views.APIView):
    """ApplyChangeSet view."""

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_WRITE)]

    def post(self, request, *args, **kwargs):
        """Apply change set for entity."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            import traceback

            traceback.print_exc()
            raise

    def _post(self, request, *args, **kwargs):
        data = request.data.copy()

        changes = []
        if "changes" in data:
            changes = [
                Change(
                    change_type=change.get("change_type"),
                    object_type=change.get("object_type"),
                    object_id=change.get("object_id"),
                    ref_id=change.get("ref_id"),
                    data=change.get("data"),
                    before=change.get("before"),
                    new_refs=change.get("new_refs", []),
                )
                for change in data["changes"]
            ]
        change_set = ChangeSet(
            id=data.get("id"),
            changes=changes,
        )
        try:
            with transaction.atomic():
                result = apply_changeset(change_set, request)
        except ChangeSetException as e:
            logger.error(f"Error applying change set: {e}")
            result = ChangeSetResult(
                id=change_set.id,
                errors=e.errors,
            )

        return Response(result.to_dict(), status=result.get_status_code())
