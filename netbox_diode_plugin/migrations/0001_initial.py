#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

import os

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.management import create_contenttypes
from django.db import migrations


def _create_user_with_token(apps, username, group, is_superuser: bool = False):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    """Create a user with the given username and API key if it does not exist."""
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        if is_superuser:
            user = User.objects.create_superuser(username=username, is_active=True)
        else:
            user = User.objects.create(username=username, is_active=True)

    user.groups.add(*[group.id])

    Token = apps.get_model("users", "Token")

    if not Token.objects.filter(user=user).exists():
        Token.objects.create(user=user, key=os.getenv(f"{username}_API_KEY"))

    return user


def configure_plugin(apps, schema_editor):
    """Configure the plugin."""
    diode_to_netbox_username = "DIODE_TO_NETBOX"
    netbox_to_diode_username = "NETBOX_TO_DIODE"
    diode_username = "DIODE"

    Group = apps.get_model("auth", "Group")
    group, _ = Group.objects.get_or_create(name="diode")

    diode_to_netbox_user = _create_user_with_token(
        apps, diode_to_netbox_username, group
    )
    _ = _create_user_with_token(apps, netbox_to_diode_username, group, True)
    _ = _create_user_with_token(apps, diode_username, group)

    app_config = django_apps.get_app_config("netbox_diode_plugin")

    create_contenttypes(app_config, verbosity=0)

    ContentType = apps.get_model("contenttypes", "ContentType")

    diode_plugin_object_type = ContentType.objects.get(
        app_label="netbox_diode_plugin", model="diode"
    )

    ObjectPermission = apps.get_model("users", "ObjectPermission")
    permission, _ = ObjectPermission.objects.get_or_create(
        name="Diode",
        actions=["add", "view"],
    )

    permission.groups.set([group])
    permission.users.set([diode_to_netbox_user])
    permission.object_types.set([diode_plugin_object_type])


class Migration(migrations.Migration):
    """Initial migration."""

    initial = True

    dependencies = [
        ("contenttypes", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            code=configure_plugin, reverse_code=migrations.RunPython.noop
        ),
    ]
