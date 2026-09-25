#!/usr/bin/env python
# Copyright 2026 NetBox Labs, Inc.
"""Diode NetBox Plugin - Tests: cooling components instantiated from templates."""

import uuid
from decimal import Decimal

from dcim.models import (
    CoolingIntake,
    CoolingIntakeTemplate,
    CoolingOutflow,
    CoolingOutflowTemplate,
    Device,
    DeviceType,
    Manufacturer,
)

from .test_api_apply_change_set import BaseApplyChangeSet


class CoolingComponentTemplateTestCase(BaseApplyChangeSet):
    """
    Cooling intakes and outflows are instantiated from device-type templates.

    NetBox 4.7 creates them on the device's first save exactly like interfaces
    and the other component templates, so a CREATE for such a component must be
    applied to the instantiated row -- not duplicated, and not silently dropped
    in favour of the template's defaults.
    """

    @staticmethod
    def _create(object_type, ref_id, data, new_refs=None):
        change = {
            "change_id": str(uuid.uuid4()),
            "change_type": "create",
            "object_version": None,
            "object_type": object_type,
            "object_id": None,
            "ref_id": ref_id,
            "data": data,
        }
        if new_refs:
            change["new_refs"] = new_refs
        return change

    @staticmethod
    def _payload(*changes):
        return {"id": str(uuid.uuid4()), "changes": list(changes)}

    def setUp(self):
        """A device type whose templates yield one intake and one outflow."""
        super().setUp()
        self.device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.first(),
            model="Device with Cooling Templates",
            slug="device-with-cooling-templates",
        )
        CoolingIntakeTemplate.objects.create(device_type=self.device_type, name="intake-1")
        CoolingOutflowTemplate.objects.create(device_type=self.device_type, name="outflow-1")

    def _device_change(self):
        return self._create("dcim.device", "device-1", {
            "name": "Cooled Device",
            "device_type": self.device_type.id,
            "role": self.roles[0].id,
            "site": self.sites[0].id,
        })

    def _intake_data(self, device):
        return {
            "name": "intake-1",
            "device": device,
            "type": "uqd",
            "diameter": "12.70",
            "diameter_unit": "mm",
            "max_flow": "3.50",
            "max_flow_unit": "lpm",
            "description": "Ingested intake",
        }

    def _outflow_data(self, device, intake):
        return {
            "name": "outflow-1",
            "device": device,
            "type": "qdc",
            "diameter": "12.70",
            "diameter_unit": "mm",
            "cooling_intake": intake,
            "description": "Ingested outflow",
        }

    def _assert_ingested(self, device):
        intakes = CoolingIntake.objects.filter(device=device, name="intake-1")
        outflows = CoolingOutflow.objects.filter(device=device, name="outflow-1")
        self.assertEqual(intakes.count(), 1, "intake should be reused, not duplicated")
        self.assertEqual(outflows.count(), 1, "outflow should be reused, not duplicated")
        intake, outflow = intakes.get(), outflows.get()
        self.assertEqual(intake.type, "uqd")
        self.assertEqual(intake.diameter, Decimal("12.70"))
        self.assertEqual(intake.diameter_unit, "mm")
        self.assertEqual(intake.max_flow, Decimal("3.50"))
        self.assertEqual(intake.max_flow_unit, "lpm")
        self.assertEqual(intake.description, "Ingested intake")
        self.assertEqual(outflow.type, "qdc")
        self.assertEqual(outflow.diameter, Decimal("12.70"))
        self.assertEqual(outflow.cooling_intake_id, intake.id)
        self.assertEqual(outflow.description, "Ingested outflow")

    def test_device_save_instantiates_cooling_components(self):
        """Premise: NetBox instantiates both templates with their defaults."""
        self.send_request(self._payload(self._device_change()))
        device = Device.objects.get(name="Cooled Device")
        intake = CoolingIntake.objects.get(device=device, name="intake-1")
        outflow = CoolingOutflow.objects.get(device=device, name="outflow-1")
        self.assertIsNone(intake.type)
        self.assertEqual(intake.description, "")
        self.assertIsNone(outflow.cooling_intake)

    def test_device_and_cooling_components_in_one_changeset(self):
        """The discovery shape: device, intake and outflow created together by ref."""
        self.send_request(self._payload(
            self._device_change(),
            self._create("dcim.coolingintake", "intake-1",
                         self._intake_data("device-1"), new_refs=["device"]),
            self._create("dcim.coolingoutflow", "outflow-1",
                         self._outflow_data("device-1", "intake-1"),
                         new_refs=["device", "cooling_intake"]),
        ))
        self._assert_ingested(Device.objects.get(name="Cooled Device"))

    def test_cooling_components_ingested_after_the_device(self):
        """A later request against an existing device updates the instantiated rows."""
        self.send_request(self._payload(self._device_change()))
        device = Device.objects.get(name="Cooled Device")
        intake = CoolingIntake.objects.get(device=device, name="intake-1")
        self.send_request(self._payload(
            self._create("dcim.coolingintake", "intake-1", self._intake_data(device.id)),
        ))
        self.send_request(self._payload(
            self._create("dcim.coolingoutflow", "outflow-1",
                         self._outflow_data(device.id, intake.id)),
        ))
        self._assert_ingested(device)
