#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode Netbox Plugin - URLs."""

from django.urls import path

from . import views

urlpatterns = (
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/edit/", views.SettingsEditView.as_view(), name="settings_edit"),
    path("credentials/", views.ClientCredentialsListView.as_view(), name="client_credentials_list"),
    path(
        "credentials/<int:client_credentials_id>/", 
        views.ClientCredentialsDetailView.as_view(),
        name="client_credentials_detail"
    ),
)
