#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Views."""
import os

from django.conf import settings as netbox_settings
from django.contrib import messages
from django.contrib.auth import get_user, get_user_model
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.generic import View
from netbox.plugins import get_plugin_config
from netbox.views import generic
from users.models import Group, ObjectPermission, Token
from utilities.views import register_model_view

from netbox_diode_plugin.forms import SettingsForm, SetupForm
from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.plugin_config import (
    get_diode_user_types_with_labels,
    get_diode_username_for_user_type,
    get_diode_usernames,
)
from netbox_diode_plugin.reconciler.sdk.client import ReconcilerClient
from netbox_diode_plugin.reconciler.sdk.exceptions import ReconcilerClientError
from netbox_diode_plugin.tables import IngestionLogsTable

User = get_user_model()


class IngestionLogsView(View):
    """Ingestion logs view."""

    INGESTION_METRICS_CACHE_KEY = "ingestion_metrics"

    def get(self, request):
        """Render ingestion logs template."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        netbox_to_diode_username = get_diode_username_for_user_type("netbox_to_diode")
        try:
            user = get_user_model().objects.get(username=netbox_to_diode_username)
        except User.DoesNotExist:
            return redirect("plugins:netbox_diode_plugin:setup")

        if not Token.objects.filter(user=user).exists():
            return redirect("plugins:netbox_diode_plugin:setup")

        token = Token.objects.get(user=user)

        settings = Setting.objects.get()

        diode_target_override = get_plugin_config(
            "netbox_diode_plugin", "diode_target_override"
        )
        diode_target = diode_target_override or settings.diode_target

        reconciler_client = ReconcilerClient(
            target=diode_target,
            api_key=token.key,
        )

        page_size = 50

        try:
            ingestion_logs_filters = {
                "page_size": page_size,
            }
            request_page_token = request.GET.get("page_token")
            if request_page_token is not None:
                ingestion_logs_filters["page_token"] = request_page_token

            resp = reconciler_client.retrieve_ingestion_logs(**ingestion_logs_filters)
            table = IngestionLogsTable(resp.logs)

            cached_ingestion_metrics = cache.get(self.INGESTION_METRICS_CACHE_KEY)
            if (
                cached_ingestion_metrics is not None
                and cached_ingestion_metrics["total"] == resp.metrics.total
            ):
                metrics = cached_ingestion_metrics
            else:
                ingestion_metrics = reconciler_client.retrieve_ingestion_logs(
                    only_metrics=True
                )
                metrics = {
                    "queued": ingestion_metrics.metrics.queued or 0,
                    "reconciled": ingestion_metrics.metrics.reconciled or 0,
                    "failed": ingestion_metrics.metrics.failed or 0,
                    "no_changes": ingestion_metrics.metrics.no_changes or 0,
                    "total": ingestion_metrics.metrics.total or 0,
                }
                cache.set(
                    self.INGESTION_METRICS_CACHE_KEY,
                    metrics,
                    timeout=300,
                )

            context = {
                "next_page_token": resp.next_page_token,
                "ingestion_logs_table": table,
                "total_count": resp.metrics.total,
                "ingestion_metrics": metrics,
            }

        except ReconcilerClientError as error:
            context = {
                "ingestion_logs_error": error,
            }

        return render(request, "diode/ingestion_logs.html", context)


class SettingsView(View):
    """Settings view."""

    def get(self, request):
        """Render settings template."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        diode_target_override = get_plugin_config(
            "netbox_diode_plugin", "diode_target_override"
        )

        try:
            settings = Setting.objects.get()
        except Setting.DoesNotExist:
            default_diode_target = get_plugin_config(
                "netbox_diode_plugin", "diode_target"
            )
            settings = Setting.objects.create(
                diode_target=diode_target_override or default_diode_target
            )

        diode_users_info = {}

        diode_users_errors = []

        for user_type, username in get_diode_usernames().items():
            try:
                user = get_user_model().objects.get(username=username)
            except User.DoesNotExist:
                diode_users_errors.append(
                    f"User '{username}' does not exist, please check plugin configuration."
                )
                continue

            if not Token.objects.filter(user=user).exists():
                diode_users_errors.append(
                    f"API key for '{username}' does not exist, please check plugin configuration."
                )
                continue

            token = Token.objects.get(user=user)

            diode_users_info[username] = {
                "api_key": token.key,
                "env_var_name": f"{user_type.upper()}_API_KEY",
            }

        if diode_users_errors:
            return redirect("plugins:netbox_diode_plugin:setup")

        diode_target = diode_target_override or settings.diode_target

        context = {
            "diode_users_errors": diode_users_errors,
            "diode_target": diode_target,
            "is_diode_target_overridden": diode_target_override is not None,
            "diode_users_info": diode_users_info,
        }

        return render(request, "diode/settings.html", context)


@register_model_view(Setting, "edit")
class SettingsEditView(generic.ObjectEditView):
    """Settings edit view."""

    queryset = Setting.objects
    form = SettingsForm
    template_name = "diode/settings_edit.html"
    default_return_url = "plugins:netbox_diode_plugin:settings"

    def get(self, request, *args, **kwargs):
        """GET request handler."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        diode_target_override = get_plugin_config(
            "netbox_diode_plugin", "diode_target_override"
        )
        if diode_target_override:
            messages.error(
                request,
                "The Diode target is not allowed to be modified.",
            )
            return redirect("plugins:netbox_diode_plugin:settings")

        settings = Setting.objects.get()
        kwargs["pk"] = settings.pk

        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """POST request handler."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        diode_target_override = get_plugin_config(
            "netbox_diode_plugin", "diode_target_override"
        )
        if diode_target_override:
            messages.error(
                request,
                "The Diode target is not allowed to be modified.",
            )
            return redirect("plugins:netbox_diode_plugin:settings")

        settings = Setting.objects.get()
        kwargs["pk"] = settings.pk

        return super().post(request, *args, **kwargs)


class SetupView(View):
    """Setup view."""

    form = SetupForm

    @staticmethod
    def _retrieve_predefined_api_key(api_key_env_var):
        """Retrieve predefined API key from a secret or environment variable."""
        try:
            f = open("/run/secrets/" + api_key_env_var, encoding="utf-8")
        except OSError:
            return os.getenv(api_key_env_var)
        else:
            with f:
                return f.readline().strip()

    def _retrieve_users(self):
        """Retrieve users for the setup form."""
        user_types_with_labels = get_diode_user_types_with_labels()
        users = {
            user_type: {
                "username": None,
                "user": None,
                "api_key": None,
                "api_key_env_var_name": f"{user_type.upper()}_API_KEY",
                "predefined_api_key": self._retrieve_predefined_api_key(
                    f"{user_type.upper()}_API_KEY"
                ),
            }
            for user_type, _ in user_types_with_labels
        }
        for user_type, _ in user_types_with_labels:
            username = get_diode_username_for_user_type(user_type)
            users[user_type]["username"] = username

            try:
                user = get_user_model().objects.get(username=username)
                users[user_type]["user"] = user
                if Token.objects.filter(user=user).exists():
                    users[user_type]["api_key"] = Token.objects.get(user=user).key
            except User.DoesNotExist:
                continue
        return users

    def get(self, request):
        """GET request handler."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        users = self._retrieve_users()

        context = {
            "form": self.form(users),
        }

        return render(request, "diode/setup.html", context)

    def post(self, request):
        """POST request handler."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        users = self._retrieve_users()

        form = self.form(users, request.POST)

        group = Group.objects.get(name="diode")
        permission = ObjectPermission.objects.get(name="Diode")

        if form.is_valid():
            for field in form.fields:
                user_type = field.rsplit("_api_key", 1)[0]
                username = users[user_type].get("username")
                if username is None:
                    raise ValueError(
                        f"Username for user type '{user_type}' is not defined"
                    )

                user = users[user_type].get("user")
                if user is None:
                    user = get_user_model().objects.create_user(
                        username=username, is_active=True
                    )
                    user.groups.add(*[group.id])

                if user_type == "diode_to_netbox":
                    permission.users.set([user.id])

                if not Token.objects.filter(user=user).exists():
                    Token.objects.create(user=user, key=form.cleaned_data[field])

            return redirect("plugins:netbox_diode_plugin:settings")

        context = {
            "form": form,
        }

        return render(request, "diode/setup.html", context)
