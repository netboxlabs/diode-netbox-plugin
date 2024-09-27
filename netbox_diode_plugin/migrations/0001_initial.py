#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

import os

from django.apps import apps as django_apps
from django.conf import settings as netbox_settings
from django.contrib.contenttypes.management import create_contenttypes
from django.db import migrations, models
from users.models import Token as NetBoxToken

from netbox_diode_plugin.plugin_config import get_diode_usernames


# Read secret from file
def _read_secret(secret_name, default=None):
    try:
        f = open("/run/secrets/" + secret_name, encoding="utf-8")
    except OSError:
        return default
    else:
        with f:
            return f.readline().strip()


def _create_user_with_token(apps, user_category, username, group):
    User = apps.get_model(netbox_settings.AUTH_USER_MODEL)
    """Create a user with the given username and API key if it does not exist."""
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        user = User.objects.create(username=username, is_active=True)

    user.groups.add(*[group.id])

    Token = apps.get_model("users", "Token")

    if not Token.objects.filter(user=user).exists():
        key = f"{user_category.upper()}_API_KEY"
        api_key = _read_secret(key.lower(), os.getenv(key))
        if api_key is None:
            api_key = NetBoxToken.generate_key()
        Token.objects.create(user=user, key=api_key)

    return user


def configure_plugin(apps, schema_editor):
    """Configure the plugin."""
    Group = apps.get_model("users", "Group")
    group, _ = Group.objects.get_or_create(name="diode")

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
    permission.object_types.set([diode_plugin_object_type.id])

    diode_to_netbox_user_id = None

    for user_category, username in get_diode_usernames().items():
        user = _create_user_with_token(apps, user_category, username, group)
        if user_category == "diode_to_netbox":
            diode_to_netbox_user_id = user.id

    permission.users.set([diode_to_netbox_user_id])


class Migration(migrations.Migration):
    """Initial migration."""

    initial = True

    dependencies = [
        ("contenttypes", "0001_initial"),
        ("users", "0006_custom_group_model"),
    ]

    operations = [
        migrations.CreateModel(
            # Does not create any table / fields in the database
            # Registers the Diode model as migrated
            # This model is used to generate permissions for the Diode NetBox Plugin
            name="Diode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
            ],
            options={
                "permissions": (
                    ("view_diode", "Can view Diode"),
                    ("add_diode", "Can apply change sets from Diode"),
                ),
                "managed": False,
                "default_permissions": (),
            },
        ),
        migrations.RunPython(
            code=configure_plugin, reverse_code=migrations.RunPython.noop
        ),
    ]
