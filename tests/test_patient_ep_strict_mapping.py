from __future__ import annotations

import pytest

from hearttwin.ep_parameter_mapping import EPParameterMappingSpec
from hearttwin.patient_ep import PatientEPWorkflowError
from hearttwin.patient_ep_strict import (
    _confirmation_parameters,
    _require_safe_parameter_replay,
)
from hearttwin.patient_model import PatientEPWorkflowSpec


def _spec() -> PatientEPWorkflowSpec:
    return PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="A1",
        calibration_evidence_ids=["fit"],
        validation_evidence_ids=["holdout"],
        fast_backend="fast-eikonal-v1",
        confirmation_backend="opencarp-external-v1",
        observation_model="lead_field",
        scientific_grade=True,
    )


def _mapping() -> EPParameterMappingSpec:
    return EPParameterMappingSpec(
        mapping_id="eikonal-to-opencarp-v1",
        mapping_version="1.0.0",
        source_backend="fast-eikonal-v1",
        target_backend="opencarp-external-v1",
        intended_use="Translate calibrated conduction velocities for confirmation replay.",
        rules=[
            {
                "source_parameter": "fibre_speed",
                "target_parameter": "longitudinal_conduction_velocity",
                "source_unit": "cm/ms",
                "target_unit": "m/s",
                "scale": 10.0,
                "rationale": "1 cm/ms equals 10 m/s; explicit unit conversion.",
            },
            {
                "source_parameter": "transverse_speed",
                "target_parameter": "transverse_conduction_velocity",
                "source_unit": "cm/ms",
                "target_unit": "m/s",
                "scale": 10.0,
                "rationale": "1 cm/ms equals 10 m/s; explicit unit conversion.",
            },
        ],
        ignored_source_parameters=[
            {
                "source_parameter": "apd_ms",
                "reason": "QRS-only confirmation does not replay repolarization in this contract.",
            }
        ],
    )


def test_cross_backend_replay_accepts_explicit_mapping() -> None:
    mapping = _require_safe_parameter_replay(_spec(), _mapping())
    assert mapping is not None
    assert mapping.mapping_id == "eikonal-to-opencarp-v1"


def test_cross_backend_replay_still_rejects_missing_mapping() -> None:
    with pytest.raises(PatientEPWorkflowError, match="implicit cross-backend"):
        _require_safe_parameter_replay(_spec())


def test_confirmation_parameters_apply_declared_unit_mapping() -> None:
    payload, audit = _confirmation_parameters(
        spec=_spec(),
        parameter_payload={
            "values": {
                "fibre_speed": 0.06,
                "transverse_speed": 0.025,
                "apd_ms": 280.0,
            },
            "units": {
                "fibre_speed": "cm/ms",
                "transverse_speed": "cm/ms",
                "apd_ms": "ms",
            },
        },
        mapping=_mapping(),
        fallback_units=None,
    )
    assert payload["source"] == "calibrated"
    assert payload["values"]["longitudinal_conduction_velocity"] == pytest.approx(0.6)
    assert payload["values"]["transverse_conduction_velocity"] == pytest.approx(0.25)
    assert payload["units"]["longitudinal_conduction_velocity"] == "m/s"
    assert audit is not None
    assert audit["ignored_source_parameters"] == ["apd_ms"]


def test_confirmation_parameters_reject_incomplete_mapping_coverage() -> None:
    with pytest.raises(PatientEPWorkflowError, match="does not account"):
        _confirmation_parameters(
            spec=_spec(),
            parameter_payload={
                "values": {
                    "fibre_speed": 0.06,
                    "transverse_speed": 0.025,
                    "apd_ms": 280.0,
                    "mystery_parameter": 1.0,
                },
                "units": {
                    "fibre_speed": "cm/ms",
                    "transverse_speed": "cm/ms",
                    "apd_ms": "ms",
                    "mystery_parameter": "1",
                },
            },
            mapping=_mapping(),
            fallback_units=None,
        )
