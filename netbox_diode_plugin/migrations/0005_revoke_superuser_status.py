#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

from django.db import migrations

from netbox_diode_plugin.plugin_config import get_diode_usernames


def revoke_superuser_status(apps, schema_editor):
    """Revoke superuser status."""
    diode_usernames = get_diode_usernames().values()
    User = apps.get_model("users", "User")
    users = User.objects.filter(username__in=diode_usernames, groups__name="diode")

    for user in users:
        user.is_staff = False
        user.is_superuser = False
        user.save()


class Migration(migrations.Migration):
    """0005_revoke_superuser_status migration."""

    dependencies = [
        ("netbox_diode_plugin", "0001_initial"),
        ("netbox_diode_plugin", "0002_setting"),
        ("netbox_diode_plugin", "0003_clear_permissions"),
        ("netbox_diode_plugin", "0004_rename_legacy_users"),
    ]

    operations = [
        migrations.RunPython(
            code=revoke_superuser_status, reverse_code=migrations.RunPython.noop
        ),
    ]
