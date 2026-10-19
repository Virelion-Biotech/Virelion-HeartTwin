from __future__ import annotations

from types import SimpleNamespace

import pytest

from hearttwin.patient_ep import PatientEPWorkflowError
from hearttwin.patient_ep_strict import _calibration_activation_reference


def _spec(*, fast: str = "numpy-eikonal-v1", confirmation: str = "opencarp-external-v1"):
    return SimpleNamespace(fast_backend=fast, confirmation_backend=confirmation)


def _calibration() -> dict:
    return {
        "simulated": {
            "outputs": [
                {
                    "artifact_id": "activation-native",
                    "kind": "activation_map",
                    "uri": "file:///tmp/activation.json",
                    "sha256": "a" * 64,
                    "coordinate_frame": "patient-model",
                }
            ]
        }
    }


def _mapping() -> dict:
    return {"mapping_fingerprint": "b" * 64}


def test_native_activation_is_transferred_into_opencarp_confirmation() -> None:
    result = _calibration_activation_reference(
        calibration=_calibration(),
        spec=_spec(),  # type: ignore[arg-type]
        anatomy_bundle_fingerprint="c" * 64,
        mapping_result=_mapping(),
    )
    assert result is not None
    assert result["role"] == "activation_target_from_fast_calibration"
    assert result["source_backend"] == "numpy-eikonal-v1"
    assert result["activation_ref"]["artifact_id"] == "activation-native"
    assert result["parameter_mapping_fingerprint"] == "b" * 64


def test_activation_handoff_remains_open_carp_specific() -> None:
    result = _calibration_activation_reference(
        calibration=_calibration(),
        spec=_spec(confirmation="numpy-eikonal-v1"),  # type: ignore[arg-type]
        anatomy_bundle_fingerprint="c" * 64,
        mapping_result=_mapping(),
    )
    assert result is None


def test_cross_backend_opencarp_handoff_requires_mapping_fingerprint() -> None:
    with pytest.raises(PatientEPWorkflowError, match="mapping result"):
        _calibration_activation_reference(
            calibration=_calibration(),
            spec=_spec(),  # type: ignore[arg-type]
            anatomy_bundle_fingerprint="c" * 64,
            mapping_result=None,
        )


def test_cross_backend_opencarp_handoff_rejects_unpinned_activation() -> None:
    calibration = _calibration()
    calibration["simulated"]["outputs"][0]["sha256"] = "not-a-sha"
    with pytest.raises(PatientEPWorkflowError, match="SHA-256 pinned"):
        _calibration_activation_reference(
            calibration=calibration,
            spec=_spec(),  # type: ignore[arg-type]
            anatomy_bundle_fingerprint="c" * 64,
            mapping_result=_mapping(),
        )
