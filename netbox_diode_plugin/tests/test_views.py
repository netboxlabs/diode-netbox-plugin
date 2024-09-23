#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status

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
        cache.delete("ingestion_metrics")

    def test_returns_200_for_authenticated(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        response = self.view.get(self.request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = AnonymousUser()
        self.view.setup(self.request)

        response = IngestionLogsView.as_view()(self.request)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_ingestion_logs_failed_to_retrieve(self):
        """Test that the ingestion logs failed to retrieve throw an error."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        response = self.view.get(self.request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
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
                reconciler_pb2.RetrieveIngestionLogsResponse(
                    metrics=reconciler_pb2.IngestionMetrics(
                        total=1,
                    ),
                ),
            )

            response = self.view.get(self.request)
            mock_retrieve_ingestion_logs.assert_called()
            self.assertEqual(mock_retrieve_ingestion_logs.call_count, 2)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertNotIn("Server Error", str(response.content))

    def test_cached_metrics(self):
        """Test that the cached metrics are used."""
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

            # Set up the cache
            cache.set(
                "ingestion_metrics",
                {
                    "new": 10,
                    "reconciled": 20,
                    "failed": 5,
                    "no_changes": 65,
                    "total": 1,
                },
                timeout=300,
            )

            response = self.view.get(self.request)
            mock_retrieve_ingestion_logs.assert_called()
            self.assertEqual(mock_retrieve_ingestion_logs.call_count, 1)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
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
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view returns 200 for an authenticated user."""
        self.request.user = AnonymousUser()
        self.view.setup(self.request)

        response = SettingsView.as_view()(self.request)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_settings_created_if_not_found(self):
        """Test that the settings are created with placeholder data if not found."""
        self.request.user = User.objects.create_user("foo", password="pass")
        self.request.user.is_staff = True

        with mock.patch("netbox_diode_plugin.models.Setting.objects.get") as mock_get:
            mock_get.side_effect = Setting.DoesNotExist

            response = self.view.get(self.request)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn(
                "grpc://localhost:8080/diode", str(response.content)
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
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_redirects_to_login_page_for_unauthenticated_user(self):
        """Test that the view redirects an authenticated user to login page."""
        request = self.request_factory.get(self.path)
        request.user = AnonymousUser()
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
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
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("grpc://localhost:8080/diode", str(response.content))

        request = self.request_factory.post(self.path)
        request.user = user
        request.htmx = None
        request.POST = {"diode_target": "grpc://localhost:8090/diode"}

        middleware = SessionMiddleware(get_response=lambda request: None)
        middleware.process_request(request)
        request.session.save()

        middleware = MessageMiddleware(get_response=lambda request: None)
        middleware.process_request(request)
        request.session.save()

        response = self.view.post(request)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, reverse("plugins:netbox_diode_plugin:settings"))

        request = self.request_factory.get(self.path)
        request.user = user
        request.htmx = None
        self.view.setup(request)

        response = self.view.get(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("grpc://localhost:8090/diode", str(response.content))

    def test_settings_update_post_redirects_to_login_page_for_unauthenticated_user(
        self,
    ):
        """Test that the view redirects an authenticated user to login page."""
        request = self.request_factory.post(self.path)
        request.user = AnonymousUser()
        request.htmx = None
        request.POST = {"diode_target": "grpc://localhost:8090/diode"}

        response = self.view.post(request)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, f"/netbox/login/?next={self.path}")

    def test_settings_update_disallowed(self):
        """Test that the Diode target cannot be overridden."""
        with mock.patch("netbox_diode_plugin.views.netbox_settings") as mock_settings:
            mock_settings.PLUGINS_CONFIG = {
                "netbox_diode_plugin": {
                    "disallow_diode_target_override": True
                }
            }

            user = User.objects.create_user("foo", password="pass")
            user.is_staff = True

            request = self.request_factory.post(self.path)
            request.user = user
            request.htmx = None
            request.POST = {"diode_target": "grpc://localhost:8090/diode"}

            middleware = SessionMiddleware(get_response=lambda request: None)
            middleware.process_request(request)
            request.session.save()

            middleware = MessageMiddleware(get_response=lambda request: None)
            middleware.process_request(request)
            request.session.save()

            setattr(request, 'session', 'session')
            messages = FallbackStorage(request)
            request._messages = messages

            self.view.setup(request)
            response = self.view.post(request)

            self.assertEqual(response.status_code, status.HTTP_302_FOUND)
            self.assertEqual(response.url, reverse("plugins:netbox_diode_plugin:settings"))
            self.assertEqual(len(request._messages._queued_messages), 1)
            self.assertEqual(
                str(request._messages._queued_messages[0]),
                "The Diode target is not allowed to be overridden.",
            )
