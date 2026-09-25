#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Tests for binding a device type by its part number when its model matches nothing."""

from types import SimpleNamespace

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Interface, InterfaceTemplate, Manufacturer, Site
from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomField
from rest_framework.exceptions import ValidationError

from netbox_diode_plugin.api.applier import _create_or_find_instance, _is_auto_created_component, apply_changeset
from netbox_diode_plugin.api.common import ChangeType, UnresolvedReference
from netbox_diode_plugin.api.differ import extract_supported_models, generate_changeset
from netbox_diode_plugin.api.matcher import (
    _FALLBACK_MATCH_ATTR,
    _FALLBACK_MATCHERS,
    _REQUIRES_PRE_SAVE_MATCH,
    PartNumberFallbackMatcher,
    _find_obj_cache_key,
    _get_custom_field_matchers,
    _request_obj_cache,
    binds_without_writing,
    enter_request_obj_cache,
    exit_request_obj_cache,
    find_existing_object,
    forget_fallback_answers,
    get_model_matchers,
    part_number_key,
)
from netbox_diode_plugin.api.supported_models import get_serializer_for_model

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

    def test_a_model_of_its_own_is_the_key(self):
        """A usable model is the key even when a different part number is asserted."""
        self.assertEqual(part_number_key({"model": " SW-1 ", "part_number": "PN-1"}), "SW-1")

    def test_an_asserted_part_number_is_never_the_key(self):
        """Without a usable model there is no key, whatever part number is asserted."""
        for data in ({"part_number": " PN-1 "}, {"model": "", "part_number": "PN-1"},
                     {"model": "Unknown", "part_number": "PN-1"}):
            self.assertIsNone(part_number_key(data), data)

    def test_placeholders_and_blanks_are_never_keys(self):
        """No key from blank, whitespace or an unknown placeholder."""
        for data in (
            {"model": ""},
            {"model": "   "},
            {"model": "Unknown"},
            {"model": "UNKNOWN"},
            {"model": "N/A"},
            {"model": "none"},
            {"model": "-"},
            {"model": "Unknown", "part_number": "n/a"},
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
        return find_existing_object({"manufacturer": self.mfr.pk, **data}, "dcim.devicetype", fallback=True)

    def test_model_equal_to_a_part_number_binds_that_type(self):
        """The payload's model is the catalog type's part number."""
        self.assertEqual(self._find(model=PART), self.catalog)

    def test_an_asserted_part_number_alone_binds_nothing(self):
        """Only the payload's model is matched against part numbers."""
        self.assertIsNone(self._find(part_number=PART))
        self.assertIsNone(self._find(model="Unknown", part_number=PART))

    def test_a_placeholder_named_type_is_never_a_candidate(self):
        """A type named Unknown that carries the part number does not make the match ambiguous."""
        DeviceType.objects.create(manufacturer=self.mfr, model="Unknown", slug="pnf-unknown", part_number=PART)
        self.assertEqual(self._find(model=PART), self.catalog)

    def test_a_contradicting_part_number_binds_nothing(self):
        """A payload asserting another part number names another part; an agreeing one still binds."""
        self.assertIsNone(self._find(model=PART, part_number=PART + "-AFI"))
        self.assertEqual(self._find(model=PART, part_number=PART), self.catalog)

    def test_a_model_of_its_own_is_never_bound_by_a_shared_part_number(self):
        """A payload naming its own model is keyed on it, so the shared part number binds nothing."""
        self.assertIsNone(self._find(model="Series 9200 48-port rev C", part_number=PART))

    def test_fallback_is_opt_in(self):
        """Without fallback=True the lookup never finds a row by part number."""
        self.assertIsNone(find_existing_object({"manufacturer": self.mfr.pk, "model": PART}, "dcim.devicetype"))

    def test_model_matcher_still_wins(self):
        """A type whose model is the part ID is found first; the fallback never runs."""
        duplicate = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup")
        self.assertEqual(self._find(model=PART), duplicate)

    def test_other_manufacturer_is_never_bound(self):
        """Part numbers are scoped to the manufacturer."""
        found = find_existing_object(
            {"manufacturer": self.other_mfr.pk, "model": PART}, "dcim.devicetype", fallback=True,
        )
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

        The pre-save match and the auto-created-component update save the payload
        onto the row they find. Both skip the fallback tier already; keeping the
        fallback types out of them is the second guard, since a row found by part
        number would be renamed.
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
        """A fallback answer served again from the request cache still binds, and keeps its tag."""
        payload = self._device_type()
        token = enter_request_obj_cache()
        try:
            generate_changeset(payload, "dcim.devicetype")
            key = _find_obj_cache_key({"manufacturer": self.mfr.pk, "model": PART}, "dcim.devicetype")
            cached = _request_obj_cache.get().get(key)
            self.assertEqual(cached, self.catalog, "the second plan must be served from the request cache")
            self.assertTrue(getattr(cached, _FALLBACK_MATCH_ATTR, False))
            cs = generate_changeset(payload, "dcim.devicetype").change_set
            self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        finally:
            exit_request_obj_cache(token)

    def test_a_batch_write_drops_cached_fallback_answers(self):
        """In one bulk request, a type another entity creates is seen by the next lookup."""
        token = enter_request_obj_cache()
        try:
            generate_changeset(self._device_type(), "dcim.devicetype")
            key = _find_obj_cache_key({"manufacturer": self.mfr.pk, "model": PART}, "dcim.devicetype")
            self.assertIn(key, _request_obj_cache.get())
            second = self._device_type(model="Series 9200 48-port rev B", part_number=PART)
            apply_changeset(generate_changeset(second, "dcim.devicetype").change_set, request=None)
            self.assertNotIn(key, _request_obj_cache.get())
            with self.assertLogs("netbox_diode_plugin.api.matcher", level="WARNING"):
                cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
            creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
            self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        finally:
            exit_request_obj_cache(token)

    def test_forgetting_fallback_answers_spares_everything_else(self):
        """Only tagged answers for the written type leave the request cache."""
        token = enter_request_obj_cache()
        try:
            cache_map = _request_obj_cache.get()
            cache_map["identity"] = self.catalog
            cache_map["other-type"] = self.site
            forget_fallback_answers("dcim.site")
            forget_fallback_answers("dcim.devicetype")
            self.assertEqual(set(cache_map), {"identity", "other-type"})
        finally:
            exit_request_obj_cache(token)

    def _cable(self, a_type, b_type):
        def end(name, device_type):
            device = {"name": name, "site": {"name": "pnf-site"}, "role": {"name": "pnf-role"},
                      "device_type": device_type}
            return [{"object_interface": {"device": device, "name": "eth0", "type": "1000base-t"}}]
        return {"a_terminations": end("pnf-dev-a", a_type), "b_terminations": end("pnf-dev-b", b_type),
                "status": "connected", "type": "cat6"}

    def test_a_part_number_asserted_elsewhere_in_the_graph_binds_nothing(self):
        """Another device type in the same changeset asserting the part number would leave it ambiguous."""
        other = self._device_type(model="Series 9200 48-port rev B", part_number=PART)
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = sorted(c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE)
        self.assertEqual(created, sorted([PART, "Series 9200 48-port rev B"]), [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_another_manufacturer_asserting_the_part_number_leaves_the_bind(self):
        """Part numbers are scoped to the manufacturer, in the graph as in the database."""
        other = {"model": "Other vendor 48-port", "part_number": PART, "manufacturer": {"name": "pnf-other-vendor"}}
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, ["Other vendor 48-port"], [c.to_dict() for c in cs.changes])

    def test_the_same_manufacturer_by_another_selector_still_counts(self):
        """One end naming the manufacturer by name, the other by slug, is still one manufacturer."""
        other = {"model": "Series 9200 48-port rev B", "part_number": PART, "manufacturer": {"slug": "pnf-vendor"}}
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = sorted(c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE)
        self.assertEqual(created, sorted([PART, "Series 9200 48-port rev B"]), [c.to_dict() for c in cs.changes])

    def test_a_pending_placeholder_type_is_not_a_candidate(self):
        """A type named Unknown carrying the part number, created in the same graph, leaves the bind."""
        other = self._device_type(model="Unknown", part_number=PART)
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, ["Unknown"], [c.to_dict() for c in cs.changes])

    def test_an_agreeing_node_elsewhere_in_the_graph_is_not_a_candidate(self):
        """Another node asserting the part as its own model and part number adds no second row."""
        other = {"model": PART, "part_number": PART, "manufacturer": {"slug": "pnf-vendor"}}
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])

    def test_the_catalog_type_named_elsewhere_in_the_graph_is_not_a_candidate(self):
        """Another end naming the catalog type by its own model resolves to the row the fallback binds."""
        other = self._device_type(model=CATALOG_MODEL, part_number=PART)
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_giving_another_type_the_part_number_counts(self):
        """An existing type this changeset gives the part number becomes a second candidate."""
        DeviceType.objects.create(manufacturer=self.mfr, model="pnf-other-type", slug="pnf-other-type")
        other = self._device_type(model="pnf-other-type", part_number=PART)
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_placeholder_row_renamed_by_the_graph_counts(self):
        """A row named Unknown that the graph renames, keeping the part number, becomes a candidate."""
        unknown = DeviceType.objects.create(manufacturer=self.mfr, model="Unknown", slug="pnf-unknown3", part_number=PART)
        other = self._device_type(model="Series 9200 48-port renamed", part_number=PART,
                                  metadata={"source_match": {"netbox_id": unknown.pk}})
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_row_moved_to_this_manufacturer_counts(self):
        """A row of another manufacturer that the graph moves here, with the part number, becomes a candidate."""
        elsewhere = Manufacturer.objects.create(name="pnf-elsewhere", slug="pnf-elsewhere")
        moved = DeviceType.objects.create(manufacturer=elsewhere, model="pnf-moved", slug="pnf-moved", part_number=PART)
        other = self._device_type(model="pnf-moved", part_number=PART, metadata={"source_match": {"netbox_id": moved.pk}})
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_placeholder_row_renamed_without_its_part_number_counts(self):
        """An update is partial: a renamed Unknown row keeps its stored part number and becomes a candidate."""
        unknown = DeviceType.objects.create(manufacturer=self.mfr, model="Unknown", slug="pnf-unknown4", part_number=PART)
        other = self._device_type(model="Series 9200 48-port renamed", metadata={"source_match": {"netbox_id": unknown.pk}})
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_row_moved_here_without_its_part_number_counts(self):
        """A row moved to this manufacturer keeps its stored part number and becomes a candidate."""
        elsewhere = Manufacturer.objects.create(name="pnf-elsewhere2", slug="pnf-elsewhere2")
        moved = DeviceType.objects.create(manufacturer=elsewhere, model="pnf-moved2", slug="pnf-moved2", part_number=PART)
        other = self._device_type(model="pnf-moved2", metadata={"source_match": {"netbox_id": moved.pk}})
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_bound_types_new_tag_is_not_created(self):
        """A bound type is not written, so a new object only it referenced is not created either."""
        cs = generate_changeset(self._device_type(tags=[{"name": "pnf-new-tag"}]), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "extras.tag"), [], [c.to_dict() for c in cs.changes])
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])

    def test_a_tag_the_device_also_uses_is_still_created(self):
        """A child another written node references survives the bound type's drop."""
        payload = self._device("pnf-dev9", tags=[{"name": "pnf-shared-tag"}])
        payload["device_type"] = self._device_type(tags=[{"name": "pnf-shared-tag"}])
        cs = generate_changeset(payload, "dcim.device").change_set
        creates = [c for c in _writes(cs, "extras.tag") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])

    def test_the_graph_taking_the_part_number_off_the_catalog_type_counts(self):
        """Another end changing the catalog type's part number leaves nothing to bind once applied."""
        other = self._device_type(model=CATALOG_MODEL, part_number="SW-9200-48P-B",
                                  metadata={"source_match": {"netbox_id": self.catalog.pk}})
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_the_graph_clearing_the_catalog_part_number_counts(self):
        """An explicit blank part number clears it, so the catalog type stops being a candidate."""
        other = self._device_type(model=CATALOG_MODEL, part_number="")
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        created = [c.data.get("model") for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(created, [PART], [c.to_dict() for c in cs.changes])

    def test_a_node_that_binds_by_slug_elsewhere_in_the_graph_changes_nothing(self):
        """Another end reaching the catalog row by slug, with the part ID as model, is bound and writes nothing."""
        other = {"model": PART, "slug": "pnf-vendor-sw-9200-48p", "manufacturer": {"slug": "pnf-vendor"}}
        cs = generate_changeset(self._cable(self._device_type(), other), "dcim.cable").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_an_agreeing_part_number_still_binds(self):
        """A node asserting its own model as its part number does not count against itself."""
        cs = generate_changeset(self._device_type(part_number=PART), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])

    def test_one_graph_naming_the_part_twice_still_binds(self):
        """Two devices of the same part in one graph are one node, and it binds the catalog type."""
        cs = generate_changeset(self._cable(self._device_type(), self._device_type()), "dcim.cable").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])

    def test_identity_lookup_skips_a_fallback_answer_in_the_request_cache(self):
        """An apply-time lookup never takes a part-number answer cached earlier in the request."""
        data = {"manufacturer": self.mfr.pk, "model": PART}
        token = enter_request_obj_cache()
        try:
            self.assertEqual(find_existing_object(data, "dcim.devicetype", fallback=True), self.catalog)
            self.assertIsNone(find_existing_object(data, "dcim.devicetype"))
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

    def test_a_bind_leaves_the_part_number_alone(self):
        """An empty part_number in the payload is not written onto the bound type."""
        cs = generate_changeset(self._device_type(part_number=""), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_model_match_still_writes_even_when_model_is_the_part_number(self):
        """A type found by its own model is updated normally, even one whose model is its part number."""
        by_model = DeviceType.objects.create(manufacturer=self.mfr, model=PART, slug="pnf-dup2", part_number=PART)
        cs = generate_changeset(self._device_type(description="from ingest"), "dcim.devicetype").change_set
        updates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.UPDATE]
        self.assertEqual([c.object_id for c in updates], [by_model.pk], [c.to_dict() for c in cs.changes])

    def test_binds_without_writing_predicate(self):
        """Bound when the fallback found the row, or when the payload's model is the row's part number."""
        data = {"manufacturer": self.mfr.pk, "model": PART}
        self.assertTrue(binds_without_writing("dcim.devicetype", data, self.catalog))
        self.assertFalse(binds_without_writing("dcim.devicetype", {**data, "model": CATALOG_MODEL}, self.catalog))
        self.assertFalse(
            binds_without_writing("dcim.devicetype", {**data, "manufacturer": self.mfr.pk + 999}, self.catalog)
        )
        self.assertFalse(binds_without_writing("dcim.moduletype", data, self.catalog))
        found = find_existing_object(data, "dcim.devicetype", fallback=True)
        self.assertTrue(getattr(found, _FALLBACK_MATCH_ATTR, False))
        tagged = SimpleNamespace(manufacturer_id=self.mfr.pk, part_number="other", model="other")
        setattr(tagged, _FALLBACK_MATCH_ATTR, True)
        self.assertTrue(binds_without_writing("dcim.devicetype", data, tagged), "whatever the fallback found binds")

    def test_binds_without_writing_compares_the_stripped_model(self):
        """A row whose own model is the payload's model, once stripped, is not bound."""
        row = SimpleNamespace(manufacturer_id=self.mfr.pk, part_number="SW-1", model="SW-1")
        data = {"manufacturer": self.mfr.pk, "model": "SW-1 "}
        self.assertFalse(binds_without_writing("dcim.devicetype", data, row))

    def test_an_asserted_part_number_alone_creates_the_type_as_before(self):
        """A placeholder model with a part number binds nothing; the plan is today's create."""
        cs = generate_changeset(self._device_type(model="Unknown", part_number=PART), "dcim.devicetype").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_a_contradicting_part_number_creates_its_type(self):
        """A variant part number is not folded into the base part's type; the plan is today's create."""
        cs = generate_changeset(self._device_type(part_number=PART + "-AFI"), "dcim.devicetype").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_a_placeholder_named_type_leaves_the_bind_intact(self):
        """With a type named Unknown carrying the part number, the catalog type is still bound."""
        DeviceType.objects.create(manufacturer=self.mfr, model="Unknown", slug="pnf-unknown2", part_number=PART)
        cs = generate_changeset(self._device_type(), "dcim.devicetype").change_set
        self.assertEqual(_writes(cs, "dcim.devicetype"), [], [c.to_dict() for c in cs.changes])

    def test_a_bind_that_discards_fields_is_logged(self):
        """Fields a bound type carried are named at INFO, with the row they were not applied to."""
        with self.assertLogs("netbox.diode_data", level="INFO") as logs:
            generate_changeset(self._device_type(description="from ingest"), "dcim.devicetype")
        message = "\n".join(logs.output)
        self.assertIn(f"pk={self.catalog.pk}", message)
        self.assertIn("description", message)
        self.assertNotIn("from ingest", message)

    def test_a_bind_logs_warning_fields_not_their_messages(self):
        """A warning that quotes a payload value is named by its field only."""
        payload = self._device_type(metadata={"source_match": {"netbox_id": "not-a-number"}})
        with self.assertLogs("netbox.diode_data", level="INFO") as logs:
            generate_changeset(payload, "dcim.devicetype")
        message = "\n".join(logs.output)
        self.assertIn("warnings dropped for: ['metadata']", message)
        self.assertNotIn("not-a-number", message)

    def test_a_plain_bind_is_not_logged_at_info(self):
        """A discovery payload carrying only its model and manufacturer binds quietly."""
        with self.assertLogs("netbox.diode_data", level="DEBUG") as logs:
            generate_changeset(self._device_type(), "dcim.devicetype")
        bound = [r for r in logs.records if "by part number without writing" in r.getMessage()]
        self.assertEqual([r.levelname for r in bound], ["DEBUG"])

    def test_a_model_of_its_own_with_a_shared_part_number_creates_its_type(self):
        """A payload naming its own model is never folded into another model that shares the part number."""
        payload = self._device_type(model="Series 9200 48-port rev C", part_number=PART)
        cs = generate_changeset(payload, "dcim.devicetype").change_set
        creates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.CREATE]
        self.assertEqual(len(creates), 1, [c.to_dict() for c in cs.changes])
        self._assert_catalog_untouched()

    def test_deliberate_update_by_slug_still_writes(self):
        """A producer naming the row by slug, with a model of its own, updates it as before."""
        payload = self._device_type(model="Series 9200 48-port PoE", slug="pnf-vendor-sw-9200-48p", part_number=PART)
        cs = generate_changeset(payload, "dcim.devicetype").change_set
        updates = [c for c in _writes(cs, "dcim.devicetype") if c.change_type == ChangeType.UPDATE]
        self.assertEqual([c.object_id for c in updates], [self.catalog.pk], [c.to_dict() for c in cs.changes])
        self.assertEqual(updates[0].data.get("model"), "Series 9200 48-port PoE")

    def test_apply_time_recovery_never_binds_by_part_number(self):
        """A create that fails for another reason still fails; the fallback answers no conflict."""
        serializer_class = get_serializer_for_model(DeviceType)
        data = {"manufacturer": self.mfr.pk, "model": PART, "slug": "pnf-new", "airflow": "sideways"}
        with self.assertRaises(ValidationError):
            _create_or_find_instance(data, "dcim.devicetype", serializer_class, request=None)
        self._assert_catalog_untouched()


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

    def test_plan_ahead_converges_inside_one_request(self):
        """The bulk endpoints plan and apply under a request cache; the outcome is the same."""
        device, interface = self._entities("pnf-dev8")
        token = enter_request_obj_cache()
        try:
            device_plan = generate_changeset(device, "dcim.device").change_set
            interface_plan = generate_changeset(interface, "dcim.interface").change_set
            apply_changeset(device_plan, request=None)
            apply_changeset(interface_plan, request=None)
        finally:
            exit_request_obj_cache(token)
        self._assert_converged("pnf-dev8", device, interface)

    def test_plan_ahead_device_and_interface_converge(self):
        """Both planned before either applies: the second device create binds, the interface updates."""
        device, interface = self._entities("pnf-dev7")
        device_plan = generate_changeset(device, "dcim.device").change_set
        interface_plan = generate_changeset(interface, "dcim.interface").change_set
        apply_changeset(device_plan, request=None)
        apply_changeset(interface_plan, request=None)
        self._assert_converged("pnf-dev7", device, interface)


class PartNumberFallbackCustomFieldTestCase(TestCase):
    """A cached part-number answer never stands in for a unique custom field's match."""

    @classmethod
    def setUpTestData(cls):
        """A catalog-style type, and another type keyed by a unique custom field."""
        cls.mfr = Manufacturer.objects.create(name="pnf-vendor", slug="pnf-vendor")
        cls.catalog = DeviceType.objects.create(
            manufacturer=cls.mfr, model=CATALOG_MODEL, slug="pnf-vendor-sw-9200-48p", part_number=PART,
        )
        field = CustomField.objects.create(
            name="pnf_key", type=CustomFieldTypeChoices.TYPE_TEXT, required=False, unique=True,
        )
        field.object_types.set([ObjectType.objects.get_for_model(DeviceType)])
        field.save()
        cls.keyed = DeviceType.objects.create(
            manufacturer=cls.mfr, model="pnf-keyed", slug="pnf-keyed", custom_field_data={"pnf_key": "KEY-1"},
        )

    @classmethod
    def tearDownClass(cls):
        """The rolled-back custom field fires no delete signal, so drop its cached matcher here."""
        super().tearDownClass()
        _get_custom_field_matchers.cache_clear()

    def setUp(self):
        """Each test answers from the database, not from a lookup an earlier test cached."""
        django_cache.clear()

    def tearDown(self):
        """Leave no cached lookups behind for other test modules."""
        django_cache.clear()

    def test_custom_field_match_is_not_served_a_cached_fallback_answer(self):
        """The cache key omits custom fields, so a keyed payload runs its matchers again."""
        token = enter_request_obj_cache()
        try:
            plain = {"manufacturer": self.mfr.pk, "model": PART}
            self.assertEqual(find_existing_object(plain, "dcim.devicetype", fallback=True), self.catalog)
            keyed = {**plain, "custom_fields": {"pnf_key": "KEY-1"}}
            self.assertEqual(find_existing_object(keyed, "dcim.devicetype", fallback=True), self.keyed)
        finally:
            exit_request_obj_cache(token)
