#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Permissions."""

from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsDiodeOAuth2Authenticated(BasePermission):
    """Check if the request is authenticated via OAuth2."""

    def has_permission(self, request, view):
        return bool(getattr(request.user, "is_authenticated", False))
