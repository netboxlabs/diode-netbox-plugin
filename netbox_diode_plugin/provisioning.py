#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""
Provision the Diode service user's NetBox object permissions.

NetBox 4.7 consults the requesting user's permissions in two places on
the plugin's apply path:

- attribute-based related-object resolution during serializer
  validation restricts lookups to objects the user may view
  (``utilities.api.get_related_object_by_attrs`` -> ``restrict(user,
  'view')``, NetBox #21988);
- creating/updating an interface's primary MAC address requires
  ``dcim.add_macaddress`` (``MACAddressShortcutMixin``).

The service user is deliberately NOT a superuser (that was revoked for
cause in migration 0005_revoke_superuser_status); instead these two
explicit, auditable ObjectPermissions are provisioned and re-synced on
every ``manage.py migrate`` via post_migrate, so new object types picked
up by a NetBox upgrade are covered by the same command that installs
them.

The view grant spans all object types with no constraints: NetBox's
``restrict()`` short-circuits an unconstrained grant and returns the
queryset unmodified, and the plugin's matcher/differ already read
through unrestricted model managers, so this matches the plugin's
existing read posture exactly - without superuser's write, user
management, or script-execution reach.

Operator kill switches are respected: deactivating the service user or
disabling either ObjectPermission in the NetBox UI is never undone here
(sync only touches enabled permissions' object-type sets).
"""

import logging

logger = logging.getLogger(__name__)

VIEW_PERMISSION_NAME = "Diode ingestion: view for reference resolution"
MACADDRESS_PERMISSION_NAME = "Diode ingestion: MAC address create"


def provision_diode_permissions(sender=None, **kwargs):  # noqa: ARG001
    """Create/refresh the service user's ObjectPermissions (post_migrate receiver)."""
    # Deferred imports: models are not loadable at module import time.
    from core.models import ObjectType
    from users.models import ObjectPermission

    from .plugin_config import get_diode_user

    diode_user = get_diode_user()

    view_permission, created = ObjectPermission.objects.get_or_create(
        name=VIEW_PERMISSION_NAME,
        defaults={"actions": ["view"], "constraints": None},
    )
    if created:
        logger.info("Created ObjectPermission %r for the Diode service user", VIEW_PERMISSION_NAME)
    if view_permission.enabled:
        view_permission.object_types.set(ObjectType.objects.all())
    else:
        logger.warning(
            "ObjectPermission %r is disabled; attribute-based reference "
            "resolution will fail until it is re-enabled", VIEW_PERMISSION_NAME
        )
    view_permission.users.add(diode_user)

    mac_permission, created = ObjectPermission.objects.get_or_create(
        name=MACADDRESS_PERMISSION_NAME,
        defaults={"actions": ["add"], "constraints": None},
    )
    if created:
        logger.info("Created ObjectPermission %r for the Diode service user", MACADDRESS_PERMISSION_NAME)
    if mac_permission.enabled:
        mac_permission.object_types.set(
            ObjectType.objects.filter(app_label="dcim", model="macaddress")
        )
    mac_permission.users.add(diode_user)
