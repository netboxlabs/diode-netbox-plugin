#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API URLs."""

from django.urls import include, path
from netbox.api.routers import NetBoxRouter

from .views import (
    ApplyChangeSetView,
    BulkApplyView,
    BulkPlanApplyView,
    BulkPlanView,
    GenerateDiffView,
    GetDefaultBranchView,
)

router = NetBoxRouter()

urlpatterns = [
    path("apply-change-set/", ApplyChangeSetView.as_view()),
    path("bulk-apply/", BulkApplyView.as_view()),
    path("bulk-plan/", BulkPlanView.as_view()),
    path("bulk-plan-apply/", BulkPlanApplyView.as_view()),
    path("generate-diff/", GenerateDiffView.as_view()),
    path("default-branch/", GetDefaultBranchView.as_view()),
    path("", include(router.urls)),
]
