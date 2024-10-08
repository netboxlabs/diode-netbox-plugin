# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Forms."""
from django import forms
from django.core.validators import MinLengthValidator
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelForm
from netbox.plugins import get_plugin_config
from users.models import Token as NetBoxToken
from utilities.forms.rendering import FieldSet

from netbox_diode_plugin.models import Setting

__all__ = (
    "SettingsForm",
    "SetupForm",
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


class SetupForm(forms.Form):
    """Setup form."""

    def __init__(self, users, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        for user_type, user_properties in users.items():
            field_name = f"{user_type}_api_key"
            username_or_type = user_properties.get("username") or user_type
            label = f"{username_or_type}"

            disabled = user_properties.get("user") is not None or (
                user_properties.get("predefined_api_key") is not None
                or user_properties.get("api_key") is not None
            )
            help_text = _(
                f"Key must be at least 40 characters in length.<br />Map to environment variable "
                f'{user_properties["api_key_env_var_name"]} in Diode service'
                f'{" and Diode SDK" if user_type == "diode" else ""}'
            )

            initial_value = user_properties.get("api_key") or user_properties.get(
                "predefined_api_key"
            )

            if (
                user_properties.get("predefined_api_key") is None
                and user_properties.get("api_key") is None
            ):
                initial_value = NetBoxToken.generate_key()

            self.fields[field_name] = forms.CharField(
                required=True,
                max_length=40,
                validators=[MinLengthValidator(40)],
                label=label,
                disabled=disabled,
                initial=initial_value,
                help_text=help_text,
                widget=forms.TextInput(
                    attrs={
                        "data-clipboard": "true",
                        "placeholder": _(
                            f"Enter a valid API key for {username_or_type} user"
                        ),
                    }
                ),
            )
