"""Transformer behavior for circular device<->virtual_chassis references."""
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site, VirtualChassis
from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api import transformer
from netbox_diode_plugin.api.common import VC_MEMBER_HINT, ChangeType
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


class FingerprintDedupePostCreateIsolationTests(TestCase):
    """A post-create node must never register fingerprints in `_fingerprint_dedupe`."""

    def test_post_create_node_does_not_corrupt_site_dedupe(self):
        """A post-create node sandwiched between duplicate sites must not steal their fingerprint slot."""
        site_a = {
            "_uuid": "site-a-uuid",
            "_object_type": "dcim.site",
            "_refs": set(),
            "name": "vctf-dedupe-site",
        }
        device_post_create = {
            "_uuid": "device-pc-uuid",
            "_object_type": "dcim.device",
            "_refs": set(),
            "_is_post_create": True,
            "_instance": "device-instance-uuid",
            "vc_position": 2,
        }
        site_b_dup = {
            "_uuid": "site-b-uuid",
            "_object_type": "dcim.site",
            "_refs": set(),
            "name": "vctf-dedupe-site",
        }

        result = transformer._fingerprint_dedupe([site_a, device_post_create, site_b_dup])
        self.assertEqual(len(result), 3)

        # (a) the duplicate site must dedupe onto the FIRST site entity.
        self.assertEqual(result[0]["_uuid"], "site-a-uuid")
        self.assertEqual(result[2]["_uuid"], "site-a-uuid")
        self.assertIs(result[0], result[2])

        # (b) the post-create node must keep ONLY its own fields: it must not
        # absorb the site's fields (e.g. 'name') nor its fingerprints.
        device_result = result[1]
        self.assertEqual(device_result["_uuid"], "device-pc-uuid")
        self.assertNotIn("name", device_result)
        self.assertEqual(device_result["vc_position"], 2)
class MemberHintMergeTests(SimpleTestCase):
    """
    _merge_nodes must UNION the member-device hint, not prefer a's copy.

    Private keys are otherwise "prefer a's value", which for this key would make
    the rule it feeds read "prefer the chassis the FIRST-named member already
    belongs to" -- an arbitrary choice of exactly the kind the whole policy
    exists to remove.

    Asserted directly on _merge_nodes because no single-entity payload can reach
    it: a dcim.device entity nests one chassis reference, so the fingerprint
    dedupe that merges two chassis nodes needs two member devices in one
    transform, which the SDK shapes this plugin accepts do not produce today. A
    guard nothing can reach through the API is still a guard, and mutation
    testing found this one unpinned without this test.
    """

    @staticmethod
    def _node(uuid, hint):
        return {
            "_object_type": "dcim.virtualchassis",
            "_uuid": uuid,
            "_refs": set(),
            "_warnings": {},
            "name": "stack",
            VC_MEMBER_HINT: list(hint),
        }

    def test_both_members_survive_the_merge(self):
        """Two chassis nodes, one member each: the merged node knows both."""
        merged = transformer._merge_nodes(self._node("a", [7]), self._node("b", [9]))
        self.assertEqual(merged[VC_MEMBER_HINT], [7, 9])

    def test_the_union_does_not_duplicate_or_reorder(self):
        """Idempotent on the overlap, and stable for the reader."""
        merged = transformer._merge_nodes(
            self._node("a", [7, 9]), self._node("b", [9, 11]))
        self.assertEqual(merged[VC_MEMBER_HINT], [7, 9, 11])

    def test_a_node_without_the_hint_contributes_nothing(self):
        """A chassis node reached other than through a member has no hint."""
        bare = self._node("b", [])
        del bare[VC_MEMBER_HINT]
        self.assertEqual(
            transformer._merge_nodes(self._node("a", [7]), bare)[VC_MEMBER_HINT], [7])
        self.assertEqual(
            transformer._merge_nodes(bare, self._node("a", [7]))[VC_MEMBER_HINT], [7])

    def test_neither_side_is_mutated(self):
        """The union must not accumulate into the inputs' own lists."""
        a, b = self._node("a", [7]), self._node("b", [9])
        transformer._merge_nodes(a, b)
        self.assertEqual(a[VC_MEMBER_HINT], [7])
        self.assertEqual(b[VC_MEMBER_HINT], [9])
