# !/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Models."""

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from netbox_diode_plugin.reconciler.sdk.client import parse_target


def diode_target_validator(target):
    """Diode target validator."""
    try:
        _, _, _ = parse_target(target)
    except ValueError as exc:
        raise ValidationError(exc)


class Diode(models.Model):
    """Dummy model used to generate permissions for Diode NetBox Plugin. Does not exist in the database."""

    class Meta:
        """Meta class."""

        managed = False

        default_permissions = ()

        permissions = (
            ("view_diode", "Can view Diode"),
            ("add_diode", "Can apply change sets from Diode"),
        )


class Setting(NetBoxModel):
    """Setting model."""

    diode_target = models.CharField(max_length=255, validators=[diode_target_validator])

    class Meta:
        """Meta class."""

        verbose_name = "Settings"
        verbose_name_plural = "Settings"

    def __str__(self):
        """Return string representation."""
        return ""

    def get_absolute_url(self):
        """Return absolute URL."""
        return reverse("plugins:netbox_diode_plugin:settings")



