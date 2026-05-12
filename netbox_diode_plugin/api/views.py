#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Views."""
import logging
import re
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction
from django.db.utils import OperationalError
from rest_framework import status, views
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .applier import apply_changeset
from .authentication import DiodeOAuth2Authentication
from .common import (
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


def _sanitize_for_log(value, max_len=200):
    """Strip newlines/CR and bound length to make user-controlled values safe for logs."""
    s = str(value) if value is not None else ""
    return s.replace("\n", "").replace("\r", "")[:max_len]


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


def _apply_one_changeset(change_set: ChangeSet, request) -> ChangeSetResult:
    """Apply one changeset, returning a ChangeSetResult on success or on ChangeSetException."""
    try:
        with transaction.atomic():
            return apply_changeset(change_set, request)
    except ChangeSetException as e:
        logger.error(f"Error applying change set: {e}")
        return ChangeSetResult(id=change_set.id, errors=e.errors)


def _get_branch_schema_id(request):
    """Return the branch schema ID from the X-NetBox-Branch header or plugin Setting fallback."""
    branch_schema_id = request.headers.get("X-NetBox-Branch")

    if not branch_schema_id and Branch is not None:
        try:
            from netbox_diode_plugin.models import Setting
            settings = Setting.objects.first()
            if settings and settings.branch:
                branch_schema_id = settings.branch.schema_id
                logger.debug(
                    "Using default branch from settings: %s (%s)",
                    settings.branch.name,
                    branch_schema_id,
                )
        except Exception as e:
            logger.warning("Could not retrieve default branch from settings: %s", e)

    return branch_schema_id


def _add_branch_to_result(result, branch_schema_id):
    """Attach branch info to a ChangeSetResult's change_set when a branch is set."""
    if not branch_schema_id or Branch is None or result.change_set is None:
        return
    try:
        branch = Branch.objects.get(schema_id=branch_schema_id)
        result.change_set.branch = {"id": branch.schema_id, "name": branch.name}
    except Branch.DoesNotExist:
        logger.warning(
            "Branch with ID %s does not exist",
            _sanitize_for_log(branch_schema_id),
        )


@dataclass
class _ExtractedEntity:
    """Result of validating and locating an entity's data within a bulk request entry."""

    entity_data: dict | None = None
    object_type: str | None = None
    error: dict | None = None


def _extract_entity_data(entry):
    """
    Validate a bulk-request entry and locate the inner entity dict by snake/camel key.

    Returns an `_ExtractedEntity` with either `entity_data` + `object_type` populated
    (success) or `error` populated with a plan-error dict (per-entity validation failure).
    """
    entity = entry.get("entity")
    object_type = entry.get("object_type")

    if not entity:
        return _ExtractedEntity(error={"request": {"entity": ["entity is required"]}})
    if not object_type:
        return _ExtractedEntity(error={"request": {"object_type": ["object_type is required"]}})

    try:
        app_label, model_name = object_type.split(".")
    except ValueError:
        return _ExtractedEntity(error={"request": {"object_type": [f"invalid format: {object_type}"]}})

    try:
        model_class = apps.get_model(app_label, model_name)
    except LookupError:
        return _ExtractedEntity(
            error={"request": {"object_type": [f"{object_type} is not supported in this version."]}}
        )

    last_key = None
    for entity_key in get_valid_entity_keys(model_class.__name__):
        last_key = entity_key
        data = entity.get(entity_key)
        if data:
            return _ExtractedEntity(entity_data=data, object_type=object_type)

    return _ExtractedEntity(
        error={"entity": {last_key: [
            f"No data found in expected entity key, got: {list(entity.keys())}"
        ]}}
    )


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
        branch_schema_id = _get_branch_schema_id(request)
        _add_branch_to_result(result, branch_schema_id)

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

        branch_schema_id = _get_branch_schema_id(request)

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
            return {
                "change_set": None,
                "errors": {"entity": {entity_key: [
                    f"No data found in expected entity key, got: {list(entity.keys())}"
                ]}},
            }

        try:
            result = generate_changeset(original_entity_data, object_type)
            _add_branch_to_result(result, branch_schema_id)
            return result.to_dict()
        except ChangeSetException as e:
            return ChangeSetResult(errors=e.errors).to_dict()
        except Exception:
            logger.exception(
                "unexpected error in bulk-plan for entity %s",
                _sanitize_for_log(entry.get("id")),
            )
            return {
                "change_set": None,
                "errors": {"request": {"__all__": ["internal error processing entity"]}},
            }


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
        change_set = ChangeSet.from_dict(request.data)
        result = _apply_one_changeset(change_set, request)
        return Response(result.to_dict(), status=result.get_status_code())


class BulkApplyView(views.APIView):
    """
    BulkApply view.

    Accepts ``{"change_sets": [<changeset>, ...]}`` and applies each changeset
    in its own ``transaction.atomic()`` block (matching the singular
    ``apply-change-set/`` endpoint). A failure in one changeset does not affect
    the others.

    Response shape: ``{"results": [<ChangeSetResult>, ...]}`` with one result
    per input changeset, preserving order. HTTP 200 if all succeeded,
    HTTP 207 (multi-status) if at least one changeset failed, HTTP 400 if the
    batch envelope itself was invalid.
    """

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_WRITE)]

    def post(self, request, *args, **kwargs):
        """Apply a batch of change sets."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            import traceback
            traceback.print_exc()
            raise

    def _post(self, request, *args, **kwargs):
        change_sets = request.data.get("change_sets")
        if change_sets is None:
            raise ValidationError({"change_sets": ["change_sets is required"]})
        if not isinstance(change_sets, list):
            raise ValidationError({"change_sets": ["change_sets must be a list"]})
        if len(change_sets) == 0:
            raise ValidationError({"change_sets": ["change_sets must not be empty"]})

        results = []
        for entry in change_sets:
            if not isinstance(entry, dict):
                results.append(
                    ChangeSetResult(
                        errors={"request": {"change_set": ["change_set must be an object"]}}
                    ).to_dict()
                )
                continue
            try:
                change_set = ChangeSet.from_dict(entry)
                result = _apply_one_changeset(change_set, request).to_dict()
            except Exception as e:
                logger.error(f"Error parsing batch entry: {e}")
                result = ChangeSetResult(
                    errors={"request": {"change_set": [f"invalid change_set: {e}"]}}
                ).to_dict()
            results.append(result)

        http_status = (
            status.HTTP_207_MULTI_STATUS
            if any(r.get("errors") for r in results)
            else status.HTTP_200_OK
        )
        return Response({"results": results}, status=http_status)


class BulkPlanApplyView(views.APIView):
    """
    BulkPlanApply view — combined plan+apply per entity for the auto-apply fast path.

    For each entity in the batch this view runs ``generate_changeset`` then, when
    a non-empty change_set is produced, applies it via ``_apply_one_changeset``.
    The returned change_set is always included in the response (when the plan
    phase succeeded) so the reconciler can persist it for audit/retry regardless
    of apply outcome. Plan failure short-circuits apply for that entity.

    Each entity's apply gets its own ``transaction.atomic()`` (inherited from
    ``_apply_one_changeset``); a failure in one entity does not affect the
    others. Manual-review flows continue to use ``/bulk-plan`` + ``/bulk-apply``.

    Request shape::

        {"entities": [{"id": ..., "object_type": "dcim.site", "entity": {...}}, ...]}

    Response shape::

        {"results": [{"id": ..., "change_set": {...} | null,
                      "errors": {"plan": {...} | null, "apply": {...} | null} | null}, ...]}

    HTTP 200 if every entity succeeded both phases, 207 multi-status if any
    entity hit a plan or apply error, 400 if the request envelope is invalid.
    """

    authentication_classes = [DiodeOAuth2Authentication]
    permission_classes = [IsAuthenticated, require_scopes(SCOPE_NETBOX_WRITE)]

    def post(self, request, *args, **kwargs):
        """Plan and apply a batch of entities."""
        try:
            return self._post(request, *args, **kwargs)
        except Exception:
            logger.exception("unexpected error in bulk-plan-apply")
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

        branch_schema_id = _get_branch_schema_id(request)

        # Share the object-lookup and prechange caches across all entities in
        # the batch. Both caches are positive-only and request-scoped (single
        # thread), so a hit reflects a row that genuinely exists; a row created
        # by an earlier entity's apply is picked up by a subsequent entity's
        # plan (cache miss → DB → found → cached). The cache extends across
        # plan and apply within this batch — _create_or_find_instance's
        # IntegrityError fallback still recovers correctly because the cache
        # cannot return a stale "not found".
        obj_token = enter_request_obj_cache()
        prechange_token = enter_prechange_cache()
        try:
            results = []
            for entry in entities:
                entity_id = entry.get("id")
                result = self._process_entity(entry, branch_schema_id, request)
                result["id"] = entity_id
                results.append(result)
        finally:
            exit_prechange_cache(prechange_token)
            exit_request_obj_cache(obj_token)

        http_status = (
            status.HTTP_207_MULTI_STATUS
            if any(self._has_error(r) for r in results)
            else status.HTTP_200_OK
        )
        return Response({"results": results}, status=http_status)

    @staticmethod
    def _has_error(result):
        errors = result.get("errors") or {}
        return bool(errors.get("plan") or errors.get("apply"))

    def _process_entity(self, entry, branch_schema_id, request):
        """Plan-then-apply one entity. Returns a dict with change_set + plan/apply errors."""
        extracted = _extract_entity_data(entry)
        if extracted.error is not None:
            return {"change_set": None, "errors": {"plan": extracted.error}}

        plan_result, plan_error = self._run_plan(
            extracted.entity_data,
            extracted.object_type,
            entry.get("id"),
            branch_schema_id,
        )
        if plan_error is not None:
            return {"change_set": None, "errors": {"plan": plan_error}}

        change_set_dict = plan_result.change_set.to_dict() if plan_result.change_set else None

        # If plan produced no changes, there's nothing to apply.
        if plan_result.change_set is None or not plan_result.change_set.changes:
            return {"change_set": change_set_dict, "errors": None}

        # Apply phase — no obj_cache. Each entity gets its own transaction
        # via _apply_one_changeset. Catch OperationalError (Postgres deadlock,
        # serialization failure, etc.) here so a single contended entity
        # fails alone with a per-entity error rather than bubbling out and
        # turning the whole batch response into a 500 (which the reconciler
        # client then retries 4x, amplifying NetBox CPU on every deadlock).
        try:
            apply_result = _apply_one_changeset(plan_result.change_set, request)
        except OperationalError as e:
            logger.warning(
                "apply phase hit DB error for entity %s: %s",
                _sanitize_for_log(entry.get("id")),
                e,
            )
            return {"change_set": change_set_dict, "errors": {"apply": {"__all__": [str(e)]}}}

        if apply_result.errors:
            return {"change_set": change_set_dict, "errors": {"apply": apply_result.errors}}

        return {"change_set": change_set_dict, "errors": None}

    @staticmethod
    def _run_plan(entity_data, object_type, entry_id, branch_schema_id):
        """Run the plan phase. Object + prechange caches are managed by _post."""
        try:
            result = generate_changeset(entity_data, object_type)
            _add_branch_to_result(result, branch_schema_id)
            return result, None
        except ChangeSetException as e:
            return None, e.errors
        except Exception:
            logger.exception(
                "plan phase failed for entity %s",
                _sanitize_for_log(entry_id),
            )
            return None, {"request": {"__all__": ["internal error"]}}


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
