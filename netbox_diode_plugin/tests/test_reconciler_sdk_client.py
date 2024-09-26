#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""NetBox Labs - Tests."""
from unittest import mock

import grpc
import pytest
from django.test import TestCase

import netbox_diode_plugin
from netbox_diode_plugin.reconciler.sdk.client import (
    ReconcilerClient,
    ReconcilerMethodClientInterceptor,
    _ClientCallDetails,
    _load_certs,
    parse_target,
)
from netbox_diode_plugin.reconciler.sdk.exceptions import ReconcilerClientError


class ReconcilerSDKClientTestCase(TestCase):
    """Test case for the Reconciler SDK client."""

    def test_init(self):
        """Check we can initiate a client configuration."""
        client = ReconcilerClient(
            target="grpc://localhost:8081",
            api_key="foobar",
        )

        plugin_config = netbox_diode_plugin.config

        assert client.target == "localhost:8081"
        assert client.name == "reconciler-sdk-python"
        assert client.version == "0.0.1"
        assert client.app_name == plugin_config.name
        assert client.app_version == plugin_config.version
        assert client.tls_verify is False
        assert client.path == ""


    def test_client_error(self):
        """Check we can raise a client error."""
        with pytest.raises(ReconcilerClientError) as err:
            client = ReconcilerClient(
                target="grpc://invalid:8089",
                api_key="foobar",
            )
            client.retrieve_ingestion_logs()

        assert err.value.status_code == grpc.StatusCode.UNAVAILABLE.name
        assert "DNS resolution failed for invalid:8089" in err.value.details


    def test_client_error_non_grpc_status_code(self):
        """Check we can raise a client error."""
        with pytest.raises(ReconcilerClientError) as err:
            rpc_error = grpc.RpcError()
            rpc_error.code = lambda: "NON_GRPC_STATUS_CODE"
            rpc_error.details = lambda: "Some details about the error"
            raise ReconcilerClientError(rpc_error)

        assert not isinstance(err.value.status_code, grpc.StatusCode)
        assert err.value.status_code == "NON_GRPC_STATUS_CODE"
        assert err.value.details == "Some details about the error"


    def test_client_error_repr_returns_correct_string(self):
        """Check we can return the correct string representation of the error."""
        grpc_error = grpc.RpcError()
        grpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        grpc_error.details = lambda: "Some details about the error"
        error = ReconcilerClientError(grpc_error)
        error._status_code = grpc.StatusCode.UNAVAILABLE
        error._details = "Some details about the error"
        assert (
            repr(error)
            == "<ReconcilerClientError status code: StatusCode.UNAVAILABLE, details: Some details about the error>"
        )


    def test_load_certs_returns_bytes(self):
        """Check that _load_certs returns bytes."""
        assert isinstance(_load_certs(), bytes)


    def test_parse_target_handles_http_prefix(self):
        """Check that parse_target raises an error when the target contains http://."""
        with pytest.raises(ValueError):
            parse_target("http://localhost:8081")


    def test_parse_target_handles_https_prefix(self):
        """Check that parse_target raises an error when the target contains https://."""
        with pytest.raises(ValueError):
            parse_target("https://localhost:8081")


    def test_parse_target_parses_authority_correctly(self):
        """Check that parse_target parses the authority correctly."""
        authority, path, tls_verify = parse_target("grpc://localhost:8081")
        assert authority == "localhost:8081"
        assert path == ""
        assert tls_verify is False


    def test_parse_target_adds_default_port_if_missing(self):
        """Check that parse_target adds the default port if missing."""
        authority, _, _ = parse_target("grpc://localhost")
        assert authority == "localhost:443"


    def test_parse_target_parses_path_correctly(self):
        """Check that parse_target parses the path correctly."""
        _, path, _ = parse_target("grpc://localhost:8081/my/path")
        assert path == "/my/path"


    def test_parse_target_handles_no_path(self):
        """Check that parse_target handles no path."""
        _, path, _ = parse_target("grpc://localhost:8081")
        assert path == ""


    def test_parse_target_parses_tls_verify_correctly(self):
        """Check that parse_target parses tls_verify correctly."""
        _, _, tls_verify = parse_target("grpcs://localhost:8081")
        assert tls_verify is True


    def test_client_sets_up_secure_channel_when_grpcs_scheme_is_found_in_target(self):
        """Check that ReconcilerClient.__init__() sets up the gRPC secure channel when grpcs:// scheme is found in the target."""
        with (
            mock.patch("grpc.secure_channel") as mock_secure_channel,
            mock.patch("logging.Logger.debug") as mock_debug,
        ):
            _ = ReconcilerClient(
                target="grpcs://localhost:8081",
                api_key="foobar",
            )

            mock_debug.assert_called_once_with("Setting up gRPC secure channel")
            mock_secure_channel.assert_called_once()


    def test_client_sets_up_insecure_channel_when_grpc_scheme_is_found_in_target(self):
        """Check that ReconcilerClient.__init__() sets up the gRPC insecure channel when grpc:// scheme is found in the target."""
        with (
            mock.patch("grpc.insecure_channel") as mock_insecure_channel,
            mock.patch("logging.Logger.debug") as mock_debug,
        ):
            _ = ReconcilerClient(
                target="grpc://localhost:8081",
                api_key="foobar",
            )

            mock_debug.assert_called_with(
                "Setting up gRPC insecure channel",
            )
            mock_insecure_channel.assert_called_once()


    def test_insecure_channel_options_with_primary_user_agent(self):
        """Check that ReconcilerClient.__init__() sets the gRPC primary_user_agent option for insecure channel."""
        with mock.patch("grpc.insecure_channel") as mock_insecure_channel:
            client = ReconcilerClient(
                target="grpc://localhost:8081",
                api_key="abcde",
            )

            mock_insecure_channel.assert_called_once()
            _, kwargs = mock_insecure_channel.call_args
            assert kwargs["options"] == (
                (
                    "grpc.primary_user_agent",
                    f"{client.name}/{client.version} {client.app_name}/{client.app_version}",
                ),
            )


    def test_secure_channel_options_with_primary_user_agent(self):
        """Check that ReconcilerClient.__init__() sets the gRPC primary_user_agent option for secure channel."""
        with mock.patch("grpc.secure_channel") as mock_secure_channel:
            client = ReconcilerClient(
                target="grpcs://localhost:8081",
                api_key="abcde",
            )

            mock_secure_channel.assert_called_once()
            _, kwargs = mock_secure_channel.call_args
            assert kwargs["options"] == (
                (
                    "grpc.primary_user_agent",
                    f"{client.name}/{client.version} {client.app_name}/{client.app_version}",
                ),
            )

    def test_client_interceptor_setup_with_path(self):
        """Check that ReconcilerClient.__init__() sets up the gRPC interceptor when a path is provided."""
        with (
            mock.patch("grpc.intercept_channel") as mock_intercept_channel,
            mock.patch("logging.Logger.debug") as mock_debug,
        ):
            _ = ReconcilerClient(
                target="grpcs://localhost:8081/my-path",
                api_key="foobar",
            )

            mock_debug.assert_called_with(
                "Setting up gRPC interceptor for path: /my-path",
            )
            mock_intercept_channel.assert_called_once()


    def test_client_interceptor_not_setup_without_path(self):
        """Check that ReconcilerClient.__init__() does not set up the gRPC interceptor when no path is provided."""
        with (
            mock.patch("grpc.intercept_channel") as mock_intercept_channel,
            mock.patch("logging.Logger.debug") as mock_debug,
        ):
            _ = ReconcilerClient(
                target="grpc://localhost:8081",
                api_key="foobar",
            )

            mock_debug.assert_called_with(
                "Setting up gRPC insecure channel",
            )
            mock_intercept_channel.assert_not_called()


    def test_client_properties_return_expected_values(self):
        """Check that ReconcilerClient properties return the expected values."""
        client = ReconcilerClient(
            target="grpc://localhost:8081",
            api_key="foobar",
        )

        plugin_config = netbox_diode_plugin.config

        assert client.target == "localhost:8081"
        assert client.name == "reconciler-sdk-python"
        assert client.version == "0.0.1"
        assert client.app_name == plugin_config.name
        assert client.app_version == plugin_config.version
        assert client.tls_verify is False
        assert client.path == ""
        assert isinstance(client.channel, grpc.Channel)


    def test_client_enter_returns_self(self):
        """Check that ReconcilerClient.__enter__() returns self."""
        client = ReconcilerClient(
            target="grpc://localhost:8081",
            api_key="foobar",
        )
        assert client.__enter__() is client


    def test_client_exit_closes_channel(self):
        """Check that ReconcilerClient.__exit__() closes the channel."""
        client = ReconcilerClient(
            target="grpc://localhost:8081",
            api_key="foobar",
        )
        with mock.patch.object(client._channel, "close") as mock_close:
            client.__exit__(None, None, None)
            mock_close.assert_called_once()


    def test_client_close_closes_channel(self):
        """Check that ReconcilerClient.close() closes the channel."""
        client = ReconcilerClient(
            target="grpc://localhost:8081",
            api_key="foobar",
        )
        with mock.patch.object(client._channel, "close") as mock_close:
            client.close()
            mock_close.assert_called_once()


    def test_interceptor_init_sets_subpath(self):
        """Check that ReconcilerMethodClientInterceptor.__init__() sets the subpath."""
        interceptor = ReconcilerMethodClientInterceptor("/my/path")
        assert interceptor._subpath == "/my/path"


    def test_interceptor_intercepts_unary_unary_calls(self):
        """Check that the interceptor intercepts unary unary calls."""
        interceptor = ReconcilerMethodClientInterceptor("/my/path")

        def continuation(x, _):
            return x.method

        client_call_details = _ClientCallDetails(
            "/diode.v1.ReconcilerService/RetrieveIngestionLogs",
            None,
            None,
            None,
            None,
            None,
        )
        request = None
        assert (
            interceptor.intercept_unary_unary(continuation, client_call_details, request)
            == "/my/path/diode.v1.ReconcilerService/RetrieveIngestionLogs"
        )


    def test_interceptor_intercepts_stream_unary_calls(self):
        """Check that ReconcilerMethodClientInterceptor.intercept_stream_unary() intercepts stream unary calls."""
        interceptor = ReconcilerMethodClientInterceptor("/my/path")

        def continuation(x, _):
            return x.method

        client_call_details = _ClientCallDetails(
            "/diode.v1.ReconcilerService/RetrieveIngestionLogs",
            None,
            None,
            None,
            None,
            None,
        )
        request_iterator = None
        assert (
            interceptor.intercept_stream_unary(
                continuation, client_call_details, request_iterator
            )
            == "/my/path/diode.v1.ReconcilerService/RetrieveIngestionLogs"
        )
