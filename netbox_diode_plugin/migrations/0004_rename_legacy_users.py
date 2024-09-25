#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

from django.db import migrations

from netbox_diode_plugin.plugin_config import get_diode_usernames


def rename_legacy_users(apps, schema_editor):
    """Rename legacy users."""
    legacy_usernames_to_user_category_map = {
        "DIODE_TO_NETBOX": "diode_to_netbox",
        "NETBOX_TO_DIODE": "netbox_to_diode",
        "DIODE": "diode",
    }

    User = apps.get_model("users", "User")
    users = User.objects.filter(
        username__in=legacy_usernames_to_user_category_map.keys(),
        groups__name="diode",
    )

    for user in users:
        user_category = legacy_usernames_to_user_category_map.get(user.username)
        user.username = get_diode_usernames().get(user_category)
        user.save()


class Migration(migrations.Migration):
    """0004_rename_legacy_users migration."""

    dependencies = [
        ("netbox_diode_plugin", "0001_initial"),
        ("netbox_diode_plugin", "0002_setting"),
        ("netbox_diode_plugin", "0003_clear_permissions"),
    ]

    operations = [
        migrations.RunPython(
            code=rename_legacy_users, reverse_code=migrations.RunPython.noop
        ),
    ]
