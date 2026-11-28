#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests for the search-index bypass."""

import importlib

from dcim.models import Site
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from extras.models import CachedValue

from netbox_diode_plugin.api import search_index_bypass


def _plugins_config_with_bypass():
    plugins_config = dict(settings.PLUGINS_CONFIG)
    plugins_config["netbox_diode_plugin"] = {
        **plugins_config["netbox_diode_plugin"],
        "apply_bypass_search_indexing": True,
    }
    return plugins_config


class SearchIndexBypassTestCase(TestCase):
    """bypass_search_indexing() with apply_bypass_search_indexing enabled."""

    def setUp(self):
        """Reload the module with the setting on; restore the signal registry after."""
        # The module reads the setting and swaps the post_save receiver once, at import.
        receivers = list(post_save.receivers)

        def restore():
            post_save.receivers = receivers
            post_save.sender_receivers_cache.clear()
            importlib.reload(search_index_bypass)

        self.addCleanup(restore)
        with override_settings(PLUGINS_CONFIG=_plugins_config_with_bypass()):
            importlib.reload(search_index_bypass)

    def _create_site_and_check_indexed(self, name):
        # NetBox >= 4.7 indexes after commit; run the on_commit callbacks.
        with self.captureOnCommitCallbacks(execute=True):
            site = Site.objects.create(name=name, slug=name)
        return CachedValue.objects.filter(
            object_type=ContentType.objects.get_for_model(Site),
            object_id=site.pk,
        ).exists()

    def test_save_inside_bypass_is_not_indexed(self):
        """A save inside bypass_search_indexing() writes no search cache entry."""
        with search_index_bypass.bypass_search_indexing():
            indexed = self._create_site_and_check_indexed("bypassed-site")
        self.assertFalse(indexed)

    def test_save_outside_bypass_is_indexed(self):
        """A save outside bypass_search_indexing() is still indexed."""
        self.assertTrue(self._create_site_and_check_indexed("indexed-site"))
