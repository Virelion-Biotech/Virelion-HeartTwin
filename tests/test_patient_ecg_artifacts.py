from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from hearttwin.patient_ecg_artifacts import (
    PatientECGArtifactError,
    _local_path,
    evaluate_patient_ep_holdouts,
)
from hearttwin.patient_ecg_eval import ECGEvaluationSpec

LEADS = [
    "I",
    "II",
    "III",
    "aVR",
    "aVL",
    "aVF",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
]


def _write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture_result(tmp_path: Path) -> dict:
    sample_rate = 500.0
    time_ms = [-4.0, -2.0, 0.0, 2.0, 4.0, 6.0]
    signals = {
        lead: [float(index + offset) for index in range(len(time_ms))]
        for offset, lead in enumerate(LEADS)
    }
    measured_payload = {
        "schema_version": "electrotrace-ep-calibration-v1",
        "kind": "ecg",
        "representation": "median_beat_template",
        "sampling_rate_hz": sample_rate,
        "relative_time_s": [value / 1000.0 for value in time_ms],
        "lead_names": LEADS,
        "lead_units": {lead: "mV" for lead in LEADS},
        "beat_template": signals,
        "qrs": {
            "median_onset_relative_ms": -2.0,
            "median_offset_relative_ms": 4.0,
        },
    }
    measured_path = tmp_path / "holdout.json"
    measured_sha = _write_json(measured_path, measured_payload)

    predicted_payload = {
        "schema_version": "cardiep-lead-field-ecg-v1",
        "lead_names": LEADS,
        "time_ms": time_ms,
        "values": [signals[lead] for lead in LEADS],
        "sample_rate_hz": sample_rate,
        "units": "mV",
        "qrs_onset_ms": -2.0,
        "qrs_offset_ms": 4.0,
    }
    predicted_path = tmp_path / "predicted.json"
    predicted_sha = _write_json(predicted_path, predicted_payload)

    return {
        "confirmation": {
            "subject_id": "P1",
            "backend": "opencarp-external-v1",
            "outputs": [
                {
                    "artifact_id": "predicted-ecg",
                    "kind": "lead_field_ecg",
                    "uri": predicted_path.resolve().as_uri(),
                    "sha256": predicted_sha,
                    "metadata": {
                        "forward_model": "lead_field",
                        "lead_names": LEADS,
                        "sample_rate_hz": sample_rate,
                    },
                }
            ],
        },
        "holdout_observations": [
            {
                "observation_id": "holdout",
                "kind": "ecg",
                "artifact": {
                    "artifact_id": "holdout-artifact",
                    "kind": "electrotrace_ecg_calibration",
                    "uri": measured_path.resolve().as_uri(),
                    "sha256": measured_sha,
                    "metadata": {
                        "withheld_from_fit": True,
                        "evidence_id": "holdout-evidence",
                    },
                },
                "units": "mV",
            }
        ],
    }


def test_evaluates_hash_pinned_holdout_without_solver_access(tmp_path: Path) -> None:
    reports = evaluate_patient_ep_holdouts(
        _fixture_result(tmp_path),
        spec=ECGEvaluationSpec(),
    )
    assert len(reports) == 1
    report = reports[0]
    assert report.evidence_id == "holdout-evidence"
    assert report.withheld_from_fit is True
    assert report.mean_nrmse == pytest.approx(0.0)
    assert report.mean_correlation == pytest.approx(1.0)
    assert report.qrs_duration_error_ms == pytest.approx(0.0)


def test_rejects_tampered_holdout_bytes(tmp_path: Path) -> None:
    result = _fixture_result(tmp_path)
    holdout_uri = result["holdout_observations"][0]["artifact"]["uri"]
    _local_path(holdout_uri).write_text("tampered", encoding="utf-8")
    with pytest.raises(PatientECGArtifactError, match="SHA-256"):
        evaluate_patient_ep_holdouts(result, spec=ECGEvaluationSpec())


def test_rejects_non_withheld_measurement(tmp_path: Path) -> None:
    result = _fixture_result(tmp_path)
    result["holdout_observations"][0]["artifact"]["metadata"]["withheld_from_fit"] = False
    with pytest.raises(PatientECGArtifactError, match="withheld_from_fit"):
        evaluate_patient_ep_holdouts(result, spec=ECGEvaluationSpec())


def test_rejects_posthoc_time_axis_mismatch(tmp_path: Path) -> None:
    result = _fixture_result(tmp_path)
    artifact = result["confirmation"]["outputs"][0]
    path = _local_path(artifact["uri"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["time_ms"] = [value + 2.0 for value in payload["time_ms"]]
    artifact["sha256"] = _write_json(path, payload)
    with pytest.raises(PatientECGArtifactError, match="time axes differ"):
        evaluate_patient_ep_holdouts(result, spec=ECGEvaluationSpec())


def test_rejects_dimensionless_predicted_ecg(tmp_path: Path) -> None:
    result = _fixture_result(tmp_path)
    artifact = result["confirmation"]["outputs"][0]
    path = _local_path(artifact["uri"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["units"] = "a.u."
    artifact["sha256"] = _write_json(path, payload)
    with pytest.raises(PatientECGArtifactError, match="physical voltage units"):
        evaluate_patient_ep_holdouts(result, spec=ECGEvaluationSpec())


def test_rejects_multiple_holdouts_without_prediction_mapping(tmp_path: Path) -> None:
    result = _fixture_result(tmp_path)
    second = deepcopy(result["holdout_observations"][0])
    second["observation_id"] = "holdout-2"
    second["artifact"]["artifact_id"] = "holdout-artifact-2"
    second["artifact"]["metadata"]["evidence_id"] = "holdout-evidence-2"
    result["holdout_observations"].append(second)

    with pytest.raises(PatientECGArtifactError, match="exactly one held-out ECG"):
        evaluate_patient_ep_holdouts(result, spec=ECGEvaluationSpec())
