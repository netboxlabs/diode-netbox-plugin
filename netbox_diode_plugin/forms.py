# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Forms."""

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
