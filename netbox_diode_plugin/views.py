#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Views."""
import logging

from django.conf import settings as netbox_settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View
from netbox.plugins import get_plugin_config
from netbox.views import generic
from utilities.htmx import htmx_partial
from utilities.permissions import get_permission_for_model
from utilities.views import register_model_view

from netbox_diode_plugin.forms import SettingsForm
from netbox_diode_plugin.models import ClientCredentials, Setting
from netbox_diode_plugin.tables import ClientCredentialsTable
from netbox_diode_plugin.client import list_clients, create_client, delete_client

User = get_user_model()


logger = logging.getLogger(__name__)

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


class GetReturnURLMixin:

    def get_return_url(self, request):

        # First, see if `return_url` was specified as a query parameter or form data. Use this URL only if it's
        # considered safe.
        return_url = request.GET.get("return_url") or request.POST.get("return_url")
        if return_url and url_has_allowed_host_and_scheme(
            return_url, allowed_hosts=None
        ):
            return return_url

        return None


class BaseDiodeView(View):

    def check_authentication(self, request):
        if not request.user.is_authenticated or not request.user.is_staff:
            next_url = request.path
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
                next_url = "/"

            return redirect(f"{netbox_settings.LOGIN_URL}?next={next_url}")

    def get_required_permission(self):
        return get_permission_for_model(self.model, "view")

class ClientCredentialListView(BaseDiodeView):
    table = ClientCredentialsTable
    template_name = "diode/client_credential_list.html"
    model = ClientCredentials

    def get_table_data(self):
        try:
            data = list_clients()
            total = len(data)
        except Exception as e:
            logger.debug(f"Error loading client credentials error: {str(e)}")
            messages.error(self.request, str(e))
            data = []
            total = 0

        return total, data

    def get(self, request):
        if ret := self.check_authentication(request):
            return ret

        total, data = self.get_table_data()
        table = self.table(data=data)  # Pass the data to the table

        # If this is an HTMX request, return only the rendered table HTML
        if htmx_partial(request):
            if request.GET.get("embedded", False):
                table.embedded = True
                # Hide selection checkboxes
                if "pk" in table.base_columns:
                    table.columns.hide("pk")
            return render(
                request,
                "htmx/table.html",
                {
                    "model": ClientCredentials,
                    "table": table,
                },
            )

        context = {
            "model": ClientCredentials,
            "table": table,
        }

        return render(request, self.template_name, context)


class ClientCredentialDeleteView(GetReturnURLMixin, BaseDiodeView):
    template_name = "diode/client_credential_delete.html"

    def get(self, request, deviation_id):
        if ret := self.check_authentication(request):
            return ret

        data = get_deviation_from_id(request, deviation_id)

        form = self.init_branch_form(request, data)

        return render(
            request,
            self.template_name,
            {
                "object": data,
                "form": form,
                "return_url": self.get_return_url(request),
            },
        )

    def post(self, request, deviation_id):
        if ret := self.check_authentication(request):
            return ret

        form = BranchSelectForm(request.POST)

        if form.is_valid():
            branch_id = None
            if "branch" in form.cleaned_data:
                branch_id = form.cleaned_data.get("branch")

            try:
                diode_api.deviation_rediff(deviation_id, branch_id)
                messages.success(request, _("Deviation Rediffed"))
            except ReconcilerClientError as e:
                sanitized_deviation_id = deviation_id.replace("\n", "").replace(
                    "\r", ""
                )
                logger.error(
                    f"Error rediffing deviation: {sanitized_deviation_id} error: {str(e)}"
                )
                messages.error(request, str(e))

            return redirect(
                reverse(
                    "plugins:netbox_assurance_plugin:deviation",
                    kwargs={"deviation_id": deviation_id},
                )
            )

        data = get_deviation_from_id(request, deviation_id)
        return render(
            request,
            self.template_name,
            {
                "object": data,
                "form": form,
                "return_url": self.get_return_url(request),
            },
        )
