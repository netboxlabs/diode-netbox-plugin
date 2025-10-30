#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Database migrations."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Clean up Setting model by removing unused fields."""

    dependencies = [
        ("netbox_diode_plugin", "0006_clientcredentials_alter_setting_diode_target"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="setting",
            name="custom_field_data",
        ),
        migrations.RemoveField(
            model_name='setting',
            name='created',
        ),
        migrations.RemoveField(
            model_name='setting',
            name='last_updated',
        ),
    ]
