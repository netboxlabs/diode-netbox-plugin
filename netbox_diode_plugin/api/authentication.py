#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Authentication."""

import hashlib
import logging
from types import SimpleNamespace

import requests
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from netbox_diode_plugin.plugin_config import (
    get_diode_auth_introspect_url,
    get_diode_user,
    get_required_token_audience,
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

        request.user = diode_user.user
        request.token_scopes = diode_user.token_scopes
        request.token_data = diode_user.token_data

        return (diode_user.user, None)

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
            # if the plugin is configured to require specific token audience(s),
            # reject the token if any are missing.
            required_audience = get_required_token_audience()
            if required_audience:
                token_audience = set(data.get("aud", []))
                missing_audience = set(required_audience) - token_audience
                if missing_audience:
                    logger.error(f"Token audience(s) {missing_audience} not found in {token_audience}")
                    return None

            diode_user = SimpleNamespace(
                user=get_diode_user(),
                token_scopes=data.get("scope", "").split(),
                token_data=data,
            )

            expires_in = (
                data.get("exp") - data.get("iat")
                if "exp" in data and "iat" in data
                else 300
            )
            cache.set(cache_key, diode_user, timeout=expires_in)
            return diode_user

        return None
