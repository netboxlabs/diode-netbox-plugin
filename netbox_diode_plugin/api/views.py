#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Views."""
import logging
import re

from django.apps import apps
from django.db import transaction
from rest_framework import status, views
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .applier import apply_changeset
from .authentication import DiodeOAuth2Authentication
from .common import (
    Change,
    ChangeSet,
    ChangeSetException,
    ChangeSetResult,
)
from .differ import enter_prechange_cache, exit_prechange_cache, generate_changeset
from .matcher import enter_request_obj_cache, exit_request_obj_cache
from .permissions import (
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

    def _get_branch_schema_id(self, request):
        """Get branch schema ID from request header or settings."""
        branch_schema_id = request.headers.get("X-NetBox-Branch")

        # If no branch specified in header, check for default branch in settings
        if not branch_schema_id and Branch is not None:
            try:
                from netbox_diode_plugin.models import Setting
                settings = Setting.objects.first()
                if settings and settings.branch:
                    branch_schema_id = settings.branch.schema_id
                    logger.debug(
                        f"Using default branch from settings: {settings.branch.name} ({branch_schema_id})"
                    )
            except Exception as e:
                logger.warning(f"Could not retrieve default branch from settings: {e}")

        return branch_schema_id

    def _add_branch_to_result(self, result, branch_schema_id):
        """Add branch information to the result if branch is available."""
        if branch_schema_id and Branch is not None:
            try:
                branch = Branch.objects.get(schema_id=branch_schema_id)
                result.change_set.branch = {"id": branch.schema_id, "name": branch.name}
            except Branch.DoesNotExist:
                sanitized_branch_id = branch_schema_id.replace('\n', '').replace('\r', '')
                logger.warning(f"Branch with ID {sanitized_branch_id} does not exist")

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
        branch_schema_id = self._get_branch_schema_id(request)
        self._add_branch_to_result(result, branch_schema_id)

        return Response(result.to_dict(), status=result.get_status_code())


class BulkPlanView(views.APIView):
    """BulkPlan view — batch generate diffs for multiple entities in a single request."""

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_READ)]

    def post(self, request, *args, **kwargs):
        """Generate diffs for a batch of entities."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            logger.exception("unexpected error in bulk-plan")
            return Response(
                {"errors": {"request": {"__all__": ["internal error"]}}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _post(self, request, *args, **kwargs):
        entities = request.data.get("entities")
        if not isinstance(entities, list) or len(entities) == 0:
            return Response(
                {"errors": {"request": {"entities": ["a non-empty list of entities is required"]}}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        branch_schema_id = self._get_branch_schema_id(request)

        obj_token = enter_request_obj_cache()
        prechange_token = enter_prechange_cache()
        try:
            results = []
            for entry in entities:
                entity_id = entry.get("id")
                result = self._process_entity(entry, branch_schema_id)
                result["id"] = entity_id
                results.append(result)
        finally:
            exit_prechange_cache(prechange_token)
            exit_request_obj_cache(obj_token)

        return Response({"results": results})

    def _get_branch_schema_id(self, request):
        """Get branch schema ID from request header or settings."""
        branch_schema_id = request.headers.get("X-NetBox-Branch")

        if not branch_schema_id and Branch is not None:
            try:
                from netbox_diode_plugin.models import Setting
                settings = Setting.objects.first()
                if settings and settings.branch:
                    branch_schema_id = settings.branch.schema_id
            except Exception as e:
                logger.warning(f"Could not retrieve default branch from settings: {e}")

        return branch_schema_id

    def _add_branch_to_result(self, result, branch_schema_id):
        """Add branch information to the result if branch is available."""
        if branch_schema_id and Branch is not None:
            try:
                branch = Branch.objects.get(schema_id=branch_schema_id)
                result.change_set.branch = {"id": branch.schema_id, "name": branch.name}
            except Branch.DoesNotExist:
                pass

    def _process_entity(self, entry, branch_schema_id):
        """Process a single entity and return its result dict."""
        entity = entry.get("entity")
        object_type = entry.get("object_type")

        if not entity:
            return {"change_set": None, "errors": {"request": {"entity": ["entity is required"]}}}
        if not object_type:
            return {"change_set": None, "errors": {"request": {"object_type": ["object_type is required"]}}}

        try:
            app_label, model_name = object_type.split(".")
        except ValueError:
            return {"change_set": None, "errors": {"request": {"object_type": [f"invalid format: {object_type}"]}}}

        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            return {"change_set": None, "errors": {"request": {"object_type": [f"{object_type} is not supported in this version."]}}}

        original_entity_data = None
        for entity_key in get_valid_entity_keys(model_class.__name__):
            original_entity_data = entity.get(entity_key)
            if original_entity_data:
                break

        if original_entity_data is None:
            return {"change_set": None, "errors": {"entity": {entity_key: [f"No data found in expected entity key, got: {list(entity.keys())}"]}}}

        try:
            result = generate_changeset(original_entity_data, object_type)
            self._add_branch_to_result(result, branch_schema_id)
            return result.to_dict()
        except ChangeSetException as e:
            return ChangeSetResult(errors=e.errors).to_dict()
        except Exception:
            logger.exception("unexpected error in bulk-plan for entity %s", entry.get("id"))
            return {"change_set": None, "errors": {"request": {"__all__": ["internal error processing entity"]}}}


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


class GetDefaultBranchView(views.APIView):
    """GetDefaultBranch view."""

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_READ)]

    def get(self, request, *args, **kwargs):
        """Get default branch from settings."""
        branch_data = None

        # Check for default branch in settings
        if Branch is not None:
            try:
                from netbox_diode_plugin.models import Setting
                settings = Setting.objects.first()
                if settings and settings.branch:
                    branch_data = {
                        "id": settings.branch.schema_id,
                        "name": settings.branch.name
                    }
                    logger.debug(
                        f"Default branch from settings: {settings.branch.name} ({settings.branch.schema_id})"
                    )
            except Exception as e:
                logger.warning(f"Could not retrieve default branch from settings: {e}")

        return Response({"branch": branch_data})
