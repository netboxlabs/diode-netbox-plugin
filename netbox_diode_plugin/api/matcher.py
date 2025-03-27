#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - API - Object matching utilities."""

import logging
from functools import cache, lru_cache
from dataclasses import dataclass
from typing import List, Optional, Type

from core.models import ObjectType as NetBoxType
from django.db import models
from django.db.models import F
from django.db.models.lookups import Exact
from django.db.models.query_utils import Q

logger = logging.getLogger(__name__)


#
# TODO: add special cases for things that lack any unique constraints,
# but may have logical pre-existing matches ... eg an ip address in
# a certain context ... etc ? possibly mac address also ?
#

@dataclass
class ObjectMatchCriteria:
    """
    Defines criteria for identifying a specific object.

    This matcher expects a fully 'transformed' and resolved
    set of fields. ie field names are snake case and match
    the model fields and any references to another object
    specify a specific id in the appropriate field name.
    eg device_id=123 etc and for any generic references,
    both the type and idshould be specified, eg:
    scope_type="dcim.site" and scope_id=123
    """

    fields: tuple[str] | None = None
    expressions: tuple | None = None
    condition: Q | None = None
    model_class: Type[models.Model] | None = None
    name: str | None = None

    def __hash__(self):
        return hash((self.fields, self.expressions, self.condition, self.model_class.__name__, self.name))

    def has_required_fields(self, data) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self.get_refs())

    @cache
    def get_refs(self) -> set[str]:
        """Returns a set of all field names referenced by the constraint."""
        refs = set()
        if self.fields:
            refs.update(self.fields)
        elif self.expressions:
            for expr in self.expressions:
                refs |= _get_refs(expr)
        return frozenset(refs)

    @cache
    def get_insensitive_refs(self) -> set[str]:
        """
        Returns a set of all field names that should be compared in a case insensitive manner.

        best effort, doesn't handle things being nested in a complex way.
        """
        refs = set()
        if self.expressions:
            for expr in self.expressions:
                # TODO be more careful here
                if expr.__class__.__name__ == "Lower":
                    for source_expr in getattr(expr, "source_expressions", []):
                        if hasattr(source_expr, "name"):
                            refs.add(source_expr.name)
        return refs

    def fingerprint(self, data: dict) -> str|None:
        """
        Returns a fingerprint of the data based on these criteria.

        These criteria that can be used to determine if two
        data structs roughly match.

        This is a best effort based on the referenced fields
        and some interrogation of case sensitivity. The
        real criteria are potentially complex...
        """
        if not self.has_required_fields(data):
            return None

        if self.condition:
            if not self._check_condition(data):
                return None

        # sort the fields by name
        sorted_fields = sorted(self.get_refs())
        insensitive = self.get_insensitive_refs()
        values = []
        for field in sorted_fields:
            value = data[field]
            if field in insensitive:
                value = value.lower()
            values.append(value)
        # logger.debug(f"fingerprint {self}: {data} -> values: {tuple(values)}")

        return hash(tuple(values))

    def _check_condition(self, data) -> bool:
        if self.condition is None:
            return True
        # TODO: handle evaluating complex conditions,
        # there are only simple ones currently
        if self.condition.connector != Q.AND:
            logger.error(f"Unhandled condition {self.condition}")
            return False

        if len(self.condition.children) != 1:
            logger.error(f"Unhandled condition {self.condition}")
            return False

        if len(self.condition.children[0]) != 2:
            logger.error(f"Unhandled condition {self.condition}")
            return False

        k, v = self.condition.children[0]
        result = False
        if k.endswith("__isnull"):
            k = k[:-8]
            result = k not in data or data[k] is None
        else:
            result = k in data and data[k] == v

        if self.condition.negated:
            result = not result

        return result

    def build_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        if self.fields and len(self.fields) > 0:
            return self._build_fields_queryset(data)
        if self.expressions and len(self.expressions) > 0:
            return self._build_expressions_queryset(data)
        raise ValueError("No fields or expressions to build queryset from")

    def _build_fields_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for a simple set-of-fields constraint."""
        lookup_kwargs = {}
        for field_name in self.fields:
            field = self.model_class._meta.get_field(field_name)
            attribute = field.attname
            if attribute not in data:
                return None  # cannot match, missing field data
            lookup_value = data.get(field.attname)
            lookup_kwargs[field.name] = lookup_value

        # logger.error(f"      * query kwargs: {lookup_kwargs}")
        qs = self.model_class.objects.filter(**lookup_kwargs)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

    def _build_expressions_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        replacements = {
            F(field): value
            for field, value in data.items()
        }

        filters = []
        for expr in self.expressions:
            if hasattr(expr, "get_expression_for_validation"):
                expr = expr.get_expression_for_validation()

            refs = _get_refs(expr)
            for ref in refs:
                if ref not in replacements:
                    return None  # cannot match, missing field data

            rhs = expr.replace_expressions(replacements)
            condition = Exact(expr, rhs)
            filters.append(condition)

        qs = self.model_class.objects.filter(*filters)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

@lru_cache(maxsize=256)
def get_model_matchers(model_class) -> list[ObjectMatchCriteria]:
    """Extract unique constraints from a Django model."""
    constraints = []

    # collect single fields that are unique
    for field in model_class._meta.fields:
        if field.name == "id":
            # TODO(ltucker): more django-general detection of pk field?
            continue

        if field.unique:
            constraints.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    fields=(field.name,),
                    name=f"unique_{field.name}",
                )
            )

    # collect UniqueConstraint constraints
    for constraint in model_class._meta.constraints:
        if not _is_supported_constraint(constraint, model_class):
            continue
        if len(constraint.fields) > 0:
            constraints.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    fields=tuple(constraint.fields),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        elif len(constraint.expressions) > 0:
            constraints.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    expressions=tuple(constraint.expressions),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        else:
            logger.error(
                f"Constraint {constraint.name} on {model_class.__name__} had no fields or expressions (skipped)"
            )
            # (this shouldn't happen / enforced by django)
            continue

    return constraints

def _is_supported_constraint(constraint, model_class) -> bool:
    if not isinstance(constraint, models.UniqueConstraint):
        return False

    if len(constraint.opclasses) > 0:
        logger.warning(f"Constraint {constraint.name} on {model_class.__name__} had opclasses (skipped)")
        return False

    if constraint.nulls_distinct is not None and constraint.nulls_distinct is True:
        logger.warning(f"Constraint {constraint.name} on {model_class.__name__} had nulls_distinct (skipped)")
        return False

    for field_name in constraint.fields:
        field = model_class._meta.get_field(field_name)
        if field.generated:
            logger.warning(
                f"Constraint {constraint.name} on {model_class.__name__} had"
                f" generated field {field_name} (skipped)"
            )
            return False

    return True

def _get_refs(expr) -> set[str]:
    refs = set()
    if isinstance(expr, str):
        refs.add(expr)
    elif isinstance(expr, F):
        refs.add(expr.name)
    elif hasattr(expr, "get_source_expressions"):
        for subexpr in expr.get_source_expressions():
            refs |= _get_refs(subexpr)
    else:
        logger.warning(f"Unhandled expression type for _get_refs: {type(expr)}")
    return refs

def _fingerprint_all(data: dict) -> str:
    """
    Returns a fingerprint of the data based on all fields.

    Data should be a (flattened) dictionary of field values.
    This ignores any fields that start with an underscore.
    """
    if data is None:
        return None

    values = []
    for k, v in sorted(data.items()):
        if k.startswith("_"):
            continue
        values.append(k)
        if isinstance(v, (list, tuple)):
            values.extend(sorted(v))
        # TODO: handle dicts
        else:
            values.append(v)
    # logger.error(f"_fingerprint_all: {data} -> values: {tuple(values)}")

    return hash(tuple(values))

def fingerprint(data: dict, object_type: str) -> str:
    """
    Fingerprint a data structure.

    This uses the first matcher that has all
    required fields or else uses all fields.

    TODO: This means there are pathological? cases where
    the same object is being referenced but by
    different unique constraints in the same diff...
    this could lead to some unexpected behavior.
    """
    if data is None:
        return None

    model_class = get_object_type_model(object_type)
    # check any known match criteria
    for matcher in get_model_matchers(model_class):
        fp = matcher.fingerprint(data)
        if fp is not None:
            return fp
    # fall back to fingerprinting all the data
    return _fingerprint_all(data)

def find_existing_object(data: dict, object_type: str):
    """
    Find an existing object that matches the given data.

    Uses all object match criteria to look for an existing
    object. Returns the first match found.

    Returns the object if found, otherwise None.
    """
    logger.debug(f"resolving {data}")
    model_class = get_object_type_model(object_type)
    for matcher in get_model_matchers(model_class):
        if not matcher.has_required_fields(data):
            logger.debug(f"  * skipped matcher {matcher.name} (missing fields)")
            continue
        q = matcher.build_queryset(data)
        if q is None:
            logger.debug(f"  * skipped matcher {matcher.name} (no queryset)")
            continue
        try:
            logger.debug(f"  * trying query {q.query}")
            existing = q.get()
            logger.debug(f"      -> Found object {existing} via {matcher.name}")
            return existing
        except model_class.DoesNotExist:
            logger.debug(f"      -> No object found for matcher {matcher.name}")
            continue
        logger.debug("  * No matchers found an existing object")
    return None

@lru_cache(maxsize=256)
def get_object_type_model(object_type: str) -> Type[models.Model]:
    """Get the model class for a given object type."""
    app_label, model_name = object_type.split(".")
    object_content_type = NetBoxType.objects.get_by_natural_key(app_label, model_name)
    return object_content_type.model_class()

def merge_data(a: dict, b: dict) -> dict:
    """
    Merges two structures.

    If there are any conflicts, an error is raised.
    Ignores conflicts in fields that start with an underscore,
    preferring a's value.
    """
    if a is None or b is None:
        raise ValueError("Cannot merge None values")
    merged = a.copy()
    for k, v in b.items():
        if k.startswith("_"):
            continue
        if k in merged and merged[k] != v:
            raise ValueError(f"Conflict merging {a} and {b} on {k}: {merged[k]} and {v}")
        merged[k] = v
    return merged
