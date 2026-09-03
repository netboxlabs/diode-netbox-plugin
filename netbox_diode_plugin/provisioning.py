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


def extend_view_permission_for_new_type(sender, instance, created, **kwargs):  # noqa: ARG001
    """
    Add a lazily-created content type to the view grant (post_save receiver).

    ObjectType rows are created on first reference, not only during
    migrate, so a post_migrate-only sync leaves late-born types outside
    the grant and their name-based references failing as 'related object
    not found'. Observed live: 5 of 170 types missing within hours of
    provisioning.
    """
    if not created:
        return
    from users.models import ObjectPermission

    try:
        view_permission = ObjectPermission.objects.get(name=VIEW_PERMISSION_NAME)
    except ObjectPermission.DoesNotExist:
        return  # not provisioned yet; post_migrate will pick everything up
    if view_permission.enabled:
        view_permission.object_types.add(instance)


def provision_diode_permissions(sender=None, **kwargs):  # noqa: ARG001
    """Create/refresh the service user's ObjectPermissions (post_migrate receiver)."""
    # Deferred imports: models are not loadable at module import time.
    from core.models import ObjectType
    from users.models import ObjectPermission

    from .plugin_config import get_diode_user

    diode_user = get_diode_user()

    # The service user is resolved purely by the configured diode_username
    # (long-standing design). If that name collides with a human or
    # SSO-managed account, the grants below would attach to it - warn
    # loudly so the collision is visible; refusing it outright is a
    # behavioral redesign tracked separately.
    if diode_user.has_usable_password() or diode_user.is_superuser:
        logger.warning(
            "The configured diode_username resolves to an account with a "
            "usable password and/or superuser status; if this is not the "
            "plugin-managed service account, choose a dedicated username "
            "before the ingestion permissions below attach to it."
        )

    view_permission, created = ObjectPermission.objects.get_or_create(
        name=VIEW_PERMISSION_NAME,
        defaults={"actions": ["view"], "constraints": None},
    )
    if created:
        logger.info("Created the Diode ingestion view ObjectPermission")
    if view_permission.enabled:
        view_permission.object_types.set(ObjectType.objects.all())
    else:
        logger.warning(
            "The Diode ingestion view ObjectPermission is disabled; "
            "attribute-based reference resolution will fail until it is re-enabled"
        )
    view_permission.users.add(diode_user)

    mac_permission, created = ObjectPermission.objects.get_or_create(
        name=MACADDRESS_PERMISSION_NAME,
        defaults={"actions": ["add"], "constraints": None},
    )
    if created:
        logger.info("Created the Diode ingestion MAC-address-create ObjectPermission")
    if mac_permission.enabled:
        mac_permission.object_types.set(
            ObjectType.objects.filter(app_label="dcim", model="macaddress")
        )
    mac_permission.users.add(diode_user)
