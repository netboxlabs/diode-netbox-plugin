#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - extras.configcontext ingest tests."""

import json
from types import SimpleNamespace
from unittest import mock

from dcim.models import DeviceRole, Site
from extras.models import ConfigContext, Tag
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user

CC_NAME = "Mist Site Settings"


def _applied(change_set):
    """(change_type, object_type) of every change that writes, i.e. all but NOOPs."""
    return [(c["change_type"], c["object_type"]) for c in change_set["changes"] if c["change_type"] != "noop"]


class ConfigContextIngestTestCase(APITestCase):
    """
    ConfigContext ingests through generate-diff -> apply-change-set and bulk-plan-apply.

    NetBox's ConfigContextSerializer takes ``tags`` as slugs (SlugRelatedField),
    unlike most models, whose serializers take tag pks. Every other M2M
    (sites, roles, ...) is a pk list on the wire.
    """

    def setUp(self):
        """Mock token introspection and seed the objects ingest must resolve."""
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.bulk_plan_apply_url = "/netbox/api/plugins/diode/bulk-plan-apply/"
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user
        )
        self.introspect_patcher.start()

        self.site_a = Site.objects.create(name="CC Site A", slug="cc-site-a")
        self.site_b = Site.objects.create(name="CC Site B", slug="cc-site-b")
        self.role = DeviceRole.objects.create(name="CC Role", slug="cc-role")
        self.tag = Tag.objects.create(name="CC Tag", slug="cc-tag")

    def tearDown(self):
        """Stop the introspection mock."""
        self.introspect_patcher.stop()
        super().tearDown()

    def _entity(self, sites=("CC Site A",), tags=("CC Tag",), data=None, **extra):
        entity = {
            "name": CC_NAME,
            "data": json.dumps(data if data is not None else {"mist": {"site_id": "abc", "vlans": [10, 20]}}),
            "sites": [{"name": name} for name in sites],
            "roles": [{"name": "CC Role"}],
            "tags": [{"name": name} for name in tags],
        }
        entity.update(extra)
        return entity

    def _diff(self, entity):
        response = self.client.post(
            self.diff_url,
            data={"timestamp": 1, "object_type": "extras.configcontext", "entity": {"config_context": entity}},
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        return response.json()["change_set"]

    def _apply(self, change_set):
        response = self.client.post(self.apply_url, data=change_set, format="json", **self.authorization_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        return response.json()

    def _ingest(self, entity):
        change_set = self._diff(entity)
        self._apply(change_set)
        return change_set

    def test_create_with_data_sites_roles_tags(self):
        """A new ConfigContext is created with its data, scope M2Ms and tags."""
        self._ingest(self._entity())

        cc = ConfigContext.objects.get(name=CC_NAME)
        self.assertEqual(cc.data, {"mist": {"site_id": "abc", "vlans": [10, 20]}})
        self.assertEqual(cc.weight, 1000)
        self.assertTrue(cc.is_active)
        self.assertEqual(list(cc.sites.values_list("pk", flat=True)), [self.site_a.pk])
        self.assertEqual(list(cc.roles.values_list("pk", flat=True)), [self.role.pk])
        self.assertEqual(list(cc.tags.values_list("pk", flat=True)), [self.tag.pk])

    def test_nested_site_role_and_tag_resolve_to_existing_objects(self):
        """Nested refs by name bind to the seeded rows; ingest creates no duplicates."""
        change_set = self._diff(self._entity())

        self.assertEqual(_applied(change_set), [("create", "extras.configcontext")])
        self._apply(change_set)

        self.assertEqual(Site.objects.count(), 2)
        self.assertEqual(DeviceRole.objects.count(), 1)
        self.assertEqual(Tag.objects.count(), 1)

    def test_tag_names_resolve_including_a_tag_created_by_the_same_ingest(self):
        """Tags given by name resolve: an existing tag binds, an unknown one is created and assigned."""
        self._ingest(self._entity(tags=("CC Tag", "CC New Tag")))

        cc = ConfigContext.objects.get(name=CC_NAME)
        self.assertEqual(sorted(cc.tags.values_list("name", flat=True)), ["CC New Tag", "CC Tag"])
        self.assertEqual(Tag.objects.filter(name="CC Tag").count(), 1)

    def test_reingest_identical_entity_is_a_noop(self):
        """Re-ingesting the same entity yields no changes (data, M2Ms, tags, weight default)."""
        entity = self._entity(sites=("CC Site B", "CC Site A"))
        self._ingest(entity)

        self.assertEqual(self._diff(entity)["changes"], [])

    def test_reingest_with_reordered_data_keys_is_a_noop(self):
        """JSON data compares by value, not by key order of the submitted string."""
        self._ingest(self._entity(data={"a": 1, "b": {"x": [1, 2], "y": "z"}}))

        self.assertEqual(self._diff(self._entity(data={"b": {"y": "z", "x": [1, 2]}, "a": 1}))["changes"], [])

    def test_reingest_without_weight_keeps_custom_weight(self):
        """An entity sent without weight neither resets a stored weight nor re-diffs."""
        self._ingest(self._entity(weight="2000"))
        self.assertEqual(ConfigContext.objects.get(name=CC_NAME).weight, 2000)

        self.assertEqual(self._diff(self._entity())["changes"], [])

    def test_changing_data_updates(self):
        """Changed data is an UPDATE that replaces the stored JSON."""
        self._ingest(self._entity())
        self._ingest(self._entity(data={"mist": {"site_id": "def"}}))

        self.assertEqual(ConfigContext.objects.get(name=CC_NAME).data, {"mist": {"site_id": "def"}})

    def test_changing_sites_replaces_the_assignment(self):
        """Scope M2Ms are replaced wholesale by what the entity sends."""
        self._ingest(self._entity(sites=("CC Site A",)))
        self._ingest(self._entity(sites=("CC Site B",)))

        cc = ConfigContext.objects.get(name=CC_NAME)
        self.assertEqual(list(cc.sites.values_list("pk", flat=True)), [self.site_b.pk])

    def test_new_tag_is_added_to_existing_tags(self):
        """Tags merge: an update with another tag keeps the stored one (slug conversion on UPDATE)."""
        Tag.objects.create(name="CC Other", slug="cc-other")
        self._ingest(self._entity(tags=("CC Tag",)))
        change_set = self._ingest(self._entity(tags=("CC Other",)))

        self.assertEqual(_applied(change_set), [("update", "extras.configcontext")])
        cc = ConfigContext.objects.get(name=CC_NAME)
        self.assertEqual(sorted(cc.tags.values_list("slug", flat=True)), ["cc-other", "cc-tag"])

    def test_bulk_plan_apply_creates_then_converges(self):
        """bulk-plan-apply creates the ConfigContext, then plans nothing for the same entity."""

        def bulk(entity):
            response = self.client.post(
                self.bulk_plan_apply_url,
                data={"entities": [{"id": "cc-1", "object_type": "extras.configcontext",
                                    "entity": {"configContext": entity}}]},
                format="json",
                **self.authorization_header,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
            result = response.json()["results"][0]
            self.assertIsNone(result.get("errors"), result)
            return result["change_set"]

        first = bulk(self._entity())
        self.assertEqual(_applied(first), [("create", "extras.configcontext")])
        cc = ConfigContext.objects.get(name=CC_NAME)
        self.assertEqual(list(cc.tags.values_list("pk", flat=True)), [self.tag.pk])

        second = bulk(self._entity())
        self.assertEqual(second["changes"], [])
