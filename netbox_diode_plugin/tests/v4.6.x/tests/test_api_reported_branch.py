#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - branch reported on planned change sets."""

from contextvars import ContextVar
from types import SimpleNamespace
from unittest import mock

from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api import views
from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.plugin_config import get_diode_user

PLAN_ENDPOINTS = ("generate-diff", "bulk-plan", "bulk-plan-apply")


class ReportedBranchTestCase(APITestCase):
    """The plan endpoints report the branch the request ran on."""

    def setUp(self):
        """Set up a plugin default branch and an empty active branch."""
        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"},
        )
        self._patch(mock.patch.object(DiodeOAuth2Authentication, "_introspect_token", return_value=diode_user))

        # The plugin has a default branch. Requests without the X-NetBox-Branch
        # header still run on main, so the default branch must not be reported.
        default_branch = SimpleNamespace(schema_id="dflt0001", name="default")
        Setting.objects.create(diode_target="grpc://localhost:8080/diode", branch_id=1)
        branch_model = mock.Mock()
        branch_model.objects.get.return_value = default_branch
        self._patch(mock.patch.object(views, "Branch", branch_model))
        self._patch(mock.patch.object(Setting, "branch", new_callable=mock.PropertyMock, return_value=default_branch))

        # netbox-branching sets this context variable for the whole request.
        self.active_branch = ContextVar("active_branch", default=None)
        self._patch(mock.patch.object(views, "active_branch", self.active_branch, create=True))

    def _patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def reported_branch(self, endpoint, slug):
        """Plan a new site through endpoint and return the branch on its change set."""
        entity = {"site": {"name": slug, "slug": slug}}
        if endpoint == "generate-diff":
            payload = {"timestamp": 1, "object_type": "dcim.site", "entity": entity}
        else:
            payload = {"entities": [{"id": "entity-1", "object_type": "dcim.site", "entity": entity}]}
        response = self.client.post(
            f"/netbox/api/plugins/diode/{endpoint}/", data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        body = response.json()
        change_set = body["change_set"] if endpoint == "generate-diff" else body["results"][0]["change_set"]
        return change_set.get("branch")

    def test_main_is_reported_as_no_branch(self):
        """A request without an active branch ran on main, whatever the default branch is."""
        for endpoint in PLAN_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.assertIsNone(self.reported_branch(endpoint, f"main-{endpoint}"))

    def test_active_branch_is_reported(self):
        """A request on a branch reports that branch."""
        token = self.active_branch.set(SimpleNamespace(schema_id="feat0001", name="feature"))
        self.addCleanup(self.active_branch.reset, token)
        for endpoint in PLAN_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.reported_branch(endpoint, f"branch-{endpoint}"),
                    {"id": "feat0001", "name": "feature"},
                )
