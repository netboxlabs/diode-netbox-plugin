#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Diode Clients API Tests."""

from unittest import mock

from django.test import TestCase

from netbox_diode_plugin.diode.clients import ClientAPI, ClientAPIError


class DiodeClientsTestCase(TestCase):
    """Test cases for Diode Clients API."""

    def test_create_client(self):
        """Test creating a client."""
        with mock.patch('requests.post') as mock_post:
            client = ClientAPI(
                base_url="http://test-diode-url",
                client_id="test-client-id",
                client_secret="test-client-secret"
            )
            client._client_auth_token = "test-client-auth-token"

            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "client_name": "test-client",
                "scope": "test-scope"
            }

            created = client.create_client(
                name="test-client",
                scope="test-scope"
            )

            self.assertEqual(created, {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "client_name": "test-client",
                "scope": "test-scope"
            })

            mock_post.assert_called_once_with(
                "http://test-diode-url/clients",
                headers={
                    "Authorization": "Bearer test-client-auth-token"
            },
            json={
                "client_name": "test-client",
                "scope": "test-scope"
            }
        )

    def test_list_clients(self):
        """Test listing clients."""
        with mock.patch('requests.get') as mock_get:
            client = ClientAPI(
                base_url="http://test-diode-url",
                client_id="test-client-id",
                client_secret="test-client-secret"
            )
            client._client_auth_token = "test-client-auth-token"

            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "data": [
                    {
                        "client_id": "test-client-id",
                        "client_name": "test-client",
                        "scope": "test-scope"
                    }
                ],
                "next_page_token": "test-next-page-token",
                "prev_page_token": "test-prev-page-token"
            }

            result = client.list_clients(page_size=100)

            self.assertEqual(result["data"], [
                {
                    "client_id": "test-client-id",
                    "client_name": "test-client",
                    "scope": "test-scope"
                }
            ])

            self.assertEqual(result["next_page_token"], "test-next-page-token")
            self.assertEqual(result["prev_page_token"], "test-prev-page-token")

            mock_get.assert_called_once_with(
                "http://test-diode-url/clients",
                headers={
                    "Authorization": "Bearer test-client-auth-token"
                },
                params={
                    "page_size": 100,
                }
            )

    def test_get_client(self):
        """Test getting a client."""
        with mock.patch('requests.get') as mock_get:
            client = ClientAPI(
                base_url="http://test-diode-url",
                client_id="test-client-id",
                client_secret="test-client-secret"
            )
            client._client_auth_token = "test-client-auth-token"

            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "client_id": "test-client-id",
                "client_name": "test-client",
                "scope": "test-scope"
            }

            result = client.get_client("test-client-id")

            self.assertEqual(result, {
                "client_id": "test-client-id",
                "client_name": "test-client",
                "scope": "test-scope"
            })

            mock_get.assert_called_once_with(
                "http://test-diode-url/clients/test-client-id",
                headers={
                    "Authorization": "Bearer test-client-auth-token"
                }
            )

    def test_get_client_raises_error_on_bad_id(self):
        """Test getting a client raises an error on bad ID."""
        client = ClientAPI(
            base_url="http://test-diode-url",
            client_id="test-client-id",
            client_secret="test-client-secret"
        )
        with self.assertRaises(ValueError):
            client.get_client("../bad/../client/id")

    def test_delete_client(self):
        """Test deleting a client."""
        with mock.patch('requests.delete') as mock_delete:
            client = ClientAPI(
                base_url="http://test-diode-url",
                client_id="test-client-id",
                client_secret="test-client-secret"
            )
            client._client_auth_token = "test-client-auth-token"

            mock_delete.return_value.status_code = 204
            mock_delete.return_value.raise_for_status = mock.Mock()

            client.delete_client("test-client-id")

            mock_delete.assert_called_once_with(
                "http://test-diode-url/clients/test-client-id",
                headers={
                    "Authorization": "Bearer test-client-auth-token"
                }
            )

    def test_delete_client_raises_error_on_bad_id(self):
        """Test deleting a client raises an error on bad ID."""
        client = ClientAPI(
            base_url="http://test-diode-url",
            client_id="test-client-id",
            client_secret="test-client-secret"
        )
        with self.assertRaises(ValueError):
            client.delete_client("../bad/../client/id")

    def test_authentication_retries(self):
        """Test authentication retries."""
        with mock.patch('requests.post') as mock_post:
            client = ClientAPI(
                base_url="http://test-diode-url",
                client_id="test-client-id",
                client_secret="test-client-secret"
            )
            client._client_auth_token = "test-client-auth-token"

            mock_post.side_effect = [
                ClientAPIError("Failed to create client", 401),
                mock.Mock(status_code=200, json=lambda: {"access_token": "new-access-token"}),
                mock.Mock(status_code=201, json=lambda: {
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "client_name": "test-client",
                    "scope": "diode:read diode:write"
                }),
            ]

            result = client.create_client("test-client", "diode:read diode:write")
            self.assertEqual(result, {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "client_name": "test-client",
                "scope": "diode:read diode:write"
            })

            self.assertEqual(mock_post.call_count, 3)

            mock_post.assert_has_calls([
                mock.call("http://test-diode-url/clients",
                    headers={
                        "Authorization": "Bearer test-client-auth-token"
                    },
                    json={
                        "client_name": "test-client",
                        "scope": "diode:read diode:write",
                    }
                ),
                mock.call("http://test-diode-url/token",
                    data='grant_type=client_credentials&client_id=test-client-id&client_secret=test-client-secret&scope=diode%3Aread+diode%3Awrite',
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}
                ),
                mock.call("http://test-diode-url/clients",
                    headers={
                        "Authorization": "Bearer new-access-token"
                    },
                    json={
                        "client_name": "test-client",
                        "scope": "diode:read diode:write",
                    }
                ),
            ])


