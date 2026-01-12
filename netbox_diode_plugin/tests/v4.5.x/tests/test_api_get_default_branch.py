#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - GetDefaultBranch API Tests."""

import logging
from types import SimpleNamespace
from unittest import mock

from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


class GetDefaultBranchViewTestCase(APITestCase):
    """Test cases for GetDefaultBranchView."""

    def setUp(self):
        """Set up the test case."""
        self.url = "/netbox/api/plugins/diode/default-branch/"

        self.authorization_header = {"HTTP_AUTHORIZATION": "Bearer mocked_oauth_token"}
        self.diode_user = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:read", "netbox:write"],
            token_data={"scope": "netbox:read netbox:write"}
        )

        self.introspect_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            '_introspect_token',
            return_value=self.diode_user
        )
        self.introspect_patcher.start()

    def tearDown(self):
        """Clean up after tests."""
        self.introspect_patcher.stop()
        super().tearDown()

    def test_get_default_branch_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_default_branch_without_read_scope(self):
        """Test that requests without netbox:read scope are rejected."""
        # Mock user with only write scope
        user_without_read = SimpleNamespace(
            user=get_diode_user(),
            token_scopes=["netbox:write"],
            token_data={"scope": "netbox:write"}
        )

        with mock.patch.object(
            DiodeOAuth2Authentication,
            '_introspect_token',
            return_value=user_without_read
        ):
            response = self.client.get(self.url, **self.authorization_header)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_default_branch_no_branching_plugin(self):
        """Test response when branching plugin is not installed."""
        # Create a setting without branch
        Setting.objects.create(diode_target="grpc://localhost:8080/diode")

        # Mock Branch as None (simulating plugin not installed)
        with mock.patch('netbox_diode_plugin.api.views.Branch', None):
            response = self.client.get(self.url, **self.authorization_header)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn("branch", response.json())
            self.assertIsNone(response.json()["branch"])

    def test_get_default_branch_no_settings(self):
        """Test response when no settings exist."""
        # Ensure no settings exist
        Setting.objects.all().delete()

        response = self.client.get(self.url, **self.authorization_header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("branch", response.json())
        self.assertIsNone(response.json()["branch"])

    def test_get_default_branch_settings_without_branch(self):
        """Test response when settings exist but branch is not set."""
        # Create a setting without branch
        Setting.objects.create(diode_target="grpc://localhost:8080/diode", branch_id=None)

        response = self.client.get(self.url, **self.authorization_header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("branch", response.json())
        self.assertIsNone(response.json()["branch"])

    def test_get_default_branch_with_branching_plugin_and_branch_set(self):
        """Test response when branching plugin is installed and branch is set."""
        # Create a mock Branch object
        mock_branch = mock.Mock()
        mock_branch.schema_id = "branch-123"
        mock_branch.name = "main"
        mock_branch.id = 1

        # Create a setting with branch_id
        Setting.objects.create(
            diode_target="grpc://localhost:8080/diode",
            branch_id=1
        )

        # Mock the Branch model and query
        mock_branch_model = mock.Mock()
        mock_branch_model.objects.get.return_value = mock_branch

        with mock.patch('netbox_diode_plugin.api.views.Branch', mock_branch_model):
            with mock.patch.object(Setting, 'branch', new_callable=mock.PropertyMock) as mock_branch_property:
                mock_branch_property.return_value = mock_branch

                response = self.client.get(self.url, **self.authorization_header)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertIn("branch", response.json())
                self.assertIsNotNone(response.json()["branch"])
                self.assertEqual(response.json()["branch"]["id"], "branch-123")
                self.assertEqual(response.json()["branch"]["name"], "main")

    def test_get_default_branch_exception_handling(self):
        """Test that exceptions during branch retrieval are handled gracefully."""
        # Create a setting with branch_id
        Setting.objects.create(
            diode_target="grpc://localhost:8080/diode",
            branch_id=1
        )

        # Mock Branch model to exist but raise exception on query
        mock_branch_model = mock.Mock()

        with mock.patch('netbox_diode_plugin.api.views.Branch', mock_branch_model):
            with mock.patch.object(Setting, 'branch', new_callable=mock.PropertyMock) as mock_branch_property:
                # Simulate an exception when accessing the branch property
                mock_branch_property.side_effect = Exception("Database error")

                response = self.client.get(self.url, **self.authorization_header)

                # Should return 200 with null branch due to exception handling
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertIn("branch", response.json())
                self.assertIsNone(response.json()["branch"])

    def test_get_default_branch_with_valid_authentication(self):
        """Test that authenticated requests with proper scope are successful."""
        response = self.client.get(self.url, **self.authorization_header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("branch", response.json())
        # Response structure is correct even if branch is None
        self.assertIsInstance(response.json(), dict)
