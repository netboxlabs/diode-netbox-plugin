#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Create ClientCredentials model and update Setting model."""

import netbox_diode_plugin.models
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create ClientCredentials and update Setting model."""

    dependencies = [
        ("netbox_diode_plugin", "0002_setting_branch"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientCredentials",
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
                    ("view_clientcredentials", "Can view Client Credentials"),
                    ("add_clientcredentials", "Can perform actions on Client Credentials"),
                ),
                "managed": False,
                "default_permissions": (),
            },
        ),
        migrations.AlterField(
            model_name="setting",
            name="diode_target",
            field=models.CharField(
                max_length=255,
                validators=[netbox_diode_plugin.models.diode_target_validator],
            ),
        ),
        migrations.RemoveField(
            model_name="setting",
            name="custom_field_data",
        ),
    ]
