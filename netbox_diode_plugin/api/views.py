#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API Views."""
import json
import logging
import re

from django.apps import apps
from django.db import transaction
from rest_framework import status, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from netbox_diode_plugin.api.applier import (
    ApplyChangeSetException,
    ApplyChangeSetResult,
    apply_changeset,
)
from netbox_diode_plugin.api.differ import Change, ChangeSet, ChangeType, generate_changeset
from netbox_diode_plugin.api.permissions import IsDiodeWriter

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


def get_entity_key(model_name):
    """Get the entity key for a model name."""
    s = re.sub(r'([A-Z0-9]{2,})([A-Z])([a-z])', r'\1_\2\3', model_name)
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s)
    s = re.sub(r'_+', '_', s.lower()) # snake
    s = ''.join([word.capitalize() for word in s.split("_")]) # upperCamelCase
    return s[0].lower() + s[1:] # lowerCamelCase


class GenerateDiffView(views.APIView):
    """GenerateDiff view."""

    permission_classes = [IsAuthenticated, IsDiodeWriter]

    def post(self, request, *args, **kwargs):
        """Generate diff for entity."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            import traceback
            traceback.print_exc()
            raise

    def _post(self, request, *args, **kwargs):
        entity = request.data.get("entity")
        object_type = request.data.get("object_type")

        if not entity:
            raise ValidationError("Entity is required")
        if not object_type:
            raise ValidationError("Object type is required")

        app_label, model_name = object_type.split(".")
        model_class = apps.get_model(app_label, model_name)

        # Convert model name to lowerCamelCase for entity lookup
        entity_key = get_entity_key(model_class.__name__)
        original_entity_data = entity.get(entity_key)

        if original_entity_data is None:
            raise ValidationError(
                f"No data found for {entity_key} in entity got: {entity.keys()}"
            )

        change_set = generate_changeset(original_entity_data, object_type)

        branch_id = request.headers.get("X-NetBox-Branch")

        # If branch ID is provided and branching plugin is installed, get branch name
        if branch_id and Branch is not None:
            try:
                branch = Branch.objects.get(id=branch_id)
                change_set.branch = {"id": branch.id, "name": branch.name}
            except Branch.DoesNotExist:
                logger.warning(f"Branch with ID {branch_id} does not exist")

        logger.info(f"change_set: {json.dumps(change_set.to_dict(), default=str)}")
        return Response(change_set.to_dict(), status=status.HTTP_200_OK)


class ApplyChangeSetView(views.APIView):
    """ApplyChangeSet view."""

    permission_classes = [IsAuthenticated, IsDiodeWriter]

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
        if 'changes' in data:
            changes = [
                Change(
                    change_type=change.get('change_type'),
                    object_type=change.get('object_type'),
                    object_id=change.get('object_id'),
                    ref_id=change.get('ref_id'),
                    data=change.get('data'),
                    before=change.get('before'),
                    new_refs=change.get('new_refs', []),
                ) for change in data['changes']
            ]
        change_set = ChangeSet(
            id=data.get('id'),
            changes=changes,
        )
        try:
            with transaction.atomic():
                result = apply_changeset(change_set)
        except ApplyChangeSetException as e:
            logger.error(f"Error applying change set: {e}")
            result = ApplyChangeSetResult(
                id=change_set.id,
                success=False,
                errors=e.errors,
            )
            return Response(result.to_dict(), status=status.HTTP_400_BAD_REQUEST)

        return Response(result.to_dict(), status=status.HTTP_200_OK)

    @staticmethod
    def _get_error_response(change_set_id, errors):
        """Get the error response."""
        return Response(
            {
                "change_set_id": change_set_id,
                "result": "failed",
                "errors": errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
