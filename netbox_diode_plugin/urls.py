#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - URLs."""

from django.urls import path

from . import views

urlpatterns = (
    path("ingestion-logs/", views.IngestionLogsView.as_view(), name="ingestion_logs"),
    path("setup/", views.SetupView.as_view(), name="setup"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/edit/", views.SettingsEditView.as_view(), name="settings_edit"),
)
