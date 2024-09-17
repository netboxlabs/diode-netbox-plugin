#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.reconciler.sdk.v1 import ingester_pb2, reconciler_pb2
from netbox_diode_plugin.views import IngestionLogsView, SettingsEditView, SettingsView

User = get_user_model()


class IngestionLogsViewTestCase(TestCase):
    """Test case for the IngestionLogsView."""

    def setUp(self):
        """Setup the test case."""
        self.path = reverse("plugins:netbox_diode_plugin:ingestion_logs")
        self.request = RequestFactory().get(self.path)
        self.view = IngestionLogsView()
        self.view.setup(self.request)

    def test_returns_200_for_authenticated(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        response = self.view.get(self.request)
        self.assertEqual(response.status_code, 200)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = AnonymousUser()
        self.view.setup(self.request)

        response = IngestionLogsView.as_view()(self.request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_ingestion_logs_failed_to_retrieve(self):
        """Test that the ingestion logs failed to retrieve throw an error."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        response = self.view.get(self.request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "UNAVAILABLE: failed to connect to all addresses;", str(response.content)
        )

    def test_ingestion_logs_retrieve_logs(self):
        """Test that the retrieved ingestion logs are rendered."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        with mock.patch(
            "netbox_diode_plugin.reconciler.sdk.client.ReconcilerClient.retrieve_ingestion_logs"
        ) as mock_retrieve_ingestion_logs:
            mock_retrieve_ingestion_logs.side_effect = (
                reconciler_pb2.RetrieveIngestionLogsResponse(
                    logs=[
                        reconciler_pb2.IngestionLog(
                            data_type="dcim.site",
                            state=reconciler_pb2.State.RECONCILED,
                            request_id="c6ecd1ea-b23b-4f98-8593-d01d5a0da012",
                            ingestion_ts=1725617988,
                            producer_app_name="diode-test-app",
                            producer_app_version="0.1.0",
                            sdk_name="diode-sdk-python",
                            sdk_version="0.1.0",
                            entity=ingester_pb2.Entity(
                                site=ingester_pb2.Site(
                                    name="Test Site",
                                ),
                            ),
                        )
                    ],
                    next_page_token="AAAAMg==",
                    metrics=reconciler_pb2.IngestionMetrics(
                        total=1,
                    ),
                ),
            )

            response = self.view.get(self.request)
            mock_retrieve_ingestion_logs.assert_called()
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Server Error", str(response.content))


class SettingsViewTestCase(TestCase):
    """Test case for the SettingsView."""

    def setUp(self):
        """Setup the test case."""
        self.path = reverse("plugins:netbox_diode_plugin:settings")
        self.request = RequestFactory().get(self.path)
        self.view = SettingsView()
        self.view.setup(self.request)

    def test_returns_200_for_authenticated(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        response = self.view.get(self.request)
        self.assertEqual(response.status_code, 200)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = AnonymousUser()
        self.view.setup(self.request)

        response = SettingsView.as_view()(self.request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_settings_created_if_not_found(self):
        """Test that the settings are created with placeholder data if not found."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        with mock.patch("netbox_diode_plugin.models.Setting.objects.get") as mock_get:
            mock_get.side_effect = Setting.DoesNotExist

            response = self.view.get(self.request)
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                "grpc://localhost:8080/diode/reconciler", str(response.content)
            )


class SettingsEditViewTestCase(TestCase):
    """Test case for the SettingsEditView."""

    def setUp(self):
        """Setup the test case."""
        self.path = reverse("plugins:netbox_diode_plugin:settings_edit")
        self.request_factory = RequestFactory()
        self.view = SettingsEditView()

    def test_returns_200_for_authenticated(self):
        """Test that the view returns 200 for an authenticated user."""
        request = self.request_factory.get(self.path)
        request.user = User.objects.create_user("foo", password="pass")
        request.user.is_staff = True
        request.htmx = None
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, 200)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view redirects an authenticated user to login page."""
        request = self.request_factory.get(self.path)
        request.user = AnonymousUser()
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_settings_updated(self):
        """Test that the settings are updated."""
        user = User.objects.create_user("foo", password="pass")
        user.is_staff = True

        request = self.request_factory.get(self.path)
        request.user = user
        request.htmx = None
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("grpc://localhost:8080/diode/reconciler", str(response.content))

        request = self.request_factory.post(self.path)
        request.user = user
        request.htmx = None
        request.POST = {"reconciler_target": "grpc://localhost:8090/diode/reconciler"}

        middleware = SessionMiddleware(get_response=lambda request: None)
        middleware.process_request(request)
        request.session.save()

        middleware = MessageMiddleware(get_response=lambda request: None)
        middleware.process_request(request)
        request.session.save()

        response = self.view.post(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("plugins:netbox_diode_plugin:settings"))

        request = self.request_factory.get(self.path)
        request.user = user
        request.htmx = None
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("grpc://localhost:8090/diode/reconciler", str(response.content))

    def test_settings_update_post_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view redirects an authenticated user to login page."""
        request = self.request_factory.post(self.path)
        request.user = AnonymousUser()
        request.htmx = None
        request.POST = {"reconciler_target": "grpc://localhost:8090/diode/reconciler"}

        response = self.view.post(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")
