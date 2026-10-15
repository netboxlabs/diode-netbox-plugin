#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Diode NetBox Plugin - cross-process invalidation of custom field caches.

A Django signal clears a process's custom field caches only when that
process saved the CustomField. These tests stand in for "another worker
process" by changing the row with QuerySet.update(), which fires no signal,
and by moving the shared version token by hand. sync() is then what brings
this process up to date.
"""

import uuid
from types import SimpleNamespace
from unittest import mock

from core.models import ObjectType
from dcim.models import Device, Site
from django.core.cache import cache as django_cache
from django.test import RequestFactory, TestCase
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomField
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import common, customfield_version, transformer
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.customfield_version import CACHE_KEY, CustomFieldVersionMiddleware, sync
from netbox_diode_plugin.api.matcher import _get_custom_field_matchers, get_model_matchers
from netbox_diode_plugin.plugin_config import get_diode_user

MATCHER = "unique_custom_field_cf_ver"


def _device_matcher_names():
    return [m.name for m in get_model_matchers(Device)]


def _unique_field(model=Device, name="cf_ver"):
    cf = CustomField.objects.create(name=name, type=CustomFieldTypeChoices.TYPE_TEXT, required=False, unique=True)
    cf.object_types.set([ObjectType.objects.get_for_model(model)])
    return cf


def _another_worker_sets_unique(cf, unique):
    """What a save in another worker looks like from here: a changed row and a moved token."""
    CustomField.objects.filter(pk=cf.pk).update(unique=unique)
    django_cache.set(CACHE_KEY, f"elsewhere-{uuid.uuid4().hex}", timeout=None)


class _VersionTestCase(TestCase):
    def setUp(self):
        """Start from an empty shared cache and empty process-level caches."""
        django_cache.clear()
        django_cache.set(CACHE_KEY, f"setup-{uuid.uuid4().hex}", timeout=None)
        sync()

    def tearDown(self):
        """Leave nothing behind in the shared cache."""
        django_cache.clear()


class UniqueToggleFromAnotherWorkerTests(_VersionTestCase):
    """The motivating case: "must be unique" changes in a worker that is not this one."""

    def test_the_change_is_invisible_until_sync(self):
        """Nothing tells this process about the toggle; sync() is what does."""
        cf = _unique_field()
        self.assertIn(MATCHER, _device_matcher_names())

        _another_worker_sets_unique(cf, unique=False)
        self.assertIn(MATCHER, _device_matcher_names())

        sync()
        self.assertNotIn(MATCHER, _device_matcher_names())

    def test_turning_the_flag_back_on_is_seen_the_same_way(self):
        """Both directions of the toggle travel through the token."""
        cf = _unique_field()
        _another_worker_sets_unique(cf, unique=False)
        sync()
        self.assertNotIn(MATCHER, _device_matcher_names())

        _another_worker_sets_unique(cf, unique=True)
        sync()
        self.assertIn(MATCHER, _device_matcher_names())

    def test_sync_leaves_caches_alone_while_the_token_stands(self):
        """An unchanged token costs one cache GET and no re-read."""
        _unique_field()
        _device_matcher_names()
        common._get_custom_fields_for_model(Device)
        transformer._get_custom_fields_for_model(Device)

        sync()
        self.assertEqual(_get_custom_field_matchers.cache_info().currsize, 1)
        self.assertEqual(common._get_custom_fields_for_model.cache_info().currsize, 1)
        self.assertEqual(transformer._get_custom_fields_for_model.cache_info().currsize, 1)

    def test_every_registered_cache_is_dropped_together(self):
        """Matcher, common and transformer caches all answer to the one token."""
        cf = _unique_field()
        _device_matcher_names()
        common._get_custom_fields_for_model(Device)
        transformer._get_custom_fields_for_model(Device)

        _another_worker_sets_unique(cf, unique=False)
        sync()
        self.assertEqual(_get_custom_field_matchers.cache_info().currsize, 0)
        self.assertEqual(common._get_custom_fields_for_model.cache_info().currsize, 0)
        self.assertEqual(transformer._get_custom_fields_for_model.cache_info().currsize, 0)


class PublishingTests(_VersionTestCase):
    """Every change that can move a model's definitions publishes a new token."""

    def _token(self):
        return django_cache.get(CACHE_KEY)

    def test_create_save_and_delete_publish(self):
        """Each CustomField write moves the token."""
        before = self._token()
        cf = CustomField.objects.create(name="cf_ver", type=CustomFieldTypeChoices.TYPE_TEXT, required=False)
        created = self._token()
        self.assertNotEqual(created, before)

        cf.unique = True
        cf.save()
        saved = self._token()
        self.assertNotEqual(saved, created)

        cf.delete()
        self.assertNotEqual(self._token(), saved)

    def test_joining_or_leaving_a_model_publishes(self):
        """object_types is an m2m; its changes fire no CustomField post_save."""
        cf = CustomField.objects.create(name="cf_ver", type=CustomFieldTypeChoices.TYPE_TEXT, required=False)
        before = self._token()

        cf.object_types.add(ObjectType.objects.get_for_model(Device))
        joined = self._token()
        self.assertNotEqual(joined, before)

        cf.object_types.clear()
        self.assertNotEqual(self._token(), joined)

    def test_the_saving_process_needs_no_sync(self):
        """The signal still clears the saving process at once, as before."""
        cf = _unique_field()
        self.assertIn(MATCHER, _device_matcher_names())

        cf.unique = False
        cf.save()
        self.assertNotIn(MATCHER, _device_matcher_names())


class CacheOutageTests(_VersionTestCase):
    """Cache trouble degrades to per-process behaviour; it fails neither a request nor a save."""

    def test_sync_survives_an_unreadable_cache(self):
        """A failed GET keeps the caches and logs."""
        _unique_field()
        _device_matcher_names()
        broken = mock.Mock()
        broken.get.side_effect = RuntimeError("redis down")
        with (
            mock.patch.object(customfield_version, "django_cache", broken),
            self.assertLogs("netbox.diode.customfield_version", level="WARNING"),
        ):
            sync()
        self.assertEqual(_get_custom_field_matchers.cache_info().currsize, 1)

    def test_a_save_survives_an_unwritable_cache_and_still_clears_locally(self):
        """A failed SET logs, and the saving process is still cleared by the signal."""
        cf = _unique_field()
        self.assertIn(MATCHER, _device_matcher_names())
        broken = mock.Mock()
        broken.set.side_effect = RuntimeError("redis down")
        with (
            mock.patch.object(customfield_version, "django_cache", broken),
            self.assertLogs("netbox.diode.customfield_version", level="WARNING"),
        ):
            cf.unique = False
            cf.save()
        self.assertNotIn(MATCHER, _device_matcher_names())


class BrokenClearerTests(_VersionTestCase):
    """A clearer that raises is logged and skipped; the rest still clear, and nothing fails."""

    def _clearers(self, *clearers):
        return mock.patch.object(customfield_version, "_clearers", list(clearers))

    def test_sync_runs_the_remaining_clearers_and_moves_on(self):
        """The other caches are cleared, and the token counts as seen, so the failure is not retried."""
        good = mock.Mock()
        broken = mock.Mock(side_effect=RuntimeError("bad clearer"))
        django_cache.set(CACHE_KEY, f"elsewhere-{uuid.uuid4().hex}", timeout=None)
        with (
            self._clearers(broken, good),
            self.assertLogs("netbox.diode.customfield_version", level="WARNING"),
        ):
            sync()
            sync()
        good.assert_called_once()

    def test_a_save_completes_and_runs_the_remaining_clearers(self):
        """The signal path is isolated the same way, so the CustomField write goes through."""
        good = mock.Mock()
        broken = mock.Mock(side_effect=RuntimeError("bad clearer"))
        with (
            self._clearers(broken, good),
            self.assertLogs("netbox.diode.customfield_version", level="WARNING"),
        ):
            cf = CustomField.objects.create(name="cf_ver", type=CustomFieldTypeChoices.TYPE_TEXT, required=False)
        self.assertTrue(CustomField.objects.filter(pk=cf.pk).exists())
        good.assert_called_once()


class MiddlewareTests(TestCase):
    """One sync per plugin API request, none for the rest of NetBox."""

    def _syncs_for(self, path):
        middleware = CustomFieldVersionMiddleware(lambda request: "response")
        with mock.patch.object(customfield_version, "sync") as synced:
            self.assertEqual(middleware(RequestFactory().post(path)), "response")
        return synced.call_count

    def test_plugin_api_requests_sync(self):
        """Diode endpoints sync before the view runs."""
        self.assertEqual(self._syncs_for("/netbox/api/plugins/diode/bulk-plan/"), 1)

    def test_other_requests_do_not(self):
        """UI and core API requests never read these caches, so they never pay the GET."""
        self.assertEqual(self._syncs_for("/netbox/dcim/devices/1/"), 0)


class BulkPlanAfterToggleTests(APITestCase):
    """
    End to end: the next plugin request after a toggle elsewhere plans against the new definition.

    Site A holds the unique custom field value. Payload B carries a different
    name and slug and the same value. With the flag on it is planned as an
    UPDATE of Site A, the field being identity. Once another worker turns the
    flag off, the same payload is planned as a CREATE.
    """

    def setUp(self):
        """Authenticate as the Diode user and seed Site A with the unique value."""
        self.url = "/netbox/api/plugins/diode/bulk-plan/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user)
        self.introspect_patcher.start()
        django_cache.clear()
        self.field = _unique_field(model=Site, name="ext_id")
        Site.objects.create(name="Site A", slug="site-a", custom_field_data={"ext_id": "EXT-1"})

    def tearDown(self):
        """Stop the auth patch and empty the shared cache."""
        self.introspect_patcher.stop()
        django_cache.clear()
        super().tearDown()

    def _plan_site_b(self):
        payload = {
            "entities": [
                {
                    "id": "b",
                    "object_type": "dcim.site",
                    "entity": {
                        "site": {
                            "name": "Site B",
                            "slug": "site-b",
                            "custom_fields": {"ext_id": {"text": "EXT-1"}},
                        }
                    },
                }
            ]
        }
        response = self.client.post(self.url, data=payload, format="json", **self.authorization_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.json()["results"][0]
        self.assertIsNone(result.get("errors"))
        changes = result["change_set"]["changes"]
        self.assertEqual(len(changes), 1)
        return changes[0]["change_type"]

    def test_the_next_request_sees_the_toggle(self):
        """UPDATE of Site A while unique, CREATE once another worker clears the flag."""
        self.assertEqual(self._plan_site_b(), "update")

        # The find_obj result cache (find_obj_cache_ttl) is a separate, self-expiring layer;
        # emptying the cache stands in for that expiry.
        django_cache.clear()
        _another_worker_sets_unique(self.field, unique=False)

        self.assertEqual(self._plan_site_b(), "create")
