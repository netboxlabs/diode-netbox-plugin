#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

from django.db import migrations


def clear_diode_group_permissions(apps, schema_editor):
    """Clear Diode group permissions."""
    ObjectPermission = apps.get_model("users", "ObjectPermission")
    permission = ObjectPermission.objects.get(name="Diode")
    permission.groups.clear()


class Migration(migrations.Migration):
    """0003_clear_permissions migration."""

    dependencies = [
        ("netbox_diode_plugin", "0001_initial"),
        ("netbox_diode_plugin", "0002_setting"),
    ]

    operations = [
        migrations.RunPython(
            code=clear_diode_group_permissions, reverse_code=migrations.RunPython.noop
        ),
    ]
