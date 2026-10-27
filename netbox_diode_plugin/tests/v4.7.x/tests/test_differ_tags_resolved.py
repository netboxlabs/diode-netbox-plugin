#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests for tags_resolved on planned changes."""

from types import SimpleNamespace
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import Tag
from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class TagsResolvedTestCase(APITestCase):
    """Every change to a taggable object names the tags it holds once applied."""

    def setUp(self):
        """Set up a device tagged prod in NetBox only."""
        self.url = "/netbox/api/plugins/diode/generate-diff/"
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

        self.prod = Tag.objects.create(name="prod", slug="prod")
        self.lab = Tag.objects.create(name="lab", slug="lab-tag")
        site = Site.objects.create(name="Tags Site", slug="tags-site")
        manufacturer = Manufacturer.objects.create(name="Tags Manufacturer", slug="tags-manufacturer")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="Tags Type", slug="tags-type")
        role = DeviceRole.objects.create(name="Tags Role", slug="tags-role", color="ff0000")
        self.device = Device.objects.create(
            name="tags-device-01", device_type=device_type, role=role, site=site, serial="OLD",
        )
        self.device.tags.set([self.prod])

    def tearDown(self):
        """Stop the token patch."""
        self.introspect_patcher.stop()
        super().tearDown()

    def plan(self, tags=None):
        """Plan a serial change on the device, with the given payload tags, and return its changes."""
        device = {
            "name": "tags-device-01",
            "serial": "NEW",
            "site": {"name": "Tags Site"},
            "role": {"name": "Tags Role"},
            "device_type": {"model": "Tags Type", "manufacturer": {"name": "Tags Manufacturer"}},
        }
        if tags is not None:
            device["tags"] = tags
        response = self.client.post(
            self.url,
            data={"timestamp": 1, "object_type": "dcim.device", "entity": {"device": device}},
            format="json",
            **self.authorization_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()["change_set"]["changes"]

    def change_for(self, changes, object_type, change_type=None):
        """Return the one change of the given object type, and change type when set."""
        matching = [
            c for c in changes
            if c["object_type"] == object_type and change_type in (None, c["change_type"])
        ]
        self.assertEqual(len(matching), 1, object_type)
        return matching[0]

    def test_payload_without_tags_names_netbox_tags(self):
        """A tag set only in NetBox is named although the payload sends no tags."""
        device = self.change_for(self.plan(), "dcim.device")
        self.assertEqual(device["change_type"], "update")
        self.assertEqual(device["tags_resolved"], [{"ref": self.prod.pk, "name": "prod", "slug": "prod"}])

    def test_payload_tags_merge_with_netbox_tags(self):
        """Existing, new and NetBox-only tags are all named, refs as in the change."""
        changes = self.plan(tags=[{"name": "lab"}, {"name": "brand-new"}])
        device = self.change_for(changes, "dcim.device")
        new_tag = self.change_for(changes, "extras.tag", "create")

        refs = set(device["before"].get("tags", [])) | set(device["data"].get("tags", []))
        self.assertEqual({t["ref"] for t in device["tags_resolved"]}, refs)
        self.assertEqual(
            sorted((t["ref"], t["name"], t["slug"]) for t in device["tags_resolved"] if isinstance(t["ref"], int)),
            sorted([(self.prod.pk, "prod", "prod"), (self.lab.pk, "lab", "lab-tag")]),
        )
        created = [t for t in device["tags_resolved"] if not isinstance(t["ref"], int)]
        self.assertEqual(created, [{"ref": new_tag["ref_id"], "name": "brand-new", "slug": "brand-new"}])

    def test_unchanged_taggable_object_names_no_tags(self):
        """An untagged noop object gets an empty list; a tag itself gets no field."""
        changes = self.plan(tags=[{"name": "lab"}])
        site = self.change_for(changes, "dcim.site")
        self.assertEqual(site["change_type"], "noop")
        self.assertEqual(site["tags_resolved"], [])
        for tag in (c for c in changes if c["object_type"] == "extras.tag"):
            self.assertNotIn("tags_resolved", tag)
