#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Object matching utilities."""

import logging
from dataclasses import dataclass
from functools import cache, lru_cache

import netaddr
from django.contrib.contenttypes.fields import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import F, Value
from django.db.models.fields import SlugField
from django.db.models.lookups import Exact
from django.db.models.query_utils import Q
from extras.models.customfields import CustomField

from .common import UnresolvedReference
from .compat import in_version_range
from .plugin_utils import content_type_id, get_object_type, get_object_type_model

logger = logging.getLogger(__name__)

#
# these matchers are not driven by netbox unique constraints,
# but are logical criteria that may be used to match objects.
# These should represent the likely intent of a user when
# matching existing objects.
#
_LOGICAL_MATCHERS = {
    "dcim.macaddress": lambda: [
        ObjectMatchCriteria(
            fields=("mac_address", "assigned_object_type", "assigned_object_id"),
            name="logical_mac_address_within_parent",
            model_class=get_object_type_model("dcim.macaddress"),
            condition=Q(assigned_object_id__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("mac_address", "assigned_object_type", "assigned_object_id"),
            name="logical_mac_address_within_parent",
            model_class=get_object_type_model("dcim.macaddress"),
            condition=Q(assigned_object_id__isnull=True),
        ),
    ],
    "ipam.aggregate": lambda: [
        ObjectMatchCriteria(
            fields=("prefix",),
            name="logical_aggregate_prefix_no_rir",
            model_class=get_object_type_model("ipam.aggregate"),
            condition=Q(rir__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("prefix", "rir"),
            name="logical_aggregate_prefix_within_rir",
            model_class=get_object_type_model("ipam.aggregate"),
            condition=Q(rir__isnull=False),
        ),
    ],
    "ipam.ipaddress": lambda: [
        GlobalIPNetworkIPMatcher(
            ip_fields=("address",),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.ipaddress"),
            name="logical_ip_address_global_no_vrf",
        ),
        VRFIPNetworkIPMatcher(
            ip_fields=("address",),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.ipaddress"),
            name="logical_ip_address_within_vrf",
        ),
    ],
    "ipam.iprange": lambda: [
        GlobalIPNetworkIPMatcher(
            ip_fields=("start_address", "end_address"),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.iprange"),
            name="logical_ip_range_start_end_global_no_vrf",
        ),
        VRFIPNetworkIPMatcher(
            ip_fields=("start_address", "end_address"),
            vrf_field="vrf",
            model_class=get_object_type_model("ipam.iprange"),
            name="logical_ip_range_start_end_within_vrf",
        ),
    ],
    "ipam.prefix": lambda: [
         ObjectMatchCriteria(
            fields=("prefix",),
            name="logical_prefix_global_no_vrf",
            model_class=get_object_type_model("ipam.prefix"),
            condition=Q(vrf__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("prefix", "vrf"),
            name="logical_prefix_within_vrf",
            model_class=get_object_type_model("ipam.prefix"),
            condition=Q(vrf__isnull=False),
        ),
    ],
    "virtualization.cluster": lambda: [
        ObjectMatchCriteria(
            fields=("name", "scope_type", "scope_id"),
            name="logical_cluster_within_scope",
            model_class=get_object_type_model("virtualization.cluster"),
            condition=Q(scope_type__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_cluster_with_no_scope_or_group",
            model_class=get_object_type_model("virtualization.cluster"),
            condition=Q(scope_type__isnull=True, group__isnull=True),
        ),
    ],
    "ipam.vlan": lambda: [
        ObjectMatchCriteria(
            fields=("vid",),
            name="logical_vlan_vid_no_group_or_svlan_or_site",
            model_class=get_object_type_model("ipam.vlan"),
            condition=Q(group__isnull=True, qinq_svlan__isnull=True, site__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("vid", "site"),
            name="logical_vlan_in_site",
            model_class=get_object_type_model("ipam.vlan"),
            condition=Q(group__isnull=True, qinq_svlan__isnull=True, site__isnull=False),
        ),
    ],
    "ipam.vlangroup": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_vlan_group_name_no_scope",
            model_class=get_object_type_model("ipam.vlangroup"),
            condition=Q(scope_type__isnull=True),
        ),
    ],
    "wireless.wirelesslan": lambda: [
        ObjectMatchCriteria(
            fields=("ssid",),
            name="logical_wireless_lan_ssid_no_group_or_vlan",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(group__isnull=True, vlan__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("ssid", "group"),
            name="logical_wireless_lan_ssid_in_group",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(group__isnull=False),
        ),
        ObjectMatchCriteria(
            fields=("ssid", "vlan"),
            name="logical_wireless_lan_ssid_in_vlan",
            model_class=get_object_type_model("wireless.wirelesslan"),
            condition=Q(vlan__isnull=False),
        ),
    ],
    "virtualization.virtualmachine": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_virtual_machine_name_no_cluster",
            model_class=get_object_type_model("virtualization.virtualmachine"),
            condition=Q(cluster__isnull=True),
        ),
    ],
    "ipam.service": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_service_name_no_device_or_vm",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(device__isnull=True, virtual_machine__isnull=True),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_service_name_on_device",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(device__isnull=False),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "virtual_machine"),
            name="logical_service_name_on_vm",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(virtual_machine__isnull=False),
            max_version="4.2.99",
        ),
        ObjectMatchCriteria(
            fields=("name", "parent_object_type", "parent_object_id"),
            name="logical_service_name_on_parent",
            model_class=get_object_type_model("ipam.service"),
            condition=Q(parent_object_type__isnull=False),
            min_version="4.3.0"
        ),
    ],
    "dcim.modulebay": lambda: [
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_module_bay_name_on_device",
            model_class=get_object_type_model("dcim.modulebay"),
        )
    ],
    "dcim.inventoryitem": lambda: [
        # TODO: this may be handleable by the existing constraints.
        # we ignore it due to null values for parent but could have
        # better coverage of this case perhaps.
        ObjectMatchCriteria(
            fields=("name", "device"),
            name="logical_inventory_item_name_on_device_no_parent",
            model_class=get_object_type_model("dcim.inventoryitem"),
            condition=Q(parent__isnull=True),
        )
    ],
    "ipam.fhrpgroup": lambda: [
        ObjectMatchCriteria(
            fields=("group_id",),
            name="logical_fhrp_group_id",
            model_class=get_object_type_model("ipam.fhrpgroup"),
        )
    ],
    "tenancy.contact": lambda: [
        ObjectMatchCriteria(
            # contacts are unconstrained in 4.3.0
            # in 4.2 they are constrained by unique name per group
            fields=("name", ),
            name="logical_contact_name",
            model_class=get_object_type_model("tenancy.contact"),
            min_version="4.3.0",
        )
    ],
    "dcim.devicerole": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_device_role_name_no_parent",
            model_class=get_object_type_model("dcim.devicerole"),
            condition=Q(parent__isnull=True),
            min_version="4.3.0",
        ),
        ObjectMatchCriteria(
            fields=("slug",),
            name="logical_device_role_slug_no_parent",
            model_class=get_object_type_model("dcim.devicerole"),
            condition=Q(parent__isnull=True),
            min_version="4.3.0",
        )
    ],
    "extras.journalentry": lambda: [
        ObjectMatchCriteria(
            fields=("assigned_object_id", "assigned_object_type", "comments"),
            name="logical_journal_entry_assigned_object_comments",
            model_class=get_object_type_model("extras.journalentry"),
        )
    ],
}

@dataclass
class ObjectMatchCriteria:
    """
    Defines criteria for identifying a specific object.

    This matcher expects a fully 'transformed' and resolved
    set of fields. ie field names are snake case and match
    the model fields and any references to another object
    specify a specific id in the appropriate field name.
    eg device_id=123 etc and for any generic references,
    both the type and id should be specified, eg:
    scope_type="dcim.site" and scope_id=123
    """

    fields: tuple[str] | None = None
    expressions: tuple | None = None
    condition: Q | None = None
    model_class: type[models.Model] | None = None
    name: str | None = None

    min_version: str | None = None
    max_version: str | None = None

    def __hash__(self):
        """Hash the object match criteria."""
        return hash((self.fields, self.expressions, self.condition, self.model_class.__name__, self.name))

    def has_required_fields(self, data) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self._get_refs())

    @cache
    def _get_refs(self) -> set[str]:
        """Returns a set of all field names referenced by the constraint."""
        refs = set()
        if self.fields:
            refs.update(self.fields)
        elif self.expressions:
            for expr in self.expressions:
                refs |= _get_refs(expr)
        return frozenset(refs)

    @cache
    def _get_insensitive_refs(self) -> set[str]:
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
        sorted_fields = sorted(self._get_refs())
        insensitive = self._get_insensitive_refs()
        values = []
        for field in sorted_fields:
            value = data[field]
            if isinstance(value, dict):
                logger.warning(f"unexpected value type for fingerprinting: {value}")
                return None
            if field in insensitive:
                value = value.lower()
            values.append(value)

        return hash((self.model_class.__name__, self.name, tuple(values)))

    def _check_condition(self, data) -> bool:
        return self._check_condition_1(data, self.condition)

    def _check_condition_1(self, data, condition) -> bool:
        if condition is None:
            return True
        if isinstance(condition, tuple):
            return self._check_simple_condition(data, condition)

        if hasattr(condition, "connector") and condition.connector == Q.AND:
            result = True
            for child in condition.children:
                if not self._check_condition_1(data, child):
                    result = False
                    break
            if condition.negated:
                return not result
            return result
        # TODO handle OR ?
        logger.warning(f"Unhandled condition {condition}")
        return False

    def _check_simple_condition(self, data, condition) -> bool:
        if condition is None:
            return True

        k, v = condition
        result = False
        if k.endswith("__isnull"):
            k = k[:-8]
            is_null = k not in data or data[k] is None
            result = is_null == v
        else:
            result = k in data and data[k] == v

        return result

    def build_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        if self.fields and len(self.fields) > 0:
            return self._build_fields_queryset(data)
        if self.expressions and len(self.expressions) > 0:
            return self._build_expressions_queryset(data)
        raise ValueError("No fields or expressions to build queryset from")

    def _build_fields_queryset(self, data) -> models.QuerySet: # noqa: C901
        """Builds a queryset for a simple set-of-fields constraint."""
        if not self._check_condition(data):
            return None

        data = self._prepare_data(data)
        lookup_kwargs = {}
        for field_name in self.fields:
            field = self.model_class._meta.get_field(field_name)
            if field_name not in data:
                return None  # cannot match, missing field data
            lookup_value = data.get(field_name)
            if isinstance(lookup_value, UnresolvedReference):
                return None  # cannot match, missing field data
            if isinstance(lookup_value, dict):
                return None  # cannot match, missing field data
            lookup_kwargs[field.name] = lookup_value

        qs = self.model_class.objects.filter(**lookup_kwargs)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

    def _build_expressions_queryset(self, data) -> models.QuerySet:
        """Builds a queryset for the constraint with the given data."""
        data = self._prepare_data(data)
        replacements = {
            F(field): Value(value) if isinstance(value, str | int | float | bool) else value
            for field, value in data.items()
        }

        filters = []
        for expr in self.expressions:
            if hasattr(expr, "get_expression_for_validation"):
                expr = expr.get_expression_for_validation()

            refs = [F(ref) for ref in _get_refs(expr)]
            for ref in refs:
                if ref not in replacements:
                    return None  # cannot match, missing field data
                if isinstance(replacements[ref], UnresolvedReference):
                    return None  # cannot match, missing field data

            rhs = expr.replace_expressions(replacements)
            condition = Exact(expr, rhs)
            filters.append(condition)

        qs = self.model_class.objects.filter(*filters)
        if self.condition:
            qs = qs.filter(self.condition)
        return qs

    def _prepare_data(self, data: dict) -> dict:
        prepared = {}
        for field_name, value in data.items():
            try:
                field = self.model_class._meta.get_field(field_name)
                # special handling for object type -> content type id
                if field.is_relation and hasattr(field, "related_model") and field.related_model == ContentType:
                    # Handle ManyToMany fields (list of object types) and ForeignKey fields (single object type)
                    if isinstance(value, list):
                        prepared[field_name] = [content_type_id(v) for v in value]
                    else:
                        prepared[field_name] = content_type_id(value)
                else:
                    prepared[field_name] = value

            except FieldDoesNotExist:
                continue
        return prepared



@dataclass
class CustomFieldMatcher:
    """A matcher for a unique custom field."""

    name: str
    custom_field: str
    model_class: type[models.Model]

    min_version: str | None = None
    max_version: str | None = None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        value = data.get("custom_fields", {}).get(self.custom_field)
        if value is None:
            return None

        return hash((self.model_class.__name__, self.name, value))

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        value = data.get("custom_fields", {}).get(self.custom_field)
        if value is None:
            return None

        return self.model_class.objects.filter(**{f'custom_field_data__{self.custom_field}': value})

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return self.custom_field in data.get("custom_fields", {})


@dataclass
class GlobalIPNetworkIPMatcher:
    """A matcher that ignores the mask."""

    ip_fields: tuple[str]
    vrf_field: str
    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def _check_condition(self, data: dict) -> bool:
        """Check the condition for the custom field."""
        return data.get(self.vrf_field, None) is None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        values = []
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            values.append(value)

        return hash((self.model_class.__name__, self.name, tuple(values)))

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self.ip_fields)

    def ip_value(self, data: dict, field: str) -> str|None:
        """Get the IP value from the data."""
        value = data.get(field)
        if value is None:
            return None
        return _ip_only(value)

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        filter = {
            f'{self.vrf_field}__isnull': True,
        }
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            filter[f'{field}__net_host'] = value

        return self.model_class.objects.filter(**filter)

@dataclass
class VRFIPNetworkIPMatcher:
    """Matches ip in a vrf, ignores mask."""

    ip_fields: tuple[str]
    vrf_field: str
    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def _check_condition(self, data: dict) -> bool:
        """Check the condition for the custom field."""
        return data.get(self.vrf_field, None) is not None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        values = []
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            values.append(value)

        vrf_id = data[self.vrf_field]

        return hash((self.model_class.__name__, self.name, tuple(values), vrf_id))

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return all(field in data for field in self.ip_fields) and self.vrf_field in data

    def ip_value(self, data: dict, field: str) -> str|None:
        """Get the IP value from the data."""
        value = data.get(field)
        if value is None:
            return None
        return _ip_only(value)

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        if not self._check_condition(data):
            return None

        filter = {}
        for field in self.ip_fields:
            value = self.ip_value(data, field)
            if value is None:
                return None
            filter[f'{field}__net_host'] = value

        vrf_id = data[self.vrf_field]
        if isinstance(vrf_id, UnresolvedReference):
            return None
        filter[f'{self.vrf_field}'] = vrf_id

        return self.model_class.objects.filter(**filter)


def _ip_only(value: str) -> str|None:
    try:
        ip = netaddr.IPNetwork(value)
        value = ip.ip
    except netaddr.core.AddrFormatError:
        return None

    return value

@dataclass
class AutoSlugMatcher:
    """A special matcher that tries to match on auto generated slugs."""

    name: str
    slug_field: str
    model_class: type[models.Model]

    min_version: str | None = None
    max_version: str | None = None

    def fingerprint(self, data: dict) -> str|None:
        """Fingerprint the custom field value."""
        if not self.has_required_fields(data):
            return None

        slug = data.get('_auto_slug', None)
        if slug is None:
            return None

        return hash((self.model_class.__name__, self.name, slug.value))

    def build_queryset(self, data: dict) -> models.QuerySet:
        """Build a queryset for the custom field."""
        if not self.has_required_fields(data):
            return None

        slug = data.get('_auto_slug', None)
        if slug is None:
            return None

        return self.model_class.objects.filter(**{f'{self.slug_field}': str(slug.value)})

    def has_required_fields(self, data: dict) -> bool:
        """Returns True if the data given contains a value for all fields referenced by the constraint."""
        return '_auto_slug' in data


def get_model_matchers(model_class) -> list:
    """Extract unique constraints from a Django model."""
    matchers = []
    matchers += _get_model_matchers(model_class)

    # TODO(ltucker): this should also be cacheable, but we need a signal to invalidate
    if hasattr(model_class, "get_custom_fields"):
        unique_custom_fields = CustomField.objects.get_for_model(model_class).filter(unique=True)
        if unique_custom_fields:
            for cf in unique_custom_fields:
                matchers.append(
                    CustomFieldMatcher(
                        model_class=model_class,
                        custom_field=cf.name,
                        name=f"unique_custom_field_{cf.name}",
                    )
                )
    matchers += _get_autoslug_matchers(model_class)
    return matchers

@lru_cache(maxsize=256)
def _get_autoslug_matchers(model_class) -> list:
    matchers = []
    for field in model_class._meta.fields:
        if isinstance(field, SlugField):
            matchers.append(
                AutoSlugMatcher(
                    model_class=model_class,
                    slug_field=field.name,
                    name=f"unique_autoslug_{field.name}",
                )
            )
            break
    return matchers

@lru_cache(maxsize=256)
def _get_model_matchers(model_class) -> list[ObjectMatchCriteria]:
    object_type = get_object_type(model_class)
    matchers = [
        x for x in _LOGICAL_MATCHERS.get(object_type, lambda: [])()
        if in_version_range(x.min_version, x.max_version)
    ]

    # collect single fields that are unique
    for field in model_class._meta.fields:
        if field.name == "id":
            # TODO(ltucker): more django-general detection of pk field?
            continue

        if field.unique:
            matchers.append(
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
            matchers.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    fields=tuple(constraint.fields),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        elif len(constraint.expressions) > 0:
            matchers.append(
                ObjectMatchCriteria(
                    model_class=model_class,
                    expressions=tuple(constraint.expressions),
                    condition=constraint.condition,
                    name=constraint.name,
                )
            )
        else:
            logger.debug(
                f"Constraint {constraint.name} on {model_class.__name__} had no fields or expressions (skipped)"
            )
            # (this shouldn't happen / enforced by django)
            continue

    return matchers


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

def _fingerprint_all(data: dict, object_type: str|None = None) -> str:
    """
    Returns a fingerprint of the data based on all fields.

    Data should be a (flattened) dictionary of field values.
    This ignores any fields that start with an underscore.
    """
    if data is None:
        return None

    try:
        values = ["object_type", object_type]
        for k, v in sorted(data.items()):
            if k.startswith("_"):
                continue
            values.append(k)
            if isinstance(v, list | tuple):
                values.extend(sorted(_as_tuples(v)))
            elif isinstance(v, dict):
                values.append(_fingerprint_all(v))
            else:
                values.append(v)

        return hash(tuple(values))
    except Exception as e:
        logger.error(f"Error fingerprinting data: {e}")
        raise

def _as_tuples(vs):
    if isinstance(vs, list):
        return tuple(_as_tuples(v) for v in vs)
    if isinstance(vs, dict):
        return tuple((k, _as_tuples(v)) for k, v in vs.items())
    return vs

def fingerprints(data: dict, object_type: str) -> list[str]:
    """
    Get fingerprints for a data structure.

    This returns all fingerprints for the given data that
    have required fields.
    """
    if data is None:
        return None

    model_class = get_object_type_model(object_type)
    # check any known match criteria
    fps = []
    for matcher in get_model_matchers(model_class):
        fp = matcher.fingerprint(data)
        if fp is not None:
            fps.append(fp)
    fp = _fingerprint_all(data, object_type)
    fps.append(fp)
    return fps

def find_existing_object(data: dict, object_type: str): # noqa: C901
    """
    Find an existing object that matches the given data.

    Uses all object match criteria to look for an existing
    object. Returns the first match found.

    Returns the object if found, otherwise None.
    """
    model_class = get_object_type_model(object_type)
    for matcher in get_model_matchers(model_class):
        if not matcher.has_required_fields(data):
            continue
        q = matcher.build_queryset(data)
        if q is None:
            continue
        existing = q.order_by('pk').first()
        if existing is not None:
            return existing
    return None
