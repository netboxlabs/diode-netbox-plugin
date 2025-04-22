#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Authentication."""

import hashlib
import logging

import requests
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from netbox_diode_plugin.plugin_config import (
    get_diode_auth_introspect_url,
    get_diode_user,
)

logger = logging.getLogger("netbox.diode_data")


class DiodeOAuth2Authentication(BaseAuthentication):
    """Diode OAuth2 Client Credentials Authentication."""

    def authenticate(self, request):
        """Authenticate the request and return the user info."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:].strip()

        diode_user = self._introspect_token(token)
        if not diode_user:
            raise AuthenticationFailed("Invalid token")

        return (diode_user, None)

    def _introspect_token(self, token: str):
        """Introspect the token and return the client info."""
        hash_token = hashlib.sha256(token.encode()).hexdigest()
        cache_key = f"diode:oauth2:introspect:{hash_token}"
        cached_user = cache.get(cache_key)
        if cached_user:
            return cached_user

        introspect_url = get_diode_auth_introspect_url()

        if not introspect_url:
            logger.error("Diode Auth introspect URL is not configured")
            return None

        try:
            response = requests.post(
                introspect_url, headers={"Authorization": f"Bearer {token}"}, timeout=5
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Diode Auth token introspection failed: {e}")
            return None

        if data.get("active"):
            # Check if token has the required scope
            scopes = data.get("scope", "").split()
            has_diode_to_netbox_scope = any(
                scope.endswith(":diode:netbox") for scope in scopes
            )

            if not has_diode_to_netbox_scope:
                logger.warning(
                    f"Diode Auth token with insufficient scopes: {scopes}"
                )
                return None

            diode_user = get_diode_user()

            expires_in = (
                data.get("exp") - data.get("iat")
                if "exp" in data and "iat" in data
                else 300
            )
            cache.set(cache_key, diode_user, timeout=expires_in)
            return diode_user

        return None
