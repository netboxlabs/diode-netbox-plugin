#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Add branch field to Setting model."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add optional branch field to Setting model."""

    dependencies = [
        ("netbox_diode_plugin", "0001_squashed_0005"),
    ]

    operations = [
        migrations.AddField(
            model_name="setting",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional branch for NetBox Branching plugin integration",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="diode_settings",
                to="netbox_branching.branch",
            ),
        ),
    ]
