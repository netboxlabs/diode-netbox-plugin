"""Guardrail: every legal wire field must be accounted for by the gate."""
from django.test import TestCase

from netbox_diode_plugin.api.plugin_utils import get_json_ref_info, legal_fields
from netbox_diode_plugin.api.supported_models import (
    extract_supported_models,
    get_serializer_for_model,
)

# Wires with dedicated ingest paths outside the per-field gate.
EXEMPT_WIRES = {"custom_fields", "metadata"}

# Models with no DRF serializer; the gate falls back to the legacy model walk.
NO_SERIALIZER_TYPES = {"core.managedfile"}

# The only intended supported-set changes of the serializer-aware gate on this
# NetBox version. Anything else appearing in the delta is an unreviewed
# behavior change: triage it against the design spec before touching this.
EXPECTED_GAINS = ["dcim.moduletype.attributes"]
# Phantom entries: the model keeps deprecated device/virtual_machine fields
# but the serializer replaced them with the parent generic FK, and the
# pre-gate compat migration rewrites those wires to parent_object_* anyway —
# the old gate's "support" was unreachable and the applier ignored the keys.
EXPECTED_LOSSES = [
    "ipam.service.device",
    "ipam.service.virtual_machine",
]


def _legacy_model_walk(model_class):
    """Frozen copy of the pre-serializer-gate field selection (names only)."""
    legal = legal_fields(model_class)
    names = set()
    for field in model_class._meta.get_fields():
        if field.name in legal or field.name == "id":
            names.add(field.name)
    return names


class LegalFieldAccountingTests(TestCase):
    """No legal field may be silently unclassified, and the delta is pinned."""

    @classmethod
    def setUpTestData(cls):
        """Extract once; the function is process-cached."""
        cls.supported = extract_supported_models()

    def test_every_legal_field_is_accounted_for(self):
        """Each legal wire is supported, loud, ref-handled, stale, or exempt."""
        unaccounted = []
        for object_type, entry in self.supported.items():
            if object_type in NO_SERIALIZER_TYPES:
                continue
            ser_fields = get_serializer_for_model(entry["model"])().get_fields()
            for wire in legal_fields(object_type):
                if wire in EXEMPT_WIRES:
                    continue
                if wire in entry["fields"] or wire in entry["serializer_only_fields"]:
                    continue
                ref_info = get_json_ref_info(object_type, wire)
                if ref_info is not None and ref_info.is_generic_object:
                    continue  # ref-handled (e.g. cable terminations)
                f = ser_fields.get(wire)
                if f is None or f.read_only:
                    continue  # version-stale on this NetBox version
                unaccounted.append(f"{object_type}.{wire}")
        self.assertEqual(unaccounted, [])

    def test_gate_delta_vs_legacy_walk_is_exactly_intended(self):
        """The old-vs-new supported-fields diff equals the documented delta."""
        gains, losses = [], []
        for object_type, entry in self.supported.items():
            legacy = _legacy_model_walk(entry["model"])
            new = set(entry["fields"])
            gains += [f"{object_type}.{w}" for w in sorted(new - legacy)]
            losses += [f"{object_type}.{w}" for w in sorted(legacy - new)]
        self.assertEqual(sorted(gains), EXPECTED_GAINS)
        self.assertEqual(sorted(losses), EXPECTED_LOSSES)
