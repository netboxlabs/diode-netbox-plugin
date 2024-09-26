#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Reconciler - SDK - Exceptions."""

from grpc import RpcError, StatusCode


class ReconcilerClientError(RpcError):
    """Reconciler Client Error."""

    _status_code = None
    _details = None

    def __init__(self, err: RpcError):
        """Initialize ReconcilerClientError."""
        self._status_code = err.code()
        self._details = err.details()

    @property
    def status_code(self):
        """Return status code."""
        if isinstance(self._status_code, StatusCode):
            return self._status_code.name
        return self._status_code

    @property
    def details(self):
        """Return error details."""
        return self._details

    def __repr__(self):
        """Return string representation."""
        return f"<ReconcilerClientError status code: {self._status_code}, details: {self._details}>"
