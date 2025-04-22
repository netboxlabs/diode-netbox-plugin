#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin - Authentication Tests."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.plugin_config import get_diode_user


class DiodeOAuth2AuthenticationTestCase(TestCase):
    """Test cases for DiodeOAuth2Authentication."""

    def setUp(self):
        """Set up test case."""
        self.auth = DiodeOAuth2Authentication()
        self.factory = APIRequestFactory()
        self.diode_user = get_diode_user()
        self.valid_token = "valid_oauth_token"
        self.invalid_token = "invalid_oauth_token"
        self.token_without_scope = "token_without_scope"
        self.token_with_scope = "token_with_scope"

        # Mock the cache
        self.cache_patcher = mock.patch.object(cache, 'get')
        self.cache_get_mock = self.cache_patcher.start()
        self.cache_set_patcher = mock.patch.object(cache, 'set')
        self.cache_set_mock = self.cache_set_patcher.start()

        # Mock requests.post for token introspection
        self.requests_patcher = mock.patch('requests.post')
        self.requests_mock = self.requests_patcher.start()
        self.requests_mock.return_value.raise_for_status = mock.Mock()

        # Mock get_diode_auth_introspect_url
        self.introspect_url_patcher = mock.patch(
            'netbox_diode_plugin.plugin_config.get_diode_auth_introspect_url',
            return_value='http://test-introspect-url'
        )
        self.introspect_url_patcher.start()

    def tearDown(self):
        """Clean up after tests."""
        self.cache_patcher.stop()
        self.cache_set_patcher.stop()
        self.requests_patcher.stop()
        self.introspect_url_patcher.stop()

    def test_authenticate_no_auth_header(self):
        """Test authentication with no Authorization header."""
        request = self.factory.get('/')
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_authenticate_invalid_auth_header_format(self):
        """Test authentication with invalid Authorization header format."""
        request = self.factory.get('/', HTTP_AUTHORIZATION='InvalidFormat')
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_authenticate_cached_token(self):
        """Test authentication with cached token."""
        self.cache_get_mock.return_value = self.diode_user
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.valid_token}')

        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.diode_user)
        self.cache_get_mock.assert_called_once()

    def test_authenticate_invalid_token(self):
        """Test authentication with invalid token."""
        self.cache_get_mock.return_value = None
        self.requests_mock.return_value.json.return_value = {'active': False}

        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.invalid_token}')

        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_authenticate_token_without_required_scope(self):
        """Test authentication with token missing required scope."""
        self.cache_get_mock.return_value = None
        self.requests_mock.return_value.json.return_value = {
            'active': True,
            'scope': 'other:scope'
        }

        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.token_without_scope}')

        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_authenticate_token_with_required_scope(self):
        """Test authentication with token having required scope."""
        self.cache_get_mock.return_value = None
        self.requests_mock.return_value.json.return_value = {
            'active': True,
            'scope': 'default:diode:netbox',
            'exp': 1000,
            'iat': 500
        }

        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.token_with_scope}')

        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.diode_user)
        self.cache_set_mock.assert_called_once()

    def test_authenticate_token_introspection_failure(self):
        """Test authentication when token introspection fails."""
        self.cache_get_mock.return_value = None
        self.requests_mock.side_effect = Exception("Introspection failed")

        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.valid_token}')

        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_authenticate_token_with_default_expiry(self):
        """Test authentication with token having no expiry information."""
        self.cache_get_mock.return_value = None
        self.requests_mock.return_value.json.return_value = {
            'active': True,
            'scope': 'default:diode:netbox'
        }

        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {self.token_with_scope}')

        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.diode_user)

        self.cache_set_mock.assert_called_once()

        # Get the actual call arguments
        call_args = self.cache_set_mock.call_args
        if not call_args:
            self.fail("Cache set was not called with any arguments")

        # The cache key should start with 'diode:oauth2:introspect:'
        cache_key = call_args.args[0]
        self.assertTrue(cache_key.startswith('diode:oauth2:introspect:'))

        # The cached value should be the diode user
        self.assertEqual(call_args.args[1], self.diode_user)

        # The timeout should be 300 (default)
        self.assertEqual(call_args.kwargs['timeout'], 300)
