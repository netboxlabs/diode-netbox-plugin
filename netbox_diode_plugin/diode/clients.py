#!/usr/bin/env python
# Copyright 2025 NetBox Labs Inc
"""Diode NetBox Plugin - Diode - Auth."""

import datetime
import json
import logging
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from netbox_diode_plugin.plugin_config import (
    get_diode_auth_base_url,
    get_diode_credentials,
    get_diode_max_auth_retries,
)

SCOPE_DIODE_READ = "diode:read"
SCOPE_DIODE_WRITE = "diode:write"

logger = logging.getLogger("netbox.diode_data")

valid_client_id_re = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_client = None
_client_lock = threading.Lock()
def get_api_client():
    """Get the client API client."""
    global _client
    global _client_lock

    with _client_lock:
        if _client is None:
            client_id, client_secret = get_diode_credentials()
            if not client_id:
                raise ClientAPIError(
                    "Please update the plugin configuration to access this feature.\nMissing netbox to diode client id.", 500)
            if not client_secret:
                raise ClientAPIError(
                    "Please update the plugin configuration to access this feature.\nMissing netbox to diode client secret.", 500)
            max_auth_retries = get_diode_max_auth_retries()
            _client = ClientAPI(
                base_url=get_diode_auth_base_url(),
                client_id=client_id,
                client_secret=client_secret,
                max_auth_retries=max_auth_retries,
            )
        return _client


class ClientAPIError(Exception):
    """Client API Error."""

    def __init__(self, message: str, status_code: int = 500):
        """Initialize the ClientAPIError."""
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

    def is_auth_error(self) -> bool:
        """Check if the error is an authentication error."""
        return self.status_code == 401 or self.status_code == 403

class ClientAPI:
    """Manages Diode Clients."""

    def __init__(self, base_url: str, client_id: str, client_secret: str, max_auth_retries: int = 2):
        """Initialize the ClientAPI."""
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret

        self._max_auth_retries = max_auth_retries
        self._client_auth_token = None
        self._client_auth_token_lock = threading.Lock()

    def create_client(self, name: str, scope: str) -> dict:
        """Create a client."""
        for attempt in range(self._max_auth_retries):
            token = None
            try:
                token = self._get_token()
                url = self.base_url + "/clients"
                headers = {"Authorization": f"Bearer {token}"}
                data = {
                    "client_name": name,
                    "scope": scope,
                }
                response = requests.post(url, json=data, headers=headers)
                if response.status_code != 201:
                    raise ClientAPIError("Failed to create client", response.status_code)
                return response.json()
            except ClientAPIError as e:
                if e.is_auth_error() and attempt < self._max_auth_retries - 1:
                    logger.info(f"Retrying create_client due to unauthenticated error, attempt {attempt + 1}")
                    self._mark_client_auth_token_invalid(token)
                    continue
                raise
        raise ClientAPIError("Failed to create client: unexpected state", 500)

    def get_client(self, client_id: str) -> dict:
        """Get a client."""
        if not valid_client_id_re.match(client_id):
            raise ValueError(f"Invalid client ID: {client_id}")

        for attempt in range(self._max_auth_retries):
            token = None
            try:
                token = self._get_token()
                url = self.base_url + f"/clients/{client_id}"
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.get(url, headers=headers)
                if response.status_code == 401 or response.status_code == 403:
                    raise ClientAPIError(f"Failed to get client {client_id}", response.status_code)
                if response.status_code != 200:
                    raise ClientAPIError(f"Failed to get client {client_id}", response.status_code)
                return response.json()
            except ClientAPIError as e:
                if e.is_auth_error() and attempt < self._max_auth_retries - 1:
                    logger.info(f"Retrying delete_client due to unauthenticated error, attempt {attempt + 1}")
                    self._mark_client_auth_token_invalid(token)
                    continue
                raise
        raise ClientAPIError(f"Failed to get client {client_id}: unexpected state")

    def delete_client(self, client_id: str) -> None:
        """Delete a client."""
        if not valid_client_id_re.match(client_id):
            raise ValueError(f"Invalid client ID: {client_id}")

        for attempt in range(self._max_auth_retries):
            token = None
            try:
                token = self._get_token()
                url = self.base_url + f"/clients/{client_id}"
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.delete(url, headers=headers)
                if response.status_code != 204:
                    raise ClientAPIError(f"Failed to delete client {client_id}", response.status_code)
                return
            except ClientAPIError as e:
                if e.is_auth_error() and attempt < self._max_auth_retries - 1:
                    logger.info(f"Retrying delete_client due to unauthenticated error, attempt {attempt + 1}")
                    self._mark_client_auth_token_invalid(token)
                    continue
                raise
        raise ClientAPIError(f"Failed to delete client {client_id}: unexpected state")


    def list_clients(self, page_token: str | None = None, page_size: int | None = None) -> list[dict]:
        """List all clients."""
        for attempt in range(self._max_auth_retries):
            token = None
            try:
                token = self._get_token()
                url = self.base_url + "/clients"
                headers = {"Authorization": f"Bearer {token}"}
                params = {}
                if page_token:
                    params["page_token"] = page_token
                if page_size:
                    params["page_size"] = page_size
                response = requests.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    raise ClientAPIError("Failed to get clients", response.status_code)
                return response.json()
            except ClientAPIError as e:
                if e.is_auth_error() and attempt < self._max_auth_retries - 1:
                    logger.info(f"Retrying list_clients due to unauthenticated error, attempt {attempt + 1}")
                    self._mark_client_auth_token_invalid(token)
                    continue
                raise
        raise ClientAPIError("Failed to list clients: unexpected state")


    def _get_token(self) -> str:
        """Get a token for the Diode Auth Service."""
        with self._client_auth_token_lock:
            if self._client_auth_token:
                return self._client_auth_token
            self._client_auth_token = self._authenticate()
            return self._client_auth_token

    def _mark_client_auth_token_invalid(self, token: str):
        """Mark a client auth token as invalid."""
        with self._client_auth_token_lock:
            self._client_auth_token = None

    def _authenticate(self) -> str:
        """Get a new access token for the Diode Auth Service."""
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": f"{SCOPE_DIODE_READ} {SCOPE_DIODE_WRITE}",
            }
        )
        url = self.base_url + "/token"
        try:
            response = requests.post(url, data=data, headers=headers)
        except Exception as e:
            raise ClientAPIError(f"Failed to obtain access token: {e}", 401) from e
        if response.status_code != 200:
            raise ClientAPIError(f"Failed to obtain access token: {response.reason}", 401)

        try:
            token_info = response.json()
        except Exception as e:
            raise ClientAPIError(f"Failed to parse access token response: {e}", 401) from e

        access_token = token_info.get("access_token")
        if not access_token:
            raise ClientAPIError(f"Failed to obtain access token for client {self._client_id}", 401)

        return access_token

