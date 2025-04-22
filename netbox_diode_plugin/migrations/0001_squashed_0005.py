#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Database migrations."""

import utilities.json
from django.db import migrations, models
from netbox.plugins import get_plugin_config


def create_settings_entity(apps, schema_editor):
    """Create a Setting entity."""
    Setting = apps.get_model("netbox_diode_plugin", "Setting")

    default_diode_target = get_plugin_config("netbox_diode_plugin", "diode_target")
    diode_target = get_plugin_config(
        "netbox_diode_plugin", "diode_target_override", default_diode_target
    )

    Setting.objects.create(diode_target=diode_target)


class Migration(migrations.Migration):
    """Initial migration."""

    replaces = [
        ("netbox_diode_plugin", "0001_initial"),
        ("netbox_diode_plugin", "0002_setting"),
        ("netbox_diode_plugin", "0003_clear_permissions"),
        ("netbox_diode_plugin", "0004_rename_legacy_users"),
        ("netbox_diode_plugin", "0005_revoke_superuser_status"),
    ]

    initial = True

    dependencies = [
        ("contenttypes", "0001_initial"),
        ("users", "0006_custom_group_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="Setting",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                ("diode_target", models.CharField(max_length=255)),
            ],
            options={
                "verbose_name": "Setting",
                "verbose_name_plural": "Diode Settings",
            },
        ),
        migrations.RunPython(
            code=create_settings_entity, reverse_code=migrations.RunPython.noop
        ),
    ]
