#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API - Object matching utilities."""

import contextvars
import hashlib
import logging
import time
from dataclasses import dataclass
from functools import cache, lru_cache

import netaddr
from django.contrib.contenttypes.fields import ContentType
from django.core.cache import cache as django_cache
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import F, Value
from django.db.models.fields import SlugField
from django.db.models.lookups import Exact
from django.db.models.query_utils import Q
from django.db.models.signals import post_delete, post_save
from extras.models.customfields import CustomField
from netbox.plugins import get_plugin_config

from .common import UnresolvedReference
from .compat import in_version_range
from .plugin_utils import content_type_id, get_object_type, get_object_type_model
from .profile import get_profile_ctx

logger = logging.getLogger(__name__)

_request_obj_cache = contextvars.ContextVar("diode_request_obj_cache", default=None)


def enter_request_obj_cache():
    """
    Activate a request-scoped object lookup cache.

    Only positive (found-instance) results are stored. A miss is left
    uncached so that subsequent lookups against the same key correctly
    pick up a row another worker (or this request's apply phase) has
    inserted in the meantime. This is what makes the cache safe to share
    across plan and apply phases within the same request.
    """
    return _request_obj_cache.set({})


def exit_request_obj_cache(token):
    """Deactivate the request-scoped object lookup cache."""
    _request_obj_cache.reset(token)


def _get_find_obj_cache_ttl() -> int:
    return get_plugin_config("netbox_diode_plugin", "find_obj_cache_ttl")


def _get_active_branch_schema() -> str | None:
    try:
        from netbox_branching.contextvars import active_branch
    except ImportError:
        return None
    branch = active_branch.get()
    if branch is not None:
        return branch.schema_id
    return None


def _find_obj_rev_key(object_type: str, object_id: int) -> str:
    branch_schema = _get_active_branch_schema()
    if branch_schema:
        return f"diode:fobj:rev:{branch_schema}:{object_type}:{object_id}"
    return f"diode:fobj:rev:{object_type}:{object_id}"


def invalidate_find_obj_entry(object_type: str, object_id: int):
    """
    Delete a cached find_existing_object result by PK.

    Uses a reverse-index (PK → cache key) to find and delete the
    lookup cache entry. Call this after updating an existing object.
    """
    rev_key = _find_obj_rev_key(object_type, object_id)
    lookup_key = django_cache.get(rev_key)
    if lookup_key:
        django_cache.delete(lookup_key)
        django_cache.delete(rev_key)

#
# these matchers are not driven by netbox unique constraints,
# but are logical criteria that may be used to match objects.
# These should represent the likely intent of a user when
# matching existing objects.
#
# Object types whose logical match criteria are NOT backed by a DB
# unique constraint. For CREATE changes on these types the applier
# must call find_existing_object BEFORE serializer.save(); otherwise
# concurrent planners can each emit CREATE for the same logical row
# and both inserts succeed, producing duplicates. The standard
# IntegrityError fallback in _create_or_find_instance cannot catch
# this because save() does not fail.
#
# The specific gaps (see _LOGICAL_MATCHERS below for the criteria):
#   - dcim.macaddress: NetBox has no unique constraint on
#     (mac_address, assigned_object_type, assigned_object_id).
#   - dcim.modulebay: matched by (name, device); NetBox's parent-aware
#     constraint does not catch unscoped duplicates the matcher dedupes.
#   - ipam.prefix: NetBox has no unique constraint on prefix, nor on
#     (prefix, vrf) - Prefix.Meta carries only ordering and indexes.
#     Duplicate detection lives solely in Prefix.clean(), behind
#     ENFORCE_GLOBAL_UNIQUE or the VRF's enforce_unique flag, and the
#     applier saves through DRF serializers without calling full_clean(),
#     so that check never runs either.
#   - dcim.virtualchassis: matched by name when the payload has no master;
#     NetBox has no uniqueness on VC name at all.
#   - ipam.vlan: NetBox's (group, vid) constraint does not enforce
#     uniqueness when group is NULL.
#   - ipam.vlangroup: NetBox does not enforce uniqueness of name when
#     scope_type is NULL.
#   - ipam.vrf: NetBox enforces uniqueness on rd, not name; multiple
#     VRFs with rd=NULL and the same name are otherwise allowed.
#   - virtualization.cluster: NetBox does not enforce uniqueness of
#     name across scopes; matched by (name, scope_type, scope_id) or
#     by name alone when unscoped and unparented.
#   - virtualization.virtualmachine: NetBox does not enforce uniqueness
#     of name when cluster is NULL.
#   - wireless.wirelesslan: NetBox does not enforce ssid uniqueness.
#
# The list also covers a DB-constraint-backed type where find-first replaces
# a lossy, noisy IntegrityError recovery with a real adopt-update:
#   - dcim.module: plan-ahead topologies (plan-all-then-apply-all) emit a
#     second CREATE for an occupied module bay; find-first adopts it and
#     applies the payload instead of discarding it after a failed INSERT.
#
# This closes the common race (concurrent plan, sequential apply).
# It does not close TOCTOU under truly concurrent apply across
# replicas - that would require a DB unique constraint or a
# coordinating lock.
_REQUIRES_PRE_SAVE_MATCH = frozenset({
    "dcim.cable",
    "dcim.macaddress",
    "dcim.module",
    "dcim.modulebay",
    "dcim.virtualchassis",
    "ipam.prefix",
    "ipam.vlan",
    "ipam.vlangroup",
    "ipam.vrf",
    "virtualization.cluster",
    "virtualization.virtualmachine",
    "wireless.wirelesslan",
})


def requires_pre_save_match(object_type: str) -> bool:
    """Whether the applier must look up an existing row before CREATE."""
    return object_type in _REQUIRES_PRE_SAVE_MATCH


_LOGICAL_MATCHERS = {
    "dcim.cable": lambda: [
        CableTerminationSetMatcher(
            model_class=get_object_type_model("dcim.cable"),
            name="logical_cable_termination_set",
        )
    ],
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
    "ipam.vrf": lambda: [
        ObjectMatchCriteria(
            fields=("name",),
            name="logical_vrf_name_no_tenant",
            model_class=get_object_type_model("ipam.vrf"),
            condition=Q(rd__isnull=True, tenant__isnull=True),
        ),
        ObjectMatchCriteria(
            fields=("name", "tenant"),
            name="logical_vrf_name_within_tenant",
            model_class=get_object_type_model("ipam.vrf"),
            condition=Q(rd__isnull=True, tenant__isnull=False),
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
    "dcim.virtualchassis": lambda: [
        VirtualChassisNameMatcher(
            model_class=get_object_type_model("dcim.virtualchassis"),
            name="logical_vc_name_no_master",
        )
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
            if field in insensitive and value is not None:
                value = value.lower()
            values.append(value)

        if values and all(v is None for v in values):
            return None

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

        # A lookup whose referenced values are ALL null can only "match" via
        # IS NULL on every field, binding an arbitrary row (e.g. a null
        # asset_tag adopting an unrelated module). Partial nulls keep the
        # long-standing clear-FK dedupe: Django renders =None as IS NULL.
        if all(data.get(field_name) is None for field_name in self.fields):
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
        # Same all-None skip as the fields path, using the cached ref set;
        # raw data is checked so both guards read identical inputs.
        refs = self._get_refs()
        if refs and all(data.get(r) is None for r in refs):
            return None

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
                        prepared[field_name] = [
                            content_type_id(v) if v is not None else None for v in value
                        ]
                    else:
                        prepared[field_name] = content_type_id(value) if value is not None else None
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
class VirtualChassisNameMatcher:
    """
    Best-effort VirtualChassis matcher: by name, only when the payload has no master.

    VirtualChassis has no unique constraint besides master (names may
    legitimately duplicate), so this is a fallback: a payload that carries a
    master keeps resolving through the auto-derived unique_master matcher.
    The DB row's own master is deliberately NOT filtered on — a masterless
    payload must bind a mastered row. Ties resolve to the oldest row via the
    framework's order_by('pk').first().
    """

    model_class: type[models.Model]
    name: str

    min_version: str | None = None
    max_version: str | None = None

    def has_required_fields(self, data: dict) -> bool:
        """True when the payload carries a usable name and no master."""
        name = data.get("name")
        return isinstance(name, str) and bool(name) and data.get("master") is None

    def fingerprint(self, data: dict) -> int | None:
        """
        Name-keyed fingerprint, deliberately NOT gated on master.

        Within one transform batch the members' name-only VC node and the
        master-bearing VC node must dedupe-merge into a single create; gating
        this on master absence would leave two nodes and a split chassis.
        """
        name = data.get("name")
        if not isinstance(name, str) or not name:
            return None
        return hash((self.model_class.__name__, self.name, name))

    def build_queryset(self, data: dict) -> models.QuerySet | None:
        """Queryset over all VCs with this name, mastered or not."""
        if not self.has_required_fields(data):
            return None
        return self.model_class.objects.filter(name=data["name"])


@dataclass
class CableTerminationSetMatcher:
    """
    Match a Cable by its canonical set of terminations.

    Cable has no DB unique constraint; identity is the sorted set of
    (object_type, logical-id) termination tuples across BOTH ends, with
    A/B and within-end order insignificant. ObjectMatchCriteria is
    scalar-field-only and cannot express a related-row set match.
    """

    model_class: type[models.Model]
    name: str
    a_field: str = "a_terminations"
    b_field: str = "b_terminations"

    min_version: str | None = None
    max_version: str | None = None

    def has_required_fields(self, data: dict) -> bool:
        """Both termination ends present and non-empty."""
        a = data.get(self.a_field)
        b = data.get(self.b_field)
        return bool(a) and bool(b) and isinstance(a, list) and isinstance(b, list)

    def _reduce(self, term: dict):
        """
        Reduce one termination dict to a hashable (object_type, logical_id) tuple.

        logical_id is the resolved pk (int) when available; at transform time
        object_id is an UnresolvedReference -> reduce to ("__uuid__", uuid)
        (best-effort in-batch dedup only). Returns None if the item is not a
        dict / lacks the expected keys.
        """
        if not isinstance(term, dict):
            return None
        object_type = term.get("object_type")
        object_id = term.get("object_id")
        if object_type is None or object_id is None:
            return None
        if isinstance(object_id, UnresolvedReference):
            logical_id = ("__uuid__", object_id.uuid)
        elif isinstance(object_id, int):
            logical_id = object_id
        else:
            # stringified ref or unexpected type -- fold to a stable string
            logical_id = ("__str__", str(object_id))
        return (object_type, logical_id)

    def _reduced_set(self, data: dict):
        """Union of A+B reduced tuples, or None if any item is unreducible."""
        reduced = []
        for field in (self.a_field, self.b_field):
            for term in data.get(field, []):
                r = self._reduce(term)
                if r is None:
                    return None
                reduced.append(r)
        return reduced

    def fingerprint(self, data: dict) -> int | None:
        """
        Order-insensitive hash over the union of A+B reduced tuples.

        Best-effort in-batch dedup: may be computed over uuids when terminations
        are unresolved. Authoritative matching is build_queryset.
        """
        if not self.has_required_fields(data):
            return None
        reduced = self._reduced_set(data)
        if reduced is None:
            return None
        return hash(
            (self.model_class.__name__, self.name, tuple(sorted(reduced)))
        )

    def build_queryset(self, data: dict) -> models.QuerySet | None:
        """
        Authoritative exact-set match over real CableTermination rows.

        Annotate the termination count per Cable, require count == requested
        count, then require every requested (termination_type ct_id,
        termination_id pk) is present. Rejects subset/superset. Returns None if
        any object_id is still unresolved (cannot authoritatively match).
        """
        if not self.has_required_fields(data):
            return None

        pairs = []  # (content_type_id, pk)
        for field in (self.a_field, self.b_field):
            for term in data.get(field, []):
                if not isinstance(term, dict):
                    return None
                object_id = term.get("object_id")
                object_type = term.get("object_type")
                if not isinstance(object_id, int) or object_type is None:
                    return None  # unresolved -> cannot authoritatively match
                pairs.append((content_type_id(object_type), object_id))

        if not pairs:
            return None

        # A cable cannot terminate the same object twice (CableTermination has
        # a unique (termination_type, termination_id) constraint). A duplicate
        # pair would both inflate len(pairs) and be satisfiable by one shared
        # termination row (each filter is a separate join), letting an invalid
        # payload like A:[if1] B:[if1] false-match a larger cable containing
        # if1. Not authoritatively matchable -> let the serializer reject it.
        if len(set(pairs)) != len(pairs):
            return None

        qs = self.model_class.objects.annotate(
            _term_count=models.Count("terminations")
        ).filter(_term_count=len(pairs))
        for ct_id, pk in pairs:
            qs = qs.filter(
                terminations__termination_type_id=ct_id,
                terminations__termination_id=pk,
            )
        return qs.distinct()


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


@lru_cache(maxsize=256)
def _get_custom_field_matchers(model_class) -> tuple:
    """Get matchers for unique custom fields (cached)."""
    if not hasattr(model_class, "get_custom_fields"):
        return ()
    unique_custom_fields = CustomField.objects.get_for_model(model_class).filter(unique=True)
    if not unique_custom_fields:
        return ()
    return tuple(
        CustomFieldMatcher(
            model_class=model_class,
            custom_field=cf.name,
            name=f"unique_custom_field_{cf.name}",
        )
        for cf in unique_custom_fields
    )


def _on_custom_field_change(**kwargs):
    _get_custom_field_matchers.cache_clear()


post_save.connect(_on_custom_field_change, sender=CustomField)
post_delete.connect(_on_custom_field_change, sender=CustomField)


def get_model_matchers(model_class) -> list:
    """Extract unique constraints from a Django model."""
    matchers = []
    matchers += _get_model_matchers(model_class)
    matchers += _get_custom_field_matchers(model_class)
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

def _find_obj_cache_key(data: dict, object_type: str) -> str | None:
    """
    Build a deterministic cache key from entity lookup data.

    Includes only simple scalar fields. Entities whose identity depends
    solely on unresolved references (no scalar fields at all) are not
    cacheable.
    """
    if object_type == "dcim.cable":
        # Cable identity lives entirely in list fields skipped by the scalar-only
        # cache key; always run the authoritative build_queryset matcher loop.
        return None

    items = []
    for k, v in sorted(data.items()):
        if k.startswith("_"):
            continue
        if isinstance(v, UnresolvedReference):
            items.append((k, f"__unresolved__:{v.object_type}"))
        elif isinstance(v, (dict, list)):
            continue  # skip complex nested data, not used by matchers
        else:
            items.append((k, str(v)))

    if not items:
        return None

    branch_schema = _get_active_branch_schema()
    if branch_schema:
        raw = f"{branch_schema}:{object_type}:{items}"
    else:
        raw = f"{object_type}:{items}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return f"diode:fobj:{key_hash}"


def find_existing_object(data: dict, object_type: str): # noqa: C901
    """
    Find an existing object that matches the given data.

    Uses all object match criteria to look for an existing
    object. Returns the first match found.

    Returns the object if found, otherwise None.
    """
    ctx = get_profile_ctx()
    start = time.monotonic() if ctx else None
    queries_before = ctx.db_query_snapshot() if ctx else 0
    matchers_checked = 0
    result = None
    cache_hit = False

    model_class = get_object_type_model(object_type)
    cache_ttl = _get_find_obj_cache_ttl()
    cache_key = _find_obj_cache_key(data, object_type) if cache_ttl > 0 else None

    req_cache = _request_obj_cache.get(None)
    if req_cache is not None and cache_key is not None and cache_key in req_cache:
        cached = req_cache[cache_key]
        if ctx:
            ctx.record_timing("find_obj", (time.monotonic() - start) * 1000)
            ctx.increment("find_obj_found")
            ctx.increment("find_obj_req_cache_hit")
        return cached

    if cache_key:
        cached_id = django_cache.get(cache_key)
        if cached_id is not None:
            cache_hit = True
            result = model_class.objects.filter(pk=cached_id).first()
            if result is None:
                # Object deleted since cached — clean up and fall through
                cache_hit = False
                django_cache.delete(cache_key)
                django_cache.delete(_find_obj_rev_key(object_type, cached_id))

    if not cache_hit:
        for matcher in get_model_matchers(model_class):
            if not matcher.has_required_fields(data):
                continue
            q = matcher.build_queryset(data)
            if q is None:
                continue
            matchers_checked += 1
            existing = q.order_by('pk').first()
            if existing is not None:
                result = existing
                break

        if cache_key and result is not None:
            django_cache.set(cache_key, result.id, cache_ttl)
            django_cache.set(_find_obj_rev_key(object_type, result.id), cache_key, cache_ttl)

    if req_cache is not None and cache_key is not None and result is not None:
        req_cache[cache_key] = result

    if ctx:
        ctx.record_timing("find_obj", (time.monotonic() - start) * 1000)
        ctx.increment("find_obj_matchers_checked", matchers_checked)
        ctx.increment("find_obj_queries", ctx.db_query_snapshot() - queries_before)
        ctx.increment("find_obj_found" if result else "find_obj_not_found")
        if cache_key is not None:
            ctx.increment("find_obj_cache_hit" if cache_hit else "find_obj_cache_miss")
    return result
