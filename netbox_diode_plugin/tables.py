#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tables."""
import logging

import django_tables2 as tables
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from netbox.tables import BaseTable, columns


class ClientCredentialsTable(BaseTable):
    label = tables.Column(
        verbose_name=_("Label"),
        accessor="client_name",
    )
    client_id = tables.Column(
        verbose_name=_("Client ID"),
        accessor="client_id",
    )
    client_secret = tables.Column(
        verbose_name=_("Client Secret"),
        empty_values=(),
    )
    actions = tables.Column(
        verbose_name=_(""),
        orderable=False,
        empty_values=(),
        attrs={
            "td": {
                "class": "text-end",
            }
        },
    )

    exempt_columns = ("actions")
    embedded = False

    class Meta:
        attrs = {
            "class": "table table-hover object-list",
            "td": {"class": "align-middle"},
        }
        fields = None
        default_columns = (
            "label",
            "client_id",
            "client_secret",
            "actions",
        )

        empty_text = _("No Client Credentials to display")
        footer = False

    def render_client_secret(self, value):
        return "*****"


    def render_actions(self, record):
        delete_url = reverse(
            "plugins:netbox_diode_plugin:client_credential_delete",
            kwargs={"client_credential_id": record["client_id"]},
        )

        buttons = f"""
            <a class="btn btn-sm btn-red" 
                data-bs-toggle="tooltip" 
                data-bs-placement="top" 
                title="Delete" 
                href="{delete_url}" 
                type="button" 
                aria-label="Delete">
                <i class="mdi mdi-trash-can-outline"></i>
            </a>
        """  # noqa: E501

        return mark_safe(buttons)
