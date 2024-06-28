#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - URLs."""

from django.urls import path

from . import views

urlpatterns = (path("audit-log/", views.AuditLogView.as_view(), name="audit_log"),)
