#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Matcher Cache Tests."""

from unittest import mock

from dcim.models import Manufacturer, Site
from django.core.cache import cache as django_cache
from django.test import TestCase, override_settings

from netbox_diode_plugin.api.common import UnresolvedReference
from netbox_diode_plugin.api.matcher import (
    _FIND_OBJ_NOT_FOUND,
    _find_obj_cache_key,
    find_existing_object,
)


class FindObjCacheKeyTestCase(TestCase):
    """Tests for _find_obj_cache_key."""

    def test_simple_scalar_fields(self):
        """Cache key is generated for data with simple scalar fields."""
        data = {"name": "Cisco", "_object_type": "dcim.manufacturer", "_uuid": "abc"}
        key = _find_obj_cache_key(data, "dcim.manufacturer")
        self.assertIsNotNone(key)
        self.assertTrue(key.startswith("diode:fobj:"))

    def test_deterministic(self):
        """Same data produces same cache key."""
        data = {"name": "Cisco", "_object_type": "dcim.manufacturer"}
        key1 = _find_obj_cache_key(data, "dcim.manufacturer")
        key2 = _find_obj_cache_key(data, "dcim.manufacturer")
        self.assertEqual(key1, key2)

    def test_different_data_different_keys(self):
        """Different data produces different cache keys."""
        key1 = _find_obj_cache_key({"name": "Cisco"}, "dcim.manufacturer")
        key2 = _find_obj_cache_key({"name": "Juniper"}, "dcim.manufacturer")
        self.assertNotEqual(key1, key2)

    def test_different_object_type_different_keys(self):
        """Same data with different object types produces different cache keys."""
        data = {"name": "test"}
        key1 = _find_obj_cache_key(data, "dcim.manufacturer")
        key2 = _find_obj_cache_key(data, "dcim.site")
        self.assertNotEqual(key1, key2)

    def test_skips_underscore_prefixed_fields(self):
        """Fields starting with _ are excluded from cache key."""
        data1 = {"name": "Cisco", "_object_type": "dcim.manufacturer", "_uuid": "abc"}
        data2 = {"name": "Cisco", "_object_type": "dcim.manufacturer", "_uuid": "xyz"}
        key1 = _find_obj_cache_key(data1, "dcim.manufacturer")
        key2 = _find_obj_cache_key(data2, "dcim.manufacturer")
        self.assertEqual(key1, key2)

    def test_skips_dicts_and_lists(self):
        """Dicts and lists are skipped, not rejected."""
        data_with_list = {"name": "DC-1", "tags": [1, 2, 3]}
        data_without_list = {"name": "DC-1"}
        key1 = _find_obj_cache_key(data_with_list, "dcim.site")
        key2 = _find_obj_cache_key(data_without_list, "dcim.site")
        self.assertIsNotNone(key1)
        self.assertEqual(key1, key2)

    def test_unresolved_reference_encoded(self):
        """UnresolvedReferences are encoded deterministically."""
        ref = UnresolvedReference("dcim.site", "uuid-123")
        data = {"name": "device-1", "site_id": ref}
        key = _find_obj_cache_key(data, "dcim.device")
        self.assertIsNotNone(key)

    def test_unresolved_reference_same_type_same_key(self):
        """Different UUIDs for same object type produce the same key."""
        data1 = {"name": "device-1", "site_id": UnresolvedReference("dcim.site", "uuid-1")}
        data2 = {"name": "device-1", "site_id": UnresolvedReference("dcim.site", "uuid-2")}
        key1 = _find_obj_cache_key(data1, "dcim.device")
        key2 = _find_obj_cache_key(data2, "dcim.device")
        self.assertEqual(key1, key2)

    def test_unresolved_reference_different_type_different_key(self):
        """Different unresolved object types produce different keys."""
        data1 = {"name": "x", "ref_id": UnresolvedReference("dcim.site", "uuid-1")}
        data2 = {"name": "x", "ref_id": UnresolvedReference("dcim.region", "uuid-1")}
        key1 = _find_obj_cache_key(data1, "dcim.device")
        key2 = _find_obj_cache_key(data2, "dcim.device")
        self.assertNotEqual(key1, key2)

    def test_empty_items_returns_none(self):
        """Returns None when no cacheable fields exist."""
        data = {"_uuid": "abc", "_object_type": "dcim.site"}
        key = _find_obj_cache_key(data, "dcim.site")
        self.assertIsNone(key)

    def test_only_complex_fields_returns_none(self):
        """Returns None when only dicts/lists remain after filtering."""
        data = {"tags": [1, 2], "metadata": {"a": 1}, "_uuid": "abc"}
        key = _find_obj_cache_key(data, "dcim.site")
        self.assertIsNone(key)


class FindExistingObjectCacheTestCase(TestCase):
    """Tests for find_existing_object caching behavior."""

    def setUp(self):
        """Set up test fixtures."""
        self.manufacturer = Manufacturer.objects.create(
            name="CacheTestManufacturer",
            slug="cache-test-manufacturer",
        )
        django_cache.clear()

    def tearDown(self):
        """Clean up cache after each test."""
        django_cache.clear()

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=5,
    )
    def test_cache_miss_populates_cache_on_found(self, _mock_ttl):
        """First lookup misses cache, finds object, and populates cache."""
        data = {"name": "CacheTestManufacturer", "_object_type": "dcim.manufacturer"}
        cache_key = _find_obj_cache_key(data, "dcim.manufacturer")

        # Cache should be empty
        self.assertIsNone(django_cache.get(cache_key))

        result = find_existing_object(data, "dcim.manufacturer")
        self.assertEqual(result.id, self.manufacturer.id)

        # Cache should now contain the PK
        self.assertEqual(django_cache.get(cache_key), self.manufacturer.id)

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=5,
    )
    def test_cache_miss_populates_cache_on_not_found(self, _mock_ttl):
        """First lookup misses cache, finds nothing, and caches not-found sentinel."""
        data = {"name": "NonExistent", "_object_type": "dcim.manufacturer"}
        cache_key = _find_obj_cache_key(data, "dcim.manufacturer")

        result = find_existing_object(data, "dcim.manufacturer")
        self.assertIsNone(result)

        # Cache should contain the not-found sentinel
        self.assertEqual(django_cache.get(cache_key), _FIND_OBJ_NOT_FOUND)

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=5,
    )
    def test_cache_hit_returns_found_object(self, _mock_ttl):
        """Second lookup hits cache and returns object via PK."""
        data = {"name": "CacheTestManufacturer", "_object_type": "dcim.manufacturer"}

        # First call populates cache
        result1 = find_existing_object(data, "dcim.manufacturer")
        self.assertEqual(result1.id, self.manufacturer.id)

        # Second call should hit cache — patch matchers to verify they're not called
        with mock.patch(
            "netbox_diode_plugin.api.matcher.get_model_matchers"
        ) as mock_matchers:
            result2 = find_existing_object(data, "dcim.manufacturer")
            self.assertEqual(result2.id, self.manufacturer.id)
            mock_matchers.assert_not_called()

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=5,
    )
    def test_cache_hit_returns_not_found(self, _mock_ttl):
        """Second lookup hits cache for not-found and returns None without querying."""
        data = {"name": "NonExistent", "_object_type": "dcim.manufacturer"}

        # First call populates cache with not-found
        result1 = find_existing_object(data, "dcim.manufacturer")
        self.assertIsNone(result1)

        # Second call should hit cache
        with mock.patch(
            "netbox_diode_plugin.api.matcher.get_model_matchers"
        ) as mock_matchers:
            result2 = find_existing_object(data, "dcim.manufacturer")
            self.assertIsNone(result2)
            mock_matchers.assert_not_called()

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=5,
    )
    def test_stale_cache_object_deleted(self, _mock_ttl):
        """Cached PK for a deleted object falls through to full matcher lookup."""
        data = {"name": "CacheTestManufacturer", "_object_type": "dcim.manufacturer"}

        # Populate cache
        find_existing_object(data, "dcim.manufacturer")
        cache_key = _find_obj_cache_key(data, "dcim.manufacturer")
        self.assertEqual(django_cache.get(cache_key), self.manufacturer.id)

        # Delete the object
        self.manufacturer.delete()

        # Should fall through to matchers, find nothing, and update cache
        result = find_existing_object(data, "dcim.manufacturer")
        self.assertIsNone(result)
        self.assertEqual(django_cache.get(cache_key), _FIND_OBJ_NOT_FOUND)

    @mock.patch(
        "netbox_diode_plugin.api.matcher._get_find_obj_cache_ttl",
        return_value=0,
    )
    def test_cache_disabled_when_ttl_zero(self, _mock_ttl):
        """Cache is bypassed when TTL is 0."""
        data = {"name": "CacheTestManufacturer", "_object_type": "dcim.manufacturer"}
        cache_key = _find_obj_cache_key(data, "dcim.manufacturer")

        result = find_existing_object(data, "dcim.manufacturer")
        self.assertEqual(result.id, self.manufacturer.id)

        # Cache should remain empty
        self.assertIsNone(django_cache.get(cache_key))
