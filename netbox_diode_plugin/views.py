#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode Netbox Plugin - Views."""

from django.shortcuts import render
from django.views.generic import View


class AuditLogView(View):
    """Audit log view."""

    def get(self, request):
        """Render an audit log template."""
        return render(request, "diode/audit_log.html")
