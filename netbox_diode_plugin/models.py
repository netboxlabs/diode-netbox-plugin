# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Models."""
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import ChangeLoggingMixin, PrimaryModel
from utilities.querysets import RestrictedQuerySet


def diode_target_validator(target):
    """Diode target validator."""
    try:
        parsed_target = urlparse(target)

        if parsed_target.scheme not in ["grpc", "grpcs"]:
            raise ValueError("target should start with grpc:// or grpcs://")
    except ValueError as exc:
        raise ValidationError(exc)


class Setting(models.Model):
    """
    Setting model.

    This model is excluded from branching by not inheriting from ChangeLoggingMixin,
    since it represents global plugin configuration that should not be branched.
    """

    diode_target = models.CharField(max_length=255, validators=[diode_target_validator])
    branch = models.ForeignKey(
        to="netbox_branching.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diode_settings",
        help_text="Optional branch for NetBox Branching plugin integration",
    )

    objects = RestrictedQuerySet.as_manager()

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

    @property
    def branch_schema_id(self):
        """Return the branch schema_id if branch is set."""
        return self.branch.schema_id if self.branch else None


class ClientCredentials(models.Model):
    """Dummy model to allow for permissions, saved filters, etc.."""

    class Meta:
        """Meta class."""

        managed = False

        default_permissions = ()

        permissions = (
            ("view_clientcredentials", "Can view Client Credentials"),
            ("add_clientcredentials", "Can perform actions on Client Credentials"),
        )

