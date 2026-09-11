#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Rewrite producer values into the form NetBox stores.

NetBox canonicalizes some values on write: a MAC or WWN is parsed and stored in
its uppercase colon form, DRF strips leading and trailing whitespace from every
text field, and a few models rewrite a field in ``save()``. The plugin plans a
change by comparing the producer's value with the stored one, so any value
NetBox rewrites and the producer did not is planned as an UPDATE, applied,
rewritten by NetBox on save, and planned again on the next ingest, forever
(netboxlabs/diode#373: a lowercase MAC re-planned on every ingest).

This module rewrites the producer's value the way NetBox will, BEFORE matching,
fingerprinting and diffing, so every later stage sees the stored form. It only
performs lossless rewrites that NetBox itself performs; anything NetBox would
reject is passed through untouched so apply reports NetBox's own error.

How a rule is chosen is deliberately NetBox-driven: the EUI rules call the model
field's own ``to_python``, and the strip rule follows DRF's ``ModelSerializer``
field mapping, so the plugin never carries a private copy of NetBox's format
rules. Rewrites that live in a model's ``save()`` (e.g. ``IPAddress.dns_name``
is lowercased there) are deliberately NOT mirrored: there is no field to
delegate to, so a copy here would be a policy NetBox may change without the
plugin noticing.
"""

import logging
from collections.abc import Callable
from functools import lru_cache

from dcim.fields import MACAddressField, WWNField
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models

from .plugin_utils import get_object_type_model

logger = logging.getLogger("netbox.diode_data")

# Model field classes whose to_python() returns the canonical value. NetBox's
# serializers map these to DRF ModelField, whose to_internal_value is exactly
# that call, so this is what apply would do to the value anyway.
_TO_PYTHON_FIELD_CLASSES = (MACAddressField, WWNField)


def _via_to_python(field: models.Field) -> Callable:
    """Return a rule that stores what ``field.to_python`` would, or the raw value if it would not parse."""

    def rule(value):
        if not isinstance(value, str):
            return value
        try:
            return str(field.to_python(value))
        except ValidationError:
            # Leave it to the serializer at apply time: NetBox reports its own
            # "Invalid MAC address format" there, which is the message the
            # producer should see.
            return value

    return rule


def _strip(value):
    """DRF CharField(trim_whitespace=True) is the default NetBox never overrides."""
    return value.strip() if isinstance(value, str) else value


def _is_trimmed_text(field: models.Field) -> bool:
    """
    Whether DRF trims this model field on write.

    ModelSerializer maps CharField and TextField (and their subclasses: SlugField,
    URLField, EmailField, ColorField) to a trimming CharField, but a CharField
    with choices to ChoiceField, which does not trim. Mirror that split.
    """
    return isinstance(field, models.CharField | models.TextField) and not field.choices


@lru_cache(maxsize=256)
def _rules_for(object_type: str) -> dict[str, Callable]:
    """The per-field rewrite plan for an object type; empty when the type has no model."""
    try:
        model = get_object_type_model(object_type)
    except (ObjectDoesNotExist, ValueError, LookupError):
        return {}
    rules: dict[str, Callable] = {}
    for field in model._meta.get_fields():
        if getattr(field, "is_relation", False):
            continue
        if isinstance(field, _TO_PYTHON_FIELD_CLASSES):
            rules[field.name] = _via_to_python(field)
        elif _is_trimmed_text(field):
            rules[field.name] = _strip
    return rules


def canonicalize_entity(proto_json: dict, object_type: str) -> None:
    """
    Rewrite the scalar values of one entity node into NetBox's stored form, in place.

    Called once per node in the transformer, after the generated format
    transforms and the compat migrations and before anything reads the values.
    Nested payloads are not descended into: each becomes its own node and is
    canonicalized when that node is transformed. Only scalar strings are
    candidates; ``None``, numbers, references, lists and dicts pass through.
    """
    for name, rule in _rules_for(object_type).items():
        value = proto_json.get(name)
        if isinstance(value, str):
            proto_json[name] = rule(value)
