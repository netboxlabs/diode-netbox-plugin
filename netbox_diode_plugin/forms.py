# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Forms."""
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelForm
from netbox.plugins import get_plugin_config
from utilities.forms.rendering import FieldSet

from netbox_diode_plugin.models import Setting

__all__ = (
    "SettingsForm",
    "ClientCredentialForm",
)


class SettingsForm(NetBoxModelForm):
    """Settings form."""

    fieldsets = (
        FieldSet(
            "diode_target",
        ),
    )

    class Meta:
        """Meta class."""

        model = Setting
        fields = ("diode_target",)

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


class ClientCredentialForm(forms.Form):
    """Form for adding client credentials."""

    client_name = forms.CharField(
        label=_("Client Name"),
        required=True,
        help_text=_("Enter a name for the client credential that will be created for authentication to the Diode ingestion service."),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
