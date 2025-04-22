#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode Netbox Plugin - URLs."""

from django.urls import path

from . import views

urlpatterns = (
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/edit/", views.SettingsEditView.as_view(), name="settings_edit"),
)
