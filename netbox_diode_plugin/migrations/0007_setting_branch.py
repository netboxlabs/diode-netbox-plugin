#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Add branch_id field to Setting model."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add optional branch_id field to Setting model."""

    dependencies = [
        ("netbox_diode_plugin", "0001_squashed_0005", "0006_clientcredentials_alter_setting_diode_target"),
    ]

    operations = [
        migrations.AddField(
            model_name="setting",
            name="branch_id",
            field=models.BigIntegerField(
                blank=True,
                null=True,
                help_text="ID of the branch for NetBox Branching plugin integration",
            ),
        ),
    ]
