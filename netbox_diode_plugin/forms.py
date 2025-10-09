# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Forms."""
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.plugins import get_plugin_config
from utilities.forms.rendering import FieldSet

from netbox_diode_plugin.models import Setting

__all__ = (
    "SettingsForm",
    "ClientCredentialForm",
)


class SettingsForm(forms.ModelForm):
    """Settings form."""

    fieldsets = (
        FieldSet(
            "diode_target",
            "branch",
        ),
    )

    class Meta:
        """Meta class."""

        model = Setting
        fields = ("diode_target", "branch")

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        diode_target_override = get_plugin_config(
            "netbox_diode_plugin", "diode_target_override"
        )

        if diode_target_override:
            self.fields["diode_target"].disabled = True
            self.fields["diode_target"].help_text = (
                "This field is not allowed to be modified."
            )

        # Handle branch field based on netbox_branching plugin availability
        from django.conf import settings as django_settings

        if "netbox_branching" in django_settings.PLUGINS:
            # Branching plugin is installed, configure the branch field
            try:
                from netbox_branching.models import Branch

                self.fields["branch"].queryset = Branch.objects.filter(status="ready")
                self.fields["branch"].required = False
                self.fields["branch"].label = "Branch"
                self.fields["branch"].help_text = (
                    "Select an active branch for Diode ingestion. Leave empty to use the main schema."
                )
            except ImportError:
                # Plugin is in PLUGINS but not actually available, remove the field
                self.fields.pop("branch", None)
        else:
            # Branching plugin is not installed, remove the branch field
            self.fields.pop("branch", None)


class ClientCredentialForm(forms.Form):
    """Form for adding client credentials."""

    client_name = forms.CharField(
        label=_("Client Name"),
        required=True,
        help_text=_("Enter a name for the client credential that will be created for authentication to the Diode ingestion service."),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
