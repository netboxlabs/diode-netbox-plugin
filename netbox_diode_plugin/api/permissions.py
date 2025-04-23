#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Permissions."""

from rest_framework.permissions import BasePermission

NETBOX_READ_SCOPE = "netbox:read"
NETBOX_WRITE_SCOPE = "netbox:write"


class IsAuthenticated(BasePermission):
    """Check if the request is authenticated."""

    def has_permission(self, request, view):
        """Check if the request is authenticated."""
        return bool(getattr(request.user, "is_authenticated", False))


class HasScope(BasePermission):
    """
    Require one or more OAuth2 token scopes to access a view.

    Example usage:
        permission_classes = [IsAuthenticated, HasScope("netbox:write")]
    """

    def __init__(self, *required_scopes):
        """Initialize the permission."""
        self.required_scopes = required_scopes

    def has_permission(self, request, view):
        """Check if the request has the required scopes."""
        token_scopes = getattr(request, "token_scopes", [])
        if not token_scopes:
            return False

        return all(scope in token_scopes for scope in self.required_scopes)


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
