#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Tests for binding a device type by its part number when its model matches nothing."""

from dcim.models import Device, DeviceRole, DeviceType, Interface, InterfaceTemplate, Manufacturer, Site
from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase

from netbox_diode_plugin.api.applier import _is_auto_created_component, apply_changeset
from netbox_diode_plugin.api.common import ChangeType, UnresolvedReference
from netbox_diode_plugin.api.differ import extract_supported_models, generate_changeset
from netbox_diode_plugin.api.matcher import (
    _FALLBACK_MATCHERS,
    _REQUIRES_PRE_SAVE_MATCH,
    PartNumberFallbackMatcher,
    _find_obj_cache_key,
    _request_obj_cache,
    binds_without_writing,
    enter_request_obj_cache,
    exit_request_obj_cache,
    find_existing_object,
    get_model_matchers,
    part_number_key,
)

PART = "SW-9200-48P"
CATALOG_MODEL = "Series 9200 48-port"


def _writes(cs, object_type=None):
    """Planned changes other than NOOPs, optionally for one object type."""
    return [
        c for c in cs.changes
        if c.change_type != ChangeType.NOOP and (object_type is None or c.object_type == object_type)
    ]


class PartNumberKeyTestCase(SimpleTestCase):
    """What a payload names its part by."""

    def test_asserted_part_number_wins_over_model(self):
        """An asserted part number is the key; the model is not consulted."""
        self.assertEqual(part_number_key({"model": "anything", "part_number": " PN-1 "}), "PN-1")

    def test_blank_part_number_falls_back_to_model(self):
        """An explicitly empty part number reads as absent."""
        self.assertEqual(part_number_key({"model": " SW-1 ", "part_number": ""}), "SW-1")

    def test_placeholders_and_blanks_are_never_keys(self):
        """No key from blank, whitespace or an unknown placeholder."""
        for data in (
            {"model": ""},
            {"model": "   "},
            {"model": "Unknown"},
            {"model": "UNKNOWN"},
            {"model": "SW-1", "part_number": "unknown"},
            {},
        ):
            self.assertIsNone(part_number_key(data), data)


class PartNumberFallbackMatcherTestCase(TestCase):
    """The fallback binds a type by part number, and only after everything else missed."""

    @classmethod
    def setUpTestData(cls):
        """A vendor with a catalog-style type: marketing model, part ID in part_number."""
        cls.mfr = Manufacturer.objects.create(name="pnf-vendor", slug="pnf-vendor")
        cls.other_mfr = Manufacturer.objects.create(name="pnf-other", slug="pnf-other")
        cls.catalog = DeviceType.objects.create(
            manufacturer=cls.mfr, model=CATALOG_MODEL, slug="pnf-vendor-sw-9200-48p", part_number=PART,
        )

    def setUp(self):
        """Each test answers from the database, not from a lookup an earlier test cached."""
        django_cache.clear()

    def tearDown(self):
        """Leave no cached lookups behind for other test modules."""
        django_cache.clear()

    def _find(self, **data):
        return find_existing_object({"manufacturer": self.mfr.pk, **data}, "dcim.devicetype")

    def test_model_equal_to_a_part_number_binds_that_type(self):
        """The payload's model is the catalog type's part number."""
        self.assertEqual(self._find(model=PART), self.catalog)

    def test_asserted_part_number_binds_when_model_misses(self):
        """An asserted part number is matched even when the model names something else."""
        self.assertEqual(self._find(model="SW 9200 family", part_number=PART), self.catalog)

    def test_model_matcher_still_wins(self):
        """A type whose model is the part ID is found first; the fallback never runs."""
        duplicate = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup")
        self.assertEqual(self._find(model=PART), duplicate)

    def test_other_manufacturer_is_never_bound(self):
        """Part numbers are scoped to the manufacturer."""
        found = find_existing_object({"manufacturer": self.other_mfr.pk, "model": PART}, "dcim.devicetype")
        self.assertIsNone(found)

    def test_unresolved_manufacturer_abstains(self):
        """A manufacturer created in the same batch cannot own an existing type."""
        matcher = PartNumberFallbackMatcher(model_class=DeviceType, name="t")
        ref = UnresolvedReference(object_type="dcim.manufacturer", uuid="u-1")
        self.assertIsNone(matcher.build_queryset({"manufacturer": ref, "model": PART}))
        self.assertIsNone(matcher.build_queryset({"manufacturer": True, "model": PART}))

    def test_placeholder_model_is_never_bound(self):
        """A type whose part number is a placeholder never absorbs unidentified devices."""
        DeviceType.objects.create(manufacturer=self.mfr, model="pnf-junk", slug="pnf-junk", part_number="Unknown")
        self.assertIsNone(self._find(model="Unknown"))

    def test_shared_part_number_binds_nothing_and_names_both(self):
        """Two types share the part number: bind neither, and say which."""
        second = DeviceType.objects.create(
            manufacturer=self.mfr, model="Series 9200 48-port rev B", slug="pnf-rev-b", part_number=PART,
        )
        with self.assertLogs("netbox_diode_plugin.api.matcher", level="WARNING") as logs:
            self.assertIsNone(self._find(model=PART))
        message = "\n".join(logs.output)
        self.assertIn(f"pk={self.catalog.pk}", message)
        self.assertIn(f"pk={second.pk}", message)

    def test_fallback_is_the_last_device_type_matcher(self):
        """Consulted only after every constraint, custom-field and slug matcher."""
        names = [m.name for m in get_model_matchers(DeviceType)]
        self.assertEqual(names[-1], "fallback_devicetype_part_number")
        self.assertEqual(names.count("fallback_devicetype_part_number"), 1)

    def test_only_device_types_have_a_fallback(self):
        """Every other supported type keeps its matchers unchanged."""
        for object_type, info in extract_supported_models().items():
            names = [m.name for m in get_model_matchers(info["model"]) if m.name.startswith("fallback_")]
            expected = ["fallback_devicetype_part_number"] if object_type == "dcim.devicetype" else []
            self.assertEqual(names, expected, object_type)

    def test_fallback_types_never_take_a_writing_apply_path(self):
        """
        Apply-time lookups that write their payload must never meet a fallback type.

        The pre-save match and the auto-created-component update both save the
        payload onto whatever find_existing_object returns; for a fallback type
        that can be a row found by part number, which would then be renamed.
        """
        for object_type in _FALLBACK_MATCHERS:
            self.assertNotIn(object_type, _REQUIRES_PRE_SAVE_MATCH)
            self.assertFalse(_is_auto_created_component(object_type), object_type)

    def test_fallback_never_fingerprints(self):
        """In-batch dedupe is not widened by part number."""
        matcher = PartNumberFallbackMatcher(model_class=DeviceType, name="t")
        self.assertIsNone(matcher.fingerprint({"manufacturer": 1, "model": PART}))


class PartNumberBindWritesNothingTestCase(TestCase):
    """A type bound by part number is referenced, never written."""

    @classmethod
    def setUpTestData(cls):
        """A catalog-style type and the scaffolding a device needs."""
        cls.mfr = Manufacturer.objects.create(name="pnf-vendor", slug="pnf-vendor")
        cls.catalog = DeviceType.objects.create(
            manufacturer=cls.mfr, model=CATALOG_MODEL, slug="pnf-vendor-sw-9200-48p", part_number=PART,
        )
        cls.site = Site.objects.create(name="pnf-site", slug="pnf-site")
        cls.role = DeviceRole.objects.create(name="pnf-role", slug="pnf-role")

    def setUp(self):
        """Each test answers from the database, not from a lookup an earlier test cached."""
        django_cache.clear()

    def tearDown(self):
        """Leave no cached lookups behind for other test modules."""
        django_cache.clear()

    def _device_type(self, **extra):
        return {"model": PART, "manufacturer": {"name": "pnf-vendor"}, **extra}

    def _device(self, name, **extra):
        return {
            "name": name, "site": {"name": "pnf-site"}, "role": {"name": "pnf-role"},
            "device_type": self._device_type(), **extra,
        }

    def _assert_catalog_untouched(self):
        self.catalog.refresh_from_db()
        self.assertEqual((self.catalog.model, self.catalog.part_number), (CATALOG_MODEL, PART))

    def test_device_type_entity_plans_no_write(self):
        """The root type binds: no create, and no rename to the part ID."""
        cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_device_create_points_at_the_catalog_type(self):
        """A new device lands on the catalog type, which is not written."""
        cs = generate_changeset(self._device("pnf-dev1"), "dcim.device").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        creates = [c for c in _writes(cs, "dcim.device") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        self.assertEqual(creates[0].data["device_type"], self.catalog.pk)

    def test_interface_nesting_the_device_plans_no_device_or_type_write(self):
        """The nested copy every interface carries binds the same way."""
        Device.objects.create(name="pnf-dev2", site=self.site, role=self.role, device_type=self.catalog)
        entity = {"name": "eth9", "type": "1000base-t", "device": self._device("pnf-dev2")}
        cs = generate_changeset(entity, "dcim.interface").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self.assertEqual(_writes(cs, "dcim.device"), [], [c.to_dict() for c in cs.changes])

    def test_second_plan_in_the_same_request_still_writes_nothing(self):
        """A repeat served from the request cache binds the same way."""
        token = enter_request_obj_cache()
        try:
            generate_changeset(self._device_type(), "dcim.devicetype")
            key = _find_obj_cache_key({"manufacturer": self.mfr.pk, "model": PART}, "dcim.devicetype")
            self.assertIn(key, _request_obj_cache.get(), "the second plan must be served from the request cache")
            cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
            self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        finally:
            exit_request_obj_cache(token)

    def test_a_part_number_edit_is_seen_by_the_next_plan(self):
        """A fallback answer is not cached across requests, so an edited row is never renamed."""
        generate_changeset(self._device_type(), "dcim.devicetype")
        key = _find_obj_cache_key({"manufacturer": self.mfr.pk, "model": PART}, "dcim.devicetype")
        self.assertIsNone(django_cache.get(key))
        DeviceType.objects.filter(pk=self.catalog.pk).update(part_number="SW-9200-48P-A")
        cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
        updates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.UPDATE]
        self.assertEqual(updates, [], [c.to_dict() for c in cs.changes])
        self.catalog.refresh_from_db()
        self.assertEqual(self.catalog.model, CATALOG_MODEL)

    def test_device_on_a_duplicate_stays_there(self):
        """A device already on a type named after the part ID plans no move."""
        duplicate = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup3")
        Device.objects.create(name="pnf-dev4", site=self.site, role=self.role, device_type=duplicate)
        cs = generate_changeset(self._device("pnf-dev4"), "dcim.device").change_set
        self.assertEqual(_writes(cs), [], [c.to_dict() for c in cs.changes])

    def test_a_second_type_with_the_part_number_later_splits_the_device_again(self):
        """
        Several candidates bind nothing, even for a device already bound.

        This is the chosen behaviour, kept from before the fallback existed: the
        plan creates a type named after the part ID and moves the device onto it.
        """
        Device.objects.create(name="pnf-dev5", site=self.site, role=self.role, device_type=self.catalog)
        DeviceType.objects.create(
            manufacturer=self.mfr, model="Series 9200 48-port rev B", slug="pnf-rev-b2", part_number=PART,
        )
        with self.assertLogs("netbox_diode_plugin.api.matcher", level="WARNING"):
            cs = generate_changeset(self._device("pnf-dev5"), "dcim.device").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        moves = [c for c in _writes(cs, "dcim.device") if c.change_type == ChangeType.UPDATE]
        self.assertEqual(len(moves), 1, [c.to_dict() for c in cs.changes])

    def test_slug_match_on_a_row_identified_by_part_number_is_not_renamed(self):
        """Reached through (manufacturer, slug), the catalog row keeps its model."""
        cs = generate_changeset(self._device_type(slug="pnf-vendor-sw-9200-48p"), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_new_manufacturer_still_creates_the_type(self):
        """No existing row can match; the payload creates its type as before."""
        cs = generate_changeset({"model": PART, "manufacturer": {"name": "pnf-new-vendor"}}, "dcim.devicetype").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])

    def test_shared_part_number_still_creates_the_type(self):
        """Two candidates bind nothing, so the plan is exactly today's create."""
        DeviceType.objects.create(
            manufacturer=self.mfr, model="Series 9200 48-port rev B", slug="pnf-rev-b", part_number=PART,
        )
        with self.assertLogs("netbox_diode_plugin.api.matcher", level="WARNING"):
            cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])

    def test_empty_part_number_keeps_the_model_as_key(self):
        """part_number: "" is not an assertion; the model still binds, and nothing is cleared."""
        cs = generate_changeset(self._device_type(part_number=""), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_model_match_still_writes_even_when_model_is_the_part_number(self):
        """A type found by its own model is updated normally, even one whose model is its part number."""
        by_model = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup2", part_number=PART)
        cs = generate_changeset(self._device_type(description="from ingest"), "dcim.devicetype").change_set
        updates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.UPDATE]
        self.assertEqual([c.object_id for c in updates], [by_model.pk], [c.to_dict() for c in cs.changes])

    def test_binds_without_writing_reads_payload_and_row_only(self):
        """The predicate: part-number identity, a different model, the same manufacturer."""
        data = {"manufacturer": self.mfr.pk, "model": PART}
        self.assertTrue(binds_without_writing("dcim.devicetype", data, self.catalog))
        self.assertFalse(binds_without_writing("dcim.devicetype", {**data, "model": CATALOG_MODEL}, self.catalog))
        self.assertFalse(
            binds_without_writing("dcim.devicetype", {**data, "manufacturer": self.mfr.pk + 999}, self.catalog)
        )
        self.assertFalse(binds_without_writing("dcim.moduletype", data, self.catalog))


class PartNumberBindConvergesTestCase(TestCase):
    """Plan and apply: a device created on a catalog type converges with its template interfaces."""

    @classmethod
    def setUpTestData(cls):
        """A catalog-style type carrying an interface template, and device scaffolding."""
        cls.mfr = Manufacturer.objects.create(name="pnf-vendor", slug="pnf-vendor")
        cls.catalog = DeviceType.objects.create(
            manufacturer=cls.mfr, model=CATALOG_MODEL, slug="pnf-vendor-sw-9200-48p", part_number=PART,
        )
        InterfaceTemplate.objects.create(device_type=cls.catalog, name="eth0", type="1000base-t")
        Site.objects.create(name="pnf-site", slug="pnf-site")
        DeviceRole.objects.create(name="pnf-role", slug="pnf-role")

    def setUp(self):
        """Each test answers from the database, not from a lookup an earlier test cached."""
        django_cache.clear()

    def tearDown(self):
        """Leave no cached lookups behind for other test modules."""
        django_cache.clear()

    @staticmethod
    def _ingest(entity, object_type):
        apply_changeset(generate_changeset(entity, object_type).change_set, request=None)

    @staticmethod
    def _entities(name):
        device = {
            "name": name, "site": {"name": "pnf-site"}, "role": {"name": "pnf-role"},
            "device_type": {"model": PART, "manufacturer": {"name": "pnf-vendor"}},
        }
        interface = {"name": "eth0", "type": "1000base-t", "description": "from ingest", "device": device}
        return device, interface

    def _assert_converged(self, name, device, interface):
        created = Device.objects.get(name=name)
        self.assertEqual(created.device_type_id, self.catalog.pk)
        interfaces = Interface.objects.filter(device=created, name="eth0")
        self.assertEqual(interfaces.count(), 1)
        self.assertEqual(interfaces.get().description, "from ingest")
        self.assertEqual(DeviceType.objects.filter(manufacturer=self.mfr).count(), 1)
        self.catalog.refresh_from_db()
        self.assertEqual((self.catalog.model, self.catalog.part_number), (CATALOG_MODEL, PART))
        for entity, object_type in ((device, "dcim.device"), (interface, "dcim.interface")):
            cs = generate_changeset(entity, object_type).change_set
            self.assertEqual(_writes(cs), [], [c.to_dict() for c in cs.changes])

    def test_device_and_its_template_interface_converge(self):
        """Device first, then its interface: one interface, updated in place, then an empty plan."""
        device, interface = self._entities("pnf-dev3")
        self._ingest(device, "dcim.device")
        self._ingest(interface, "dcim.interface")
        self._assert_converged("pnf-dev3", device, interface)

    def test_interface_nesting_a_new_device_converges(self):
        """One changeset creates the device and the interface its template already created."""
        device, interface = self._entities("pnf-dev6")
        self._ingest(interface, "dcim.interface")
        self._assert_converged("pnf-dev6", device, interface)

    def test_plan_ahead_device_and_interface_converge(self):
        """Both planned before either applies: the second device create binds, the interface updates."""
        device, interface = self._entities("pnf-dev7")
        device_plan = generate_changeset(device, "dcim.device").change_set
        interface_plan = generate_changeset(interface, "dcim.interface").change_set
        apply_changeset(device_plan, request=None)
        apply_changeset(interface_plan, request=None)
        self._assert_converged("pnf-dev7", device, interface)
