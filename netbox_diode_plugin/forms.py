# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Forms."""
from django.conf import settings as netbox_settings
from netbox.forms import NetBoxModelForm
from utilities.forms.rendering import FieldSet

from netbox_diode_plugin.models import Setting

__all__ = ("SettingsForm",)


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

        disallow_diode_target_override = netbox_settings.PLUGINS_CONFIG.get(
            "netbox_diode_plugin", {}
        ).get("disallow_diode_target_override", False)

        if disallow_diode_target_override:
            self.fields["diode_target"].disabled = True
            self.fields["diode_target"].help_text = (
                "This field is not allowed to be overridden."
            )
