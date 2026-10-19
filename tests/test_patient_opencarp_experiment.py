from __future__ import annotations

from copy import deepcopy

import pytest

from hearttwin.patient_ep_declared import run_strict_patient_ep_workflow
from hearttwin.patient_model import PatientEPWorkflowSpec
from hearttwin.patient_opencarp_experiment import inspect_opencarp_experiment

LEADS = [
    "I", "II", "III", "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
]


def _workflow() -> PatientEPWorkflowSpec:
    return PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct-1",
        calibration_evidence_ids=["fit"],
        validation_evidence_ids=["holdout"],
        fast_backend="camps-cdt-external-v1",
        confirmation_backend="opencarp-external-v1",
        observation_model="lead_field",
        scientific_grade=True,
        torso_mode="standardized",
        electrode_mode="protocol_derived",
    )


def _experiment() -> dict:
    return {
        "contract_version": "1.0",
        "experiment_id": "patient-qrs-confirmation",
        "experiment_version": "1.0.0",
        "intended_use": "Confirm one calibrated patient ventricular activation/QRS model.",
        "field_model": "monodomain",
        "activation_model": "reaction_eikonal_plus",
        "ecg": {
            "forward_model": "lead_field",
            "torso_mode": "standardized",
            "electrode_mode": "protocol_derived",
            "expected_leads": LEADS,
        },
        # Additional CardiEP-owned fields intentionally remain opaque to HeartTwin.
        "tissue_regions": [{"region_id": "myocardium"}],
        "solver_options": {"dt": 0.02},
    }


def test_declaration_fingerprints_complete_opaque_experiment() -> None:
    raw = _experiment()
    declaration = inspect_opencarp_experiment(raw, workflow=_workflow())
    assert declaration.experiment_id == "patient-qrs-confirmation"
    assert declaration.experiment_version == "1.0.0"
    assert declaration.field_model == "monodomain"
    assert declaration.activation_model == "reaction_eikonal_plus"
    assert len(declaration.fingerprint) == 64

    changed = deepcopy(raw)
    changed["solver_options"]["dt"] = 0.01
    changed_declaration = inspect_opencarp_experiment(changed, workflow=_workflow())
    assert changed_declaration.fingerprint != declaration.fingerprint


def test_declaration_rejects_patient_workflow_mode_mismatch() -> None:
    raw = _experiment()
    raw["ecg"]["torso_mode"] = "patient_specific"
    with pytest.raises(ValueError, match="torso_mode"):
        inspect_opencarp_experiment(raw, workflow=_workflow())


def test_declaration_rejects_nonstandard_lead_set() -> None:
    raw = _experiment()
    raw["ecg"]["expected_leads"] = ["I", "II"]
    with pytest.raises(ValueError, match="standard 12 leads"):
        inspect_opencarp_experiment(raw, workflow=_workflow())


class _FakeResult:
    def __init__(self, confirmation=None):
        self.confirmation = confirmation or {
            "backend": "opencarp-external-v1",
            "provenance": {"engine": "openCARP"},
        }

    def model_copy(self, *, update):
        return _FakeResult(confirmation=update["confirmation"])


def test_public_declared_wrapper_fails_before_core_when_spec_missing(monkeypatch) -> None:
    called = False

    def _core(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("core workflow must not run before declaration validation")

    monkeypatch.setattr("hearttwin.patient_ep_declared._run_core", _core)
    with pytest.raises(ValueError, match="opencarp_experiment_spec"):
        run_strict_patient_ep_workflow(
            object(),  # type: ignore[arg-type]
            case=object(),  # type: ignore[arg-type]
            spec=_workflow(),
            anatomy_bundle={},
            parameter_bounds={"cv": (0.1, 1.0)},
            confirmation_settings={},
        )
    assert called is False


def test_public_declared_wrapper_stamps_experiment_provenance(monkeypatch) -> None:
    raw = _experiment()
    captured = {}

    def _core(registry, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr("hearttwin.patient_ep_declared._run_core", _core)
    result = run_strict_patient_ep_workflow(
        object(),  # type: ignore[arg-type]
        case=object(),  # type: ignore[arg-type]
        spec=_workflow(),
        anatomy_bundle={},
        parameter_bounds={"cv": (0.1, 1.0)},
        confirmation_settings={"opencarp_experiment_spec": raw},
    )

    assert captured["confirmation_settings"]["opencarp_experiment_spec"] == raw
    envelope = result.confirmation["provenance"]["hearttwin_opencarp_experiment"]
    assert envelope["experiment_id"] == "patient-qrs-confirmation"
    assert envelope["experiment_version"] == "1.0.0"
    assert len(envelope["fingerprint"]) == 64
