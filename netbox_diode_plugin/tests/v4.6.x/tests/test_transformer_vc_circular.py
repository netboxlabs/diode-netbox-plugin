"""Transformer behavior for circular device<->virtual_chassis references."""
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import TestCase

from netbox_diode_plugin.api.common import ChangeType
from netbox_diode_plugin.api.differ import generate_changeset


def _device_entity(name, extra=None):
    entity = {
        "name": name,
        "site": {"name": "vctf-site"},
        "role": {"name": "vctf-role"},
        "device_type": {"manufacturer": {"name": "vctf-mfr"}, "model": "vctf-dt"},
    }
    entity.update(extra or {})
    return entity


class VcCircularTransformTests(TestCase):
    """Natural VC shapes must transform cycle-free with position preserved."""

    def test_natural_master_shape_plans_without_cycle(self):
        """A master device carrying its own VC ref must not cycle."""
        entity = _device_entity("vctf-sw1", {
            "vc_position": 1,
            "vc_priority": 200,
            "virtual_chassis": {
                "name": "vctf-stack",
                "master": {"name": "vctf-sw1", "site": {"name": "vctf-site"}},
            },
        })
        result = generate_changeset(entity, "dcim.device")
        self.assertIsNone(result.errors)
        types = [c.object_type for c in result.change_set.changes]
        self.assertIn("dcim.virtualchassis", types)

    def test_deferred_update_carries_position_and_priority(self):
        """The deferred VC-set must re-assert position/priority after the signal."""
        entity = _device_entity("vctf-sw2", {
            "vc_position": 3,
            "vc_priority": 128,
            "virtual_chassis": {
                "name": "vctf-stack2",
                "master": {"name": "vctf-sw2", "site": {"name": "vctf-site"}},
            },
        })
        result = generate_changeset(entity, "dcim.device")
        self.assertIsNone(result.errors)
        deferred = [
            c for c in result.change_set.changes
            if c.object_type == "dcim.device" and c.change_type == ChangeType.UPDATE
            and "virtual_chassis" in (c.data or {})
        ]
        self.assertTrue(deferred, result.change_set.changes)
        self.assertEqual(deferred[0].data.get("vc_position"), 3)
        self.assertEqual(deferred[0].data.get("vc_priority"), 128)

    def test_explicit_vc_clear_does_not_crash(self):
        """virtual_chassis: {} must plan a clear, not an internal error."""
        site = Site.objects.create(name="vctf-site", slug="vctf-site")
        mfr = Manufacturer.objects.create(name="vctf-mfr", slug="vctf-mfr")
        dt = DeviceType.objects.create(manufacturer=mfr, model="vctf-dt", slug="vctf-dt")
        role = DeviceRole.objects.create(name="vctf-role", slug="vctf-role")
        member = Device.objects.create(name="vctf-member", site=site, device_type=dt, role=role)
        vc = VirtualChassis.objects.create(name="vctf-stack3")
        Device.objects.filter(pk=member.pk).update(virtual_chassis=vc, vc_position=2)

        entity = _device_entity("vctf-member", {"virtual_chassis": {}})
        result = generate_changeset(entity, "dcim.device")
        self.assertIsNone(result.errors)
        clears = [
            c for c in result.change_set.changes
            if c.object_type == "dcim.device"
            and "virtual_chassis" in (c.data or {})
            and c.data["virtual_chassis"] is None
        ]
        self.assertTrue(clears, result.change_set.changes)

    def test_primary_ip_empty_clear_does_not_crash(self):
        """The pre-existing clear-branch defect: primary_ip4: {} must not 500."""
        entity = _device_entity("vctf-sw4", {"primary_ip4": {}})
        result = generate_changeset(entity, "dcim.device")
        self.assertIsNone(result.errors)
