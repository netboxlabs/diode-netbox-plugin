#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Permissions."""

from rest_framework.permissions import BasePermission

SCOPE_NETBOX_READ = "netbox:read"
SCOPE_NETBOX_WRITE = "netbox:write"


class IsAuthenticated(BasePermission):
    """Check if the request is authenticated."""

    def has_permission(self, request, view):
        """Check if the request is authenticated."""
        return bool(getattr(request.user, "is_authenticated", False))


def require_scopes(*required_scopes):
    """Require one or more OAuth2 token scopes to access a view."""

    class ScopedPermission(BasePermission):
        """Check if the request has the required scopes."""

        def has_permission(self, request, view):
            """Check if the request has the required scopes."""
            scopes = getattr(request, "token_scopes", [])
            return all(scope in scopes for scope in required_scopes)

    ScopedPermission.__name__ = f"RequireScopes_{'_'.join(required_scopes)}"
    return ScopedPermission
