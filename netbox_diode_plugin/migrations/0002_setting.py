#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Database migrations."""

import utilities.json
from django.conf import settings as netbox_settings
from django.db import migrations, models


def create_settings_entity(apps, schema_editor):
    """Create a Setting entity."""
    Setting = apps.get_model("netbox_diode_plugin", "Setting")

    diode_target = netbox_settings.PLUGINS_CONFIG.get(
        "netbox_diode_plugin", {}
    ).get("diode_target", "grpc://localhost:8080/diode")

    Setting.objects.create(diode_target=diode_target)


class Migration(migrations.Migration):
    """0002_setting migration."""

    dependencies = [
        ("netbox_diode_plugin", "0001_initial"),
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
