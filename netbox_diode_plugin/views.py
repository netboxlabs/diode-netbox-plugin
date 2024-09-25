#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Views."""
from django.conf import settings as netbox_settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.generic import View
from netbox.views import generic
from users.models import Token
from utilities.views import register_model_view

from netbox_diode_plugin.forms import SettingsForm
from netbox_diode_plugin.models import Setting
from netbox_diode_plugin.plugin_config import (
    get_diode_username_for_user_category,
    get_diode_usernames,
)
from netbox_diode_plugin.reconciler.sdk.client import ReconcilerClient
from netbox_diode_plugin.reconciler.sdk.exceptions import ReconcilerClientError
from netbox_diode_plugin.tables import IngestionLogsTable


class IngestionLogsView(View):
    """Ingestion logs view."""

    INGESTION_METRICS_CACHE_KEY = "ingestion_metrics"

    def get(self, request):
        """Render ingestion logs template."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        diode_settings = Setting.objects.get()

        netbox_to_diode_username = get_diode_username_for_user_category(
            "netbox_to_diode"
        )
        user = get_user_model().objects.get(username=netbox_to_diode_username)
        token = Token.objects.get(user=user)

        reconciler_client = ReconcilerClient(
            target=diode_settings.diode_target,
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
                "error": error,
            }

        return render(request, "diode/ingestion_logs.html", context)


class SettingsView(View):
    """Settings view."""

    def get(self, request):
        """Render settings template."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        try:
            settings = Setting.objects.get()
        except Setting.DoesNotExist:
            settings = None

        if settings is None:
            """Create a default setting with placeholder data."""
            settings = Setting.objects.create(
                diode_target="grpc://localhost:8080/diode"
            )

        diode_users_info = {}

        for user_category, username in get_diode_usernames().items():
            user = get_user_model().objects.get(username=username)
            token = Token.objects.get(user=user)
            diode_users_info[username] = {
                "api_key": token.key,
                "env_var_name": f"{user_category.upper()}_API_KEY",
            }

        context = {
            "diode_target": settings.diode_target,
            "last_updated": settings.last_updated,
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

        settings = Setting.objects.get()
        kwargs["pk"] = settings.pk

        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """POST request handler."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(f"{netbox_settings.LOGIN_URL}?next={request.path}")

        if netbox_settings.PLUGINS_CONFIG.get("netbox_diode_plugin", {}).get(
            "disallow_diode_target_override", False
        ):
            messages.error(
                request,
                "The Diode target is not allowed to be overridden.",
            )
            return redirect("plugins:netbox_diode_plugin:settings")

        settings = Setting.objects.get()
        kwargs["pk"] = settings.pk

        return super().post(request, *args, **kwargs)
