#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Reconciler - SDK - Client."""

import collections
import logging
import platform
from urllib.parse import urlparse

import certifi
import grpc

import netbox_diode_plugin
from netbox_diode_plugin.reconciler.sdk.exceptions import ReconcilerClientError
from netbox_diode_plugin.reconciler.sdk.v1 import reconciler_pb2, reconciler_pb2_grpc

_LOGGER = logging.getLogger(__name__)


def _load_certs() -> bytes:
    """Loads cacert.pem."""
    with open(certifi.where(), "rb") as f:
        return f.read()


def parse_target(target: str) -> tuple[str, str, bool]:
    """Parse the target into authority, path and tls_verify."""
    parsed_target = urlparse(target)

    if parsed_target.scheme not in ["grpc", "grpcs"]:
        raise ValueError("target should start with grpc:// or grpcs://")

    tls_verify = parsed_target.scheme == "grpcs"

    authority = parsed_target.netloc

    if ":" not in authority:
        authority += ":443"

    return authority, parsed_target.path, tls_verify


class ReconcilerClient:
    """Reconciler Client."""

    _name = "reconciler-sdk-python"
    _version = "0.0.1"
    _channel = None
    _stub = None

    def __init__(
        self,
        target: str,
        api_key: str,
    ):
        """Initiate a new client."""
        self._target, self._path, self._tls_verify = parse_target(target)

        plugin_config = netbox_diode_plugin.config

        self._app_name = plugin_config.name
        self._app_version = plugin_config.version
        self._platform = platform.platform()
        self._python_version = platform.python_version()

        self._metadata = (
            ("authorization", api_key),
            ("platform", self._platform),
            ("python-version", self._python_version),
        )

        channel_opts = (
            ("grpc.primary_user_agent", f"{self._name}/{self._version} {self._app_name}/{self._app_version}"),
        )

        if self._tls_verify:
            _LOGGER.debug("Setting up gRPC secure channel")
            self._channel = grpc.secure_channel(
                self._target,
                grpc.ssl_channel_credentials(
                    root_certificates=_load_certs(),
                ),
                options=channel_opts,
            )
        else:
            _LOGGER.debug("Setting up gRPC insecure channel")
            self._channel = grpc.insecure_channel(
                target=self._target,
                options=channel_opts,
            )

        channel = self._channel

        if self._path:
            _LOGGER.debug(f"Setting up gRPC interceptor for path: {self._path}")
            rpc_method_interceptor = ReconcilerMethodClientInterceptor(subpath=self._path)

            intercept_channel = grpc.intercept_channel(
                self._channel, rpc_method_interceptor
            )
            channel = intercept_channel

        self._stub = reconciler_pb2_grpc.ReconcilerServiceStub(channel)


    @property
    def name(self) -> str:
        """Retrieve the name."""
        return self._name

    @property
    def version(self) -> str:
        """Retrieve the version."""
        return self._version

    @property
    def target(self) -> str:
        """Retrieve the target."""
        return self._target

    @property
    def path(self) -> str:
        """Retrieve the path."""
        return self._path

    @property
    def tls_verify(self) -> bool:
        """Retrieve the tls_verify."""
        return self._tls_verify

    @property
    def app_name(self) -> str:
        """Retrieve the app name."""
        return self._app_name

    @property
    def app_version(self) -> str:
        """Retrieve the app version."""
        return self._app_version

    @property
    def channel(self) -> grpc.Channel:
        """Retrieve the channel."""
        return self._channel

    def __enter__(self):
        """Enters the runtime context related to the channel object."""
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """Exits the runtime context related to the channel object."""
        self.close()

    def close(self):
        """Close the channel."""
        self._channel.close()

    def retrieve_ingestion_logs(
        self,
        state: str | None = None,
        data_type: str | None = None,
        request_id: str | None = None,
        ingestion_ts_start: int | None = None,
        ingestion_ts_end: int | None = None,
        page_token: str | None = None,
        page_size: int = 100,
        only_metrics: bool = False,
    ) -> reconciler_pb2.RetrieveIngestionLogsResponse:
        """Retrieve ingestion logs."""
        try:
            request = reconciler_pb2.RetrieveIngestionLogsRequest(
                page_size=page_size,
                state=state,
                data_type=data_type,
                request_id=request_id,
                ingestion_ts_start=ingestion_ts_start,
                ingestion_ts_end=ingestion_ts_end,
                page_token=page_token,
                only_metrics=only_metrics,
            )

            return self._stub.RetrieveIngestionLogs(request, metadata=self._metadata)
        except grpc.RpcError as err:
            raise ReconcilerClientError(err) from err


class _ClientCallDetails(
    collections.namedtuple(
        "_ClientCallDetails",
        (
            "method",
            "timeout",
            "metadata",
            "credentials",
            "wait_for_ready",
            "compression",
        ),
    ),
    grpc.ClientCallDetails,
):
    """
    _ClientCallDetails class.

    This class describes an RPC to be invoked and is required for custom gRPC interceptors.

    """

    pass


class ReconcilerMethodClientInterceptor(
    grpc.UnaryUnaryClientInterceptor, grpc.StreamUnaryClientInterceptor
):
    """
    Reconciler Method Client Interceptor class.

    This class is used to intercept the client calls and modify the method details. It inherits from
    grpc.UnaryUnaryClientInterceptor and grpc.StreamUnaryClientInterceptor.

    Reconciler's default method generated from Protocol Buffers definition is /diode.v1.ReconcilerService/RetrieveIngestionLogs and in order
    to use Diode targets with path (i.e. localhost:8081/this/is/custom/path), this interceptor is used to modify the
    method details, by prepending the generated method name with the path extracted from initial target.

    """

    def __init__(self, subpath):
        """Initiate a new interceptor."""
        self._subpath = subpath

    def _intercept_call(self, continuation, client_call_details, request_or_iterator):
        """Intercept call."""
        method = client_call_details.method
        if client_call_details.method is not None:
            method = f"{self._subpath}{client_call_details.method}"

        client_call_details = _ClientCallDetails(
            method,
            client_call_details.timeout,
            client_call_details.metadata,
            client_call_details.credentials,
            client_call_details.wait_for_ready,
            client_call_details.compression,
        )

        response = continuation(client_call_details, request_or_iterator)
        return response

    def intercept_unary_unary(self, continuation, client_call_details, request):
        """Intercept unary unary."""
        return self._intercept_call(continuation, client_call_details, request)

    def intercept_stream_unary(
        self, continuation, client_call_details, request_iterator
    ):
        """Intercept stream unary."""
        return self._intercept_call(continuation, client_call_details, request_iterator)
