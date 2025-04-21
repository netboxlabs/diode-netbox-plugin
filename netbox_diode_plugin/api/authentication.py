#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - API Authentication."""

import os
import requests
import logging
import hashlib

from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger("netbox.diode_data")

class DiodeOAuth2Authentication(BaseAuthentication):
    """Diode OAuth2 Client Credentials Authentication."""

    def authenticate(self, request):
        """Authenticate the request and return the user info."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:].strip()

        user = self._introspect_token(token)
        if not user:
            raise AuthenticationFailed("Invalid OAuth2 token.")

        return (user, None)

    def _validate_token(self, token: str):
        """Validate the token and return the user info."""
        hash_token = hashlib.sha256(token.encode()).hexdigest()
        cache_key = f"diode:oauth2:introspect:{hash_token}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Load config from environment variables
        # TODO: Move to plugin config
        introspect_url = os.environ.get("OAUTH2_INTROSPECT_URL")

        if not introspect_url:
            logger.error("OAuth2 configuration is missing.")
            return None

        try:
            response = requests.post(
                introspect_url,
                data={"token": token},
                timeout=5
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"OAuth2 introspection failed: {e}")
            return None

        if data.get("active"):
            # Check if token has the required scope for Diode NetBox access
            scopes = data.get("scope", "").split()
            has_diode_to_netbox_scope = any(scope.endswith(":diode:netbox") for scope in scopes)
            
            if not has_diode_to_netbox_scope:
                logger.warning(f"Token missing required :diode:netbox scope. Scopes: {scopes}")
                return None

            # Create an authenticated user-like object
            user_info = type("DiodeOAuth2User", (), {
                "is_authenticated": True,
                "token_data": data
            })()
            expires_in = data.get("exp") - data.get("iat") if "exp" in data and "iat" in data else 300
            cache.set(cache_key, user_info, timeout=expires_in)
            return user_info

        return None
