# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Models."""
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel


def diode_target_validator(target):
    """Diode target validator."""
    try:
        parsed_target = urlparse(target)

        if parsed_target.scheme not in ["grpc", "grpcs"]:
            raise ValueError("target should start with grpc:// or grpcs://")
    except ValueError as exc:
        raise ValidationError(exc)


class Setting(NetBoxModel):
    """Setting model."""

    diode_target = models.CharField(max_length=255, validators=[diode_target_validator])
    tags = None

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


class UnmanagedModelManager(models.Manager):
    """Manager for unmanaged models that prevents database queries."""

    def get_queryset(self):
        """Return an empty queryset without hitting the database."""
        return super().get_queryset().none()


class ClientCredentials(models.Model):
    """Dummy model to allow for permissions, saved filters, etc.."""

    objects = UnmanagedModelManager()

    class Meta:
        """Meta class."""

        managed = False

        default_permissions = ()

        permissions = (
            ("view_clientcredentials", "Can view Client Credentials"),
            ("add_clientcredentials", "Can perform actions on Client Credentials"),
        )

