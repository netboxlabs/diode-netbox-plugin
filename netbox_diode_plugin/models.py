# !/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Models."""
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
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

    Simple model without change logging, excluded from branching.
    """

    diode_target = models.CharField(max_length=255, validators=[diode_target_validator])
    branch_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="ID of the branch for NetBox Branching plugin integration",
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
    def branch(self):
        """
        Return the Branch object if branch_id is set and branching plugin is installed.

        Returns None if:
        - branch_id is not set
        - branching plugin is not installed
        - branch with given ID does not exist
        """
        if not self.branch_id:
            return None

        try:
            from netbox_branching.models import Branch
            return Branch.objects.get(id=self.branch_id)
        except (ImportError, Exception):
            return None

    @branch.setter
    def branch(self, branch_obj):
        """Set branch_id from a Branch object."""
        if branch_obj is None:
            self.branch_id = None
        else:
            self.branch_id = branch_obj.id

    @property
    def branch_schema_id(self):
        """Return the branch schema_id if branch is set."""
        branch = self.branch
        return branch.schema_id if branch else None


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

