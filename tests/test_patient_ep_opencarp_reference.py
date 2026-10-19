from __future__ import annotations

import pytest

from hearttwin.patient_ep import PatientEPWorkflowError
from hearttwin.patient_ep_strict import (
    _calibration_activation_reference,
    _configure_confirmation_settings,
)
from hearttwin.patient_model import PatientEPWorkflowSpec


def _spec(
    *,
    fast_backend: str = "camps-cdt-external-v1",
    confirmation_backend: str = "opencarp-external-v1",
) -> PatientEPWorkflowSpec:
    return PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct-1",
        calibration_evidence_ids=["fit"],
        validation_evidence_ids=["holdout"],
        fast_backend=fast_backend,
        confirmation_backend=confirmation_backend,
        observation_model="lead_field",
        scientific_grade=True,
        torso_mode="standardized",
        electrode_mode="protocol_derived",
    )


def _calibration() -> dict:
    return {
        "subject_id": "P1",
        "backend": "camps-cdt-external-v1",
        "simulated": {
            "outputs": [
                {
                    "artifact_id": "P1-activation",
                    "kind": "activation_map",
                    "uri": "file:///activation.json",
                    "sha256": "a" * 64,
                    "coordinate_frame": "ventricular-model",
                    "metadata": {"units": "ms"},
                },
                {
                    "artifact_id": "P1-fit-ecg",
                    "kind": "calibration_ecg",
                    "uri": "file:///fit-ecg.json",
                    "sha256": "b" * 64,
                },
            ]
        },
    }


def _mapping_result() -> dict:
    return {
        "mapping_id": "camps-to-opencarp-v1",
        "mapping_version": "1.0",
        "mapping_fingerprint": "c" * 64,
        "source_parameter_fingerprint": "d" * 64,
    }


def test_camps_to_opencarp_preserves_hash_pinned_activation_target() -> None:
    reference = _calibration_activation_reference(
        calibration=_calibration(),
        spec=_spec(),
        anatomy_bundle_fingerprint="e" * 64,
        mapping_result=_mapping_result(),
    )

    assert reference is not None
    assert reference["role"] == "activation_target_from_fast_calibration"
    assert reference["source_backend"] == "camps-cdt-external-v1"
    assert reference["anatomy_bundle_fingerprint"] == "e" * 64
    assert reference["parameter_mapping_fingerprint"] == "c" * 64
    assert reference["activation_ref"]["artifact_id"] == "P1-activation"
    assert reference["activation_ref"]["sha256"] == "a" * 64
    assert reference["activation_ref"]["coordinate_frame"] == "ventricular-model"


def test_camps_to_opencarp_rejects_missing_activation_map() -> None:
    calibration = _calibration()
    calibration["simulated"]["outputs"] = [
        item
        for item in calibration["simulated"]["outputs"]
        if item["kind"] != "activation_map"
    ]
    with pytest.raises(PatientEPWorkflowError, match="exactly one activation_map"):
        _calibration_activation_reference(
            calibration=calibration,
            spec=_spec(),
            anatomy_bundle_fingerprint="e" * 64,
            mapping_result=_mapping_result(),
        )


def test_camps_to_opencarp_requires_mapping_fingerprint() -> None:
    mapping = _mapping_result()
    mapping["mapping_fingerprint"] = "bad"
    with pytest.raises(PatientEPWorkflowError, match="mapping fingerprint"):
        _calibration_activation_reference(
            calibration=_calibration(),
            spec=_spec(),
            anatomy_bundle_fingerprint="e" * 64,
            mapping_result=mapping,
        )


def test_other_backend_pair_does_not_invent_activation_reference() -> None:
    reference = _calibration_activation_reference(
        calibration=_calibration(),
        spec=_spec(fast_backend="other-fast-v1", confirmation_backend="other-pde-v1"),
        anatomy_bundle_fingerprint="e" * 64,
        mapping_result=_mapping_result(),
    )
    assert reference is None


def test_opencarp_settings_are_bound_to_patient_workflow_spec() -> None:
    reference = _calibration_activation_reference(
        calibration=_calibration(),
        spec=_spec(),
        anatomy_bundle_fingerprint="e" * 64,
        mapping_result=_mapping_result(),
    )
    settings = _configure_confirmation_settings(
        spec=_spec(),
        settings={"wrapper_setting": "kept"},
        calibration_reference=reference,
    )
    assert settings["wrapper_setting"] == "kept"
    assert settings["ecg_forward_model"] == "lead_field"
    assert settings["torso_mode"] == "standardized"
    assert settings["electrode_mode"] == "protocol_derived"
    assert set(settings["expected_leads"]) == {
        "I", "II", "III", "aVR", "aVL", "aVF",
        "V1", "V2", "V3", "V4", "V5", "V6",
    }
    assert settings["hearttwin_calibration_reference"] == reference


def test_opencarp_settings_reject_conflicting_observation_model() -> None:
    with pytest.raises(PatientEPWorkflowError, match="conflicts"):
        _configure_confirmation_settings(
            spec=_spec(),
            settings={"ecg_forward_model": "pseudo_ecg"},
            calibration_reference=None,
        )
