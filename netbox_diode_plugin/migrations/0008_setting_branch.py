#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Database migrations."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add optional branch_id field to Setting model."""

    dependencies = [
        ("netbox_diode_plugin", "0007_setting_model_cleanup"),
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
