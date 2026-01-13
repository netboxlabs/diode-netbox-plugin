#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests."""
from unittest import mock

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import TestCase as _TestCase
from django.urls import reverse
from rest_framework import status
from users.models import ObjectPermission
from utilities.permissions import resolve_permission_type

from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.views import SettingsEditView, SettingsView

User = get_user_model()


class TestCase(_TestCase):
    """Base test case class for NetBox Diode plugin tests."""

    def add_permissions(self, user, *names):
        """Assign a set of permissions to the test user. Accepts permission names in the form <app>.<action>_<model>."""
        for name in names:
            object_type, action = resolve_permission_type(name)
            obj_perm = ObjectPermission(name=name, actions=[action])
            obj_perm.save()
            obj_perm.users.add(user)
            obj_perm.object_types.add(object_type)


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
        self.add_permissions(self.request.user, "netbox_diode_plugin.view_setting")

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
        self.add_permissions(self.request.user, "netbox_diode_plugin.view_setting")

        with mock.patch("netbox_diode_plugin.models.Setting.objects.get") as mock_get:
            mock_get.side_effect = Setting.DoesNotExist

            response = self.view.get(self.request)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn("grpc://localhost:8080/diode", str(response.content))


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
        self.add_permissions(request.user, "netbox_diode_plugin.view_setting", "netbox_diode_plugin.change_setting")
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
        self.add_permissions(user, "netbox_diode_plugin.view_setting", "netbox_diode_plugin.change_setting")

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

    def test_settings_update_allowed_on_get_method_with_override(self):
        """Test that accessing settings edit shows info message when diode target is overridden."""
        with mock.patch(
            "netbox_diode_plugin.views.get_plugin_config"
        ) as mock_get_plugin_config:
            mock_get_plugin_config.return_value = "grpc://localhost:8080/diode"

            user = User.objects.create_user("foo", password="pass")
            self.add_permissions(
                user,
                "netbox_diode_plugin.view_setting",
                "netbox_diode_plugin.add_setting",
                "netbox_diode_plugin.change_setting",
            )

            request = self.request_factory.get(self.path)
            request.user = user
            request.htmx = None

            middleware = SessionMiddleware(get_response=lambda request: None)
            middleware.process_request(request)
            request.session.save()

            middleware = MessageMiddleware(get_response=lambda request: None)
            middleware.process_request(request)
            request.session.save()

            self.view.setup(request)
            response = self.view.get(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # Check that the message was added
            storage = messages.get_messages(request)
            message_list = list(storage)
            self.assertEqual(len(message_list), 1)
            self.assertEqual(
                str(message_list[0]),
                "The Diode target field is disabled because it is overridden in the plugin configuration.",
            )

    def test_settings_update_allowed_on_post_method_with_override(self):
        """Test that updating settings succeeds when diode target is overridden (field is disabled in form)."""
        with mock.patch(
            "netbox_diode_plugin.views.get_plugin_config"
        ) as mock_get_plugin_config:
            mock_get_plugin_config.return_value = "grpc://localhost:8080/diode"

            user = User.objects.create_user("foo", password="pass")
            self.add_permissions(
                user,
                "netbox_diode_plugin.view_setting",
                "netbox_diode_plugin.add_setting",
                "netbox_diode_plugin.change_setting",
            )

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

            setattr(request, "session", "session")
            messages = FallbackStorage(request)
            request._messages = messages

            self.view.setup(request)
            response = self.view.post(request)

            # Should succeed and redirect to settings view
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)
            self.assertEqual(
                response.url, reverse("plugins:netbox_diode_plugin:settings")
            )
