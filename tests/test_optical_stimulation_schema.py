import json
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver


SCHEMA_DIR = Path(__file__).parents[1] / "schemas"
OPTICAL_SCHEMA = SCHEMA_DIR / "optical-stimulation-1.0.0.schema.json"
TWIN_INPUT_SCHEMA = SCHEMA_DIR / "cardiac-twin-input-1.0.0.schema.json"


def _example_protocol():
    return {
        "schema_version": "virelion.optical-stimulation/1.0.0",
        "protocol_id": "paced-hiPSC-CM-001",
        "modality": "optogenetic",
        "target": {
            "cell_type": "hiPSC-derived cardiomyocyte",
            "anatomical_region": None,
            "spatial_pattern": "global",
            "geometry_ref": None,
        },
        "actuator": {
            "name": "CheRiff2.0",
            "class": "opsin",
            "expression_method": "viral",
        },
        "light": {
            "wavelength_nm": 470.0,
            "irradiance_mw_mm2": 0.8,
            "pulse_width_ms": 5.0,
            "frequency_hz": 2.0,
        },
        "timing": {"start_ms": 0.0, "duration_ms": 1000.0},
        "control": {
            "mode": "open_loop",
            "feedback_signal": None,
            "controller_ref": None,
        },
        "provenance": {"source": "experimental", "source_id": "fixture"},
    }


def test_optical_stimulation_schema_is_valid_and_accepts_protocol():
    schema = json.loads(OPTICAL_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(_example_protocol()))
    assert not errors, [error.message for error in errors]


def test_cardiac_twin_input_accepts_optional_optogenetic_stimulation():
    schema = json.loads(TWIN_INPUT_SCHEMA.read_text(encoding="utf-8"))
    resolver = RefResolver(base_uri=SCHEMA_DIR.as_uri() + "/", referrer=schema)
    validator = Draft202012Validator(schema, resolver=resolver)
    payload = {
        "entity_id": "fixture",
        "geometry": {
            "node_xyz": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "tetrahedra": [[0, 1, 2, 3]],
        },
        "root_nodes": [0],
        "optogenetic_stimulation": _example_protocol(),
    }
    errors = list(validator.iter_errors(payload))
    assert not errors, [error.message for error in errors]


def test_optical_contract_rejects_unknown_fields():
    protocol = _example_protocol()
    protocol["therapeutic_claim"] = "not allowed"
    schema = json.loads(OPTICAL_SCHEMA.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(protocol))
