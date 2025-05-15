#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode Netbox Plugin - URLs."""

from django.urls import path

from . import views

urlpatterns = (
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/edit/", views.SettingsEditView.as_view(), name="settings_edit"),
    path("credentials/", views.ClientCredentialListView.as_view(), name="client_credential_list"),
    path("credentials/add/", views.ClientCredentialAddView.as_view(), name="client_credential_add"),
    path("credentials/secret/", views.ClientCredentialSecretView.as_view(), name="client_credential_secret"),
    path("credentials/delete/<str:client_credential_id>/", views.ClientCredentialDeleteView.as_view(), name="client_credential_delete"),
)
