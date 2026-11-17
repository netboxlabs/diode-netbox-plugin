"""
Hand a device entity's configuration to a config store.

Diode's ``Device`` entity may carry ``config`` (``DeviceConfig``: startup, running,
candidate, metadata). NetBox has no field for it, so the transformer drops it. When the
plugin setting ``device_config_handler`` names a callable (a dotted path), the
bulk-plan-apply path extracts the config before planning and, once the device row is
known (created, updated or matched unchanged), calls::

    handler(device, running=bytes | None, startup=bytes | None,
            candidate=bytes | None, metadata=dict, entity_id=str | None)

The handler's failure is reported as a warning on the entity's result and never fails
the apply. ``netbox_config_compliance.diode.ingest_device_config`` is the reference
handler.
"""

import base64
import logging

from django.utils.module_loading import import_string
from netbox.plugins import get_plugin_config

from .common import ChangeType

logger = logging.getLogger(__name__)

DEVICE_OBJECT_TYPE = "dcim.device"
CONFIG_KEYS = ("config", "device_config", "deviceConfig")
_BLOB_FIELDS = ("startup", "running", "candidate")

__all__ = (
    "dispatch_device_config",
    "extract_device_config",
    "handler_configured",
    "resolve_device_id",
)


def handler_configured() -> bool:
    """True when the plugin setting ``device_config_handler`` names a callable."""
    return bool(get_plugin_config("netbox_diode_plugin", "device_config_handler"))


def extract_device_config(entity_data: dict) -> dict | None:
    """
    Remove ``config`` from a ``dcim.device`` entity and return it decoded, or None.

    Proto JSON encodes ``bytes`` as standard base64; a Python producer may already
    hand us text. Both are accepted. Missing or empty blobs are None.
    """
    raw = None
    for key in CONFIG_KEYS:
        if key in entity_data:
            raw = entity_data.pop(key)
            break
    if not isinstance(raw, dict):
        return None
    out = {}
    for field in _BLOB_FIELDS:
        value = raw.get(field)
        if value in (None, "", b""):
            out[field] = None
        elif isinstance(value, bytes):
            out[field] = value
        else:
            try:
                out[field] = base64.b64decode(value, validate=True)
            except Exception:
                out[field] = str(value).encode("utf-8")
    metadata = raw.get("metadata")
    out["metadata"] = metadata if isinstance(metadata, dict) else {}
    if not any(out[f] for f in _BLOB_FIELDS):
        return None
    return out


def resolve_device_id(change_set) -> int | None:
    """The NetBox id of the ``dcim.device`` change in an applied change set, if known."""
    if change_set is None:
        return None
    for change in change_set.changes:
        if change.object_type == DEVICE_OBJECT_TYPE and change.object_id:
            return change.object_id
    # A device that matched with nothing to change has an empty change list;
    # the differ records the matched row for exactly this case.
    return getattr(change_set, "root_object_id", None)


def dispatch_device_config(device_id: int, config: dict, entity_id=None) -> dict | None:
    """Call the configured handler. Returns a warning dict on failure, None on success."""
    path = get_plugin_config("netbox_diode_plugin", "device_config_handler")
    if not path:
        return None
    try:
        handler = import_string(path)
    except Exception as e:
        logger.error("device_config_handler %r cannot be imported: %s", path, e)
        return {"device_config": f"handler {path!r} cannot be imported"}
    try:
        from dcim.models import Device

        device = Device.objects.get(pk=device_id)
        handler(
            device,
            running=config.get("running"),
            startup=config.get("startup"),
            candidate=config.get("candidate"),
            metadata=config.get("metadata") or {},
            entity_id=entity_id,
        )
        logger.debug("device config for %s handed to %s", device, path)
        return None
    except Exception as e:  # never fail the apply because of the config store
        logger.exception("device_config_handler failed for device %s", device_id)
        return {"device_config": f"handler failed: {type(e).__name__}: {e}"}


def lift_device_config(object_type: str, entity_data: dict) -> dict | None:
    """Extract a device entity's config when a handler is configured, else None."""
    if not should_handle(object_type):
        return None
    return extract_device_config(entity_data)


def should_handle(object_type: str) -> bool:
    """True for a device entity when a handler is configured."""
    return object_type == DEVICE_OBJECT_TYPE and handler_configured()


def noop_device_change(change_set) -> bool:
    """True when the device change set has nothing to apply (all NOOP or empty)."""
    return change_set is None or all(c.change_type == ChangeType.NOOP for c in change_set.changes)
