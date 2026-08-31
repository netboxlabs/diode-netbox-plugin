#!/usr/bin/env python
# Copyright 2025 NetBox Labs, Inc.
"""Diode NetBox Plugin."""

from netbox.plugins import PluginConfig

from .version import version_semver


class NetBoxDiodePluginConfig(PluginConfig):
    """NetBox Diode plugin configuration."""

    name = "netbox_diode_plugin"
    verbose_name = "NetBox Labs, Diode Plugin"
    description = "Diode plugin for NetBox."
    version = version_semver()
    base_url = "diode"
    min_version = "4.4.10"
    max_version = "4.6.99"
    middleware = [
        "netbox_diode_plugin.api.profile.DiodeProfileMiddleware",
    ]
    default_settings = {
        # Default Diode gRPC target for communication with Diode server
        "diode_target": "grpc://localhost:8080/diode",

        # Default username associated with changes applied via plugin
        "diode_username": "diode",

        # client_id and client_secret for communication with Diode server.
        # By default, the secret is read from a file /run/secrets/netbox_to_diode
        # but may be specified directly as a string in netbox_to_diode_client_secret
        "netbox_to_diode_client_id": "netbox-to-diode",
        "netbox_to_diode_client_secret": None,
        "secrets_path": "/run/secrets/",
        "netbox_to_diode_client_secret_name": "netbox_to_diode",
        "diode_max_auth_retries": 3,

        # List of audiences to require for the diode-to-netbox token.
        # If empty, no audience is required.
        "required_token_audience": [],

        # TTL in seconds for caching find_existing_object results in Redis.
        # Cached entries are auto-invalidated when this plugin's apply path
        # updates the same PK; the TTL bounds staleness for external
        # mutations (UI edits, direct API writes, other tools). Set to 0 to
        # disable caching.
        "find_obj_cache_ttl": 30,

        # Override the displayed Diode target URL without affecting internal
        # communication (e.g. to show the external ingress address).
        "diode_target_display": None,

        # Per-write side-effect bypasses applied during /bulk-plan-apply
        # (and the per-changeset apply path it shares with the legacy
        # /apply-change-set endpoint). Both default to False; enabling
        # either trades NetBox-side correctness for ingest throughput
        # and requires a worker restart to take effect.
        #
        # apply_bypass_counter_updates: skip the per-write parent counter
        # UPDATE (Device.interface_count, DeviceType.device_count, ...).
        # Counters drift; reconcile via utilities.counters.update_counts.
        #
        # apply_bypass_change_logging: skip the per-write ObjectChange
        # row + post_save/m2m_changed signal handler chain. CREATE/UPDATE
        # made via diode apply do not appear in the NetBox audit log;
        # provenance lives in the diode-side ChangeSet rows.
        #
        # apply_bypass_search_indexing: skip the per-write CachedValue
        # INSERTs that back NetBox's global search index. NetBox has no
        # built-in periodic reindex, so deployments enabling this must
        # schedule `manage.py reindex` (or a system_job) to keep the
        # search box current.
        #
        # apply_bypass_customfield_query_cache: install a process-level
        # cache for CustomField.objects.get_for_model. NetBox's built-in
        # cache is request-scoped and misses for models with no custom
        # fields (empty-QuerySet truthiness bug), so under load every
        # instance.clean()/save() re-queries extras_customfield. Cache
        # is invalidated on CustomField post_save/post_delete signals.
        #
        # apply_buffer_change_logging: keep the audit trail intact but
        # cut the per-save change-logging cost. During apply,
        # ObjectChange serialisation skips the per-m2m-relation SELECTs
        # that dominate `to_objectchange` (one query per m2m field on
        # every save), and the rows are collected in an in-memory
        # buffer instead of being written one at a time. On successful
        # commit the buffer is flushed as a single `bulk_create`, with
        # all objects' m2m relations resolved in one query per relation,
        # and `post_save` is re-emitted so receivers connected to
        # `post_save(ObjectChange)` still fire. The flush runs inline at
        # commit, so the audit log stays immediately consistent.
        # Mutually exclusive in intent with `apply_bypass_change_logging`
        # - if both are enabled, bypass wins (no rows produced at all).
        #
        # apply_buffer_counter_updates: keep counters exact but stop
        # holding the parent row lock for the whole apply. Deltas
        # accumulate in memory and flush as one CASE UPDATE per counter
        # just before COMMIT, inside the same transaction, so unlike the
        # bypass this cannot drift. If both counter flags are enabled,
        # bypass wins (no counter update at all).
        "apply_bypass_counter_updates": False,
        "apply_bypass_change_logging": False,
        "apply_bypass_search_indexing": False,
        "apply_bypass_customfield_query_cache": False,
        "apply_buffer_change_logging": False,
        "apply_buffer_counter_updates": False,

        # Per-entity retry on Postgres deadlock (SQLSTATE 40P01) during
        # the apply phase of /bulk-plan-apply. Cross-batch concurrent
        # writes for shared-lookup unique indexes (dcim_site_slug_key,
        # dcim_devicerole_name, ...) occasionally deadlock under load;
        # the deadlocked transaction is killed by Postgres and the
        # entity would otherwise fail. Setting >0 retries the apply
        # with small jittered backoff. Set to 0 to disable retries.
        "apply_deadlock_max_retries": 2,
    }


config = NetBoxDiodePluginConfig
