#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Configure Diode plugin command."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from packaging import version
from users.models import Group, ObjectPermission, Token

if version.parse(settings.VERSION).major >= 4:
    from core.models import ObjectType as NetBoxType
else:
    from django.contrib.contenttypes.models import ContentType as NetBoxType

User = get_user_model()


def _create_user_with_token(
    username: str, group: Group, is_superuser: bool = False
) -> User:
    """Create a user with the given username and API key if it does not exist."""
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        if is_superuser:
            user = User.objects.create_superuser(username=username, is_active=True)
        else:
            user = User.objects.create(username=username, is_active=True)

    user.groups.add(*[group.id])

    if not Token.objects.filter(user=user).exists():
        Token.objects.create(user=user, key=os.getenv(f"{username}_API_KEY"))

    return user


class Command(BaseCommand):
    """Configure NetBox Diode plugin."""

    help = "Configure NetBox Diode plugin"

    diode_to_netbox_username = "DIODE_TO_NETBOX"
    netbox_to_diode_username = "NETBOX_TO_DIODE"
    diode_username = "DIODE"

    def handle(self, *args, **options):
        """Handle command execution."""
        self.stdout.write("Configuring NetBox Diode plugin...")

        group, _ = Group.objects.get_or_create(name="diode")

        diode_to_netbox_user = _create_user_with_token(
            self.diode_to_netbox_username, group
        )
        _ = _create_user_with_token(self.netbox_to_diode_username, group, True)
        _ = _create_user_with_token(self.diode_username, group)

        diode_plugin_object_type = NetBoxType.objects.get(
            app_label="netbox_diode_plugin", model="diode"
        )

        permission, _ = ObjectPermission.objects.get_or_create(
            name="Diode",
            actions=["add", "view"],
        )

        permission.groups.set([group])
        permission.users.set([diode_to_netbox_user])
        permission.object_types.set([diode_plugin_object_type])

        self.stdout.write("Finished.")
