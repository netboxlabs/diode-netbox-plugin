#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API URLs."""

from django.urls import include, path
from netbox.api.routers import NetBoxRouter

from .views import ApplyChangeSetView, GenerateDiffView

router = NetBoxRouter()

urlpatterns = [
    path("apply-change-set/", ApplyChangeSetView.as_view()),
    path("generate-diff/", GenerateDiffView.as_view()),
    path("", include(router.urls)),
]
