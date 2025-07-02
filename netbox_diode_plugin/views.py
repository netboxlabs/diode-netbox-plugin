#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Views."""
import logging
from collections import defaultdict

from django.conf import settings as netbox_settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.generic import View
from netbox.plugins import get_plugin_config
from netbox.views import generic
from users.models import ObjectPermission
from utilities.forms import ConfirmationForm
from utilities.htmx import htmx_partial
from utilities.permissions import get_permission_for_model, permission_is_exempt
from utilities.views import register_model_view

from netbox_diode_plugin.client import create_client, delete_client, get_client, list_clients
from netbox_diode_plugin.forms import ClientCredentialForm, SettingsForm
from netbox_diode_plugin.models import ClientCredentials, Setting
from netbox_diode_plugin.tables import ClientCredentialsTable

User = get_user_model()


logger = logging.getLogger(__name__)

def redirect_to_login(request):
    """Redirect to login view."""
    redirect_url = netbox_settings.LOGIN_URL
    target = request.path

    if target and url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        redirect_url = f"{netbox_settings.LOGIN_URL}?next={target}"

    return HttpResponseRedirect(redirect_url)


class BaseDiodeView(View):
    """
    Base view class for Diode plugin views.

    Provides authentication and permission checking functionality for views
    that need to interact with the Diode API. Includes methods for:
    - Object permission filtering and retrieval
    - Permission checking for authenticated users
    - Authentication validation for requests
    """

    def get_permission_filter(self, user_obj):
        """Return the permission filter for the user."""
        return Q(users=user_obj) | Q(groups__user=user_obj)

    def get_object_permissions(self, user_obj):
        """Return all permissions granted to the user by an ObjectPermission."""
        # Initialize a dictionary mapping permission names to sets of constraints
        perms = defaultdict(list)

        # Collect any configured default permissions
        for perm_name, constraints in netbox_settings.DEFAULT_PERMISSIONS.items():
            constraints = constraints or ()
            if type(constraints) not in (list, tuple):
                raise ImproperlyConfigured(
                    f"Constraints for default permission {perm_name} must be defined as a list or tuple."
                )
            perms[perm_name].extend(constraints)

        # Retrieve all assigned and enabled ObjectPermissions
        object_permissions = ObjectPermission.objects.filter(
            self.get_permission_filter(user_obj),
            enabled=True
        ).order_by('id').distinct('id').prefetch_related('object_types')

        # Create a dictionary mapping permissions to their constraints
        for obj_perm in object_permissions:
            for object_type in obj_perm.object_types.all():
                for action in obj_perm.actions:
                    perm_name = f"{object_type.app_label}.{action}_{object_type.model}"
                    perms[perm_name].extend(obj_perm.list_constraints())

        return perms

    def get_all_permissions(self, user_obj, obj=None):
        """Get all permissions for the user."""
        if not user_obj.is_active or user_obj.is_anonymous:
            return {}
        if not hasattr(user_obj, '_object_perm_cache'):
            user_obj._object_perm_cache = self.get_object_permissions(user_obj)
        return user_obj._object_perm_cache

    def has_perm(self, user_obj, perm):
        """Check if the user has the required permission."""
        # Superusers implicitly have all permissions
        if not user_obj.is_authenticated:
            return False

        if user_obj.is_active and user_obj.is_superuser:
            return True

        # Permission is exempt from enforcement (i.e. listed in EXEMPT_VIEW_PERMISSIONS)
        if permission_is_exempt(perm):
            return True

        # Handle inactive/anonymous users
        if not user_obj.is_active or user_obj.is_anonymous:
            return False

        object_permissions = self.get_all_permissions(user_obj)

        # If no applicable ObjectPermissions have been created for this user/permission, deny permission
        if perm not in object_permissions:
            return False

        return True

    def check_authentication(self, request):
        """Check if the user has the required permission."""
        if not request.user.is_authenticated:
            return redirect_to_login(request)

        if not self.has_perm(request.user, self.get_required_permission()):
            return redirect(
                reverse("home",)
            )
        return None

class SettingsView(BaseDiodeView):
    """Settings view."""

    def get_required_permission(self):
        """Return the permission required to view Diode plugin settings."""
        return "netbox_diode_plugin.view_setting"

    def get(self, request):
        """Render settings template."""
        if ret := self.check_authentication(request):
            return ret

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
class SettingsEditView(BaseDiodeView,generic.ObjectEditView):
    """Settings edit view."""

    queryset = Setting.objects
    form = SettingsForm
    template_name = "diode/settings_edit.html"
    default_return_url = "plugins:netbox_diode_plugin:settings"

    def get_required_permission(self):
        """Return the permission required to view Diode plugin settings."""
        return "netbox_diode_plugin.change_setting"

    def get(self, request, *args, **kwargs):
        """GET request handler."""
        if ret := self.check_authentication(request):
            return ret

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
        if ret := self.check_authentication(request):
            return ret

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
    """Get return URL mixin."""

    def get_return_url(self, request):
        """Get return URL."""
        # First, see if `return_url` was specified as a query parameter or form data. Use this URL only if it's
        # considered safe.
        return_url = request.GET.get("return_url") or request.POST.get("return_url")
        if return_url and url_has_allowed_host_and_scheme(
            return_url, allowed_hosts=None
        ):
            return return_url

        return None


class ClientCredentialListView(BaseDiodeView):
    """Client credential list view."""

    table = ClientCredentialsTable
    template_name = "diode/client_credential_list.html"
    model = ClientCredentials

    def get_required_permission(self):
        """Return the permission required to view client credentials list."""
        return "netbox_diode_plugin.view_clientcredentials"

    def get_table_data(self, request):
        """Get table data."""
        try:
            data = list_clients(request)
            total = len(data)
        except Exception as e:
            logger.debug(f"Error loading client credentials error: {str(e)}")
            messages.error(self.request, str(e))
            data = []
            total = 0

        return total, data

    def get(self, request):
        """GET request handler."""
        if ret := self.check_authentication(request):
            return ret

        total, data = self.get_table_data(request)
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
                    "total_count": len(data),
                },
            )

        context = {
            "model": ClientCredentials,
            "table": table,
            "total_count": len(data),
        }

        return render(request, self.template_name, context)


class ClientCredentialDeleteView(GetReturnURLMixin, BaseDiodeView):
    """Client credential delete view."""

    template_name = "diode/client_credential_delete.html"
    default_return_url = "plugins:netbox_diode_plugin:client_credential_list"

    def get_required_permission(self):
        """Return the permission required to delete client credentials."""
        return "netbox_diode_plugin.delete_clientcredentials"

    def get(self, request, client_credential_id):
        """GET request handler."""
        if ret := self.check_authentication(request):
            return ret

        data = get_client(request, client_credential_id)

        return render(
            request,
            self.template_name,
            {
                "object": data,
                "object_type": "Client Credential",
                "return_url": self.get_return_url(request) or reverse(self.default_return_url),
            },
        )

    def post(self, request, client_credential_id):
        """POST request handler."""
        sanitized_client_credential_id = client_credential_id.replace('\n', '').replace('\r', '')
        logger.info(f"Deleting client {sanitized_client_credential_id}")
        if ret := self.check_authentication(request):
            return ret

        form = ConfirmationForm(request.POST)
        if form.is_valid():
            try:
                delete_client(request, client_credential_id)
                messages.success(request, _("Client deleted successfully"))
            except Exception as e:
                logger.error(
                    f"Error deleting client: {sanitized_client_credential_id} error: {str(e)}"
                )
                messages.error(request, str(e))

        return redirect(
            reverse(
                "plugins:netbox_diode_plugin:client_credential_list",
            )
        )


class ClientCredentialAddView(GetReturnURLMixin, BaseDiodeView):
    """View for adding client credentials."""

    template_name = "diode/client_credential_add.html"
    form_class = ClientCredentialForm
    default_return_url = "plugins:netbox_diode_plugin:client_credential_list"

    def get_required_permission(self):
        """Return the permission required to add new client credentials."""
        return "netbox_diode_plugin.add_clientcredentials"

    def get(self, request):
        """GET request handler."""
        if ret := self.check_authentication(request):
            return ret

        form = self.form_class()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "return_url": self.get_return_url(request) or reverse(self.default_return_url),
            },
        )

    def post(self, request):
        """POST request handler."""
        if ret := self.check_authentication(request):
            return ret

        form = self.form_class(request.POST)
        if form.is_valid():
            try:
                response = create_client(request, form.cleaned_data["client_name"], "diode:ingest")
                # Store the client credentials in session
                request.session['client_secret'] = response.get('client_secret')
                request.session['client_name'] = form.cleaned_data["client_name"]
                request.session['client_id'] = response.get('client_id')
                return redirect(
                    reverse(
                        "plugins:netbox_diode_plugin:client_credential_secret",
                    )
                )
            except Exception as e:
                logger.error(f"Error creating client: {str(e)}")
                messages.error(request, str(e))

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "return_url": self.get_return_url(request) or reverse(self.default_return_url),
            },
        )


class ClientCredentialSecretView(BaseDiodeView):
    """View for displaying client secret."""

    template_name = "diode/client_credential_secret.html"

    def get_required_permission(self):
        """Return the permission required to view client credential secrets."""
        return "netbox_diode_plugin.view_clientcredentials"

    def get(self, request):
        """Get request handler."""
        if ret := self.check_authentication(request):
            return ret

        # Get the client secret from session
        client_secret = request.session.get('client_secret')
        client_name = request.session.get('client_name')
        client_id = request.session.get('client_id')

        if not client_secret:
            messages.error(request, _("No client secret found. Please create a new client."))
            return redirect(
                reverse(
                    "plugins:netbox_diode_plugin:client_credential_list",
                )
            )

        # Clear the session data after retrieving it
        request.session.pop('client_secret', None)
        request.session.pop('client_name', None)
        request.session.pop('client_id', None)

        return render(
            request,
            self.template_name,
            {
                "object": {
                    "client_name": client_name,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
            },
        )
