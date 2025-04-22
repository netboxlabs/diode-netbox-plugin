#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Views."""
from django.conf import settings as netbox_settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View
from netbox.plugins import get_plugin_config
from netbox.views import generic
from utilities.views import register_model_view

from netbox_diode_plugin.forms import SettingsForm
from netbox_diode_plugin.models import Setting

User = get_user_model()


def redirect_to_login(request):
    """Redirect to login view."""
    redirect_url = netbox_settings.LOGIN_URL
    target = request.path

    if target and url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        redirect_url = f"{netbox_settings.LOGIN_URL}?next={target}"

    return HttpResponseRedirect(redirect_url)


class SettingsView(View):
    """Settings view."""

    def get(self, request):
        """Render settings template."""
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect_to_login(request)

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

        diode_target = diode_target_override or settings.diode_target

        context = {
            "diode_target": diode_target,
            "is_diode_target_overridden": diode_target_override is not None,
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
            return redirect_to_login(request)

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
            return redirect_to_login(request)

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
