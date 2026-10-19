"""Artifact bridge from strict patient EP outputs to held-out ECG evaluation.

This module is intentionally downstream-only. It reads immutable measured and
predicted ECG artifacts, verifies their hashes, checks that their time axes are
compatible with the predeclared evaluation protocol, and then delegates metric
calculation to :mod:`hearttwin.patient_ecg_eval`.

It never calls a calibration or simulation service, never searches a held-out
signal for an optimal alignment, and never performs implicit resampling.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np

from .patient_ecg_eval import (
    ECGEvaluationSpec,
    ECGValidationReport,
    ECGWaveform,
    evaluate_heldout_ecg,
)
from .patient_ep import PatientEPWorkflowResult

_ELECTROTRACE_SCHEMA = "electrotrace-ep-calibration-v1"
_PREDICTED_ECG_SCHEMAS = {
    "cardiep-ecg-v1",
    "cardiep-lead-field-ecg-v1",
}
_PREDICTED_KINDS = {"lead_field_ecg", "ecg_12lead"}
_SUPPORTED_UNITS = {"V", "mV", "uV"}


class PatientECGArtifactError(RuntimeError):
    pass


def _local_path(uri: str) -> Path:
    parsed = urlparse(str(uri))
    if parsed.scheme not in {"", "file"}:
        raise PatientECGArtifactError(
            "automatic held-out ECG evaluation currently requires local/file artifacts"
        )
    if parsed.scheme == "file":
        raw = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            raw = f"//{parsed.netloc}{raw}"
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return Path(raw).expanduser().resolve()
    return Path(str(uri)).expanduser().resolve()


def _verified_json(artifact: dict[str, Any], *, role: str) -> dict[str, Any]:
    uri = artifact.get("uri")
    digest = str(artifact.get("sha256") or "").lower()
    if not uri or len(digest) != 64:
        raise PatientECGArtifactError(f"{role} requires uri and SHA-256")
    path = _local_path(str(uri))
    if not path.is_file():
        raise PatientECGArtifactError(f"{role} artifact is missing: {path}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != digest:
        raise PatientECGArtifactError(
            f"{role} artifact failed SHA-256 verification"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PatientECGArtifactError(f"{role} artifact is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PatientECGArtifactError(f"{role} artifact must contain a JSON object")
    return payload


def _single_voltage_unit(
    *,
    observation_unit: Any,
    lead_units: dict[str, Any],
    leads: list[str],
) -> str:
    explicit = str(observation_unit) if observation_unit is not None else None
    if explicit in _SUPPORTED_UNITS:
        return explicit
    present = {str(lead_units.get(lead)) for lead in leads if lead_units.get(lead)}
    if len(present) != 1:
        raise PatientECGArtifactError(
            "ECG evaluation requires one explicit supported voltage unit across all leads"
        )
    unit = next(iter(present))
    if unit not in _SUPPORTED_UNITS:
        raise PatientECGArtifactError(f"unsupported ECG voltage unit: {unit!r}")
    return unit


def waveform_from_electrotrace_holdout(
    observation: dict[str, Any],
) -> tuple[ECGWaveform, np.ndarray, str]:
    """Load one immutable held-out ElectroTrace median-beat observation."""
    artifact = observation.get("artifact")
    if not isinstance(artifact, dict):
        raise PatientECGArtifactError("held-out observation has no artifact object")
    metadata = artifact.get("metadata") or {}
    if metadata.get("withheld_from_fit") is not True:
        raise PatientECGArtifactError(
            "held-out observation is not explicitly marked withheld_from_fit"
        )
    evidence_id = str(metadata.get("evidence_id") or "").strip()
    if not evidence_id:
        raise PatientECGArtifactError("held-out observation has no evidence_id")

    payload = _verified_json(artifact, role="held-out ElectroTrace ECG")
    if payload.get("schema_version") != _ELECTROTRACE_SCHEMA:
        raise PatientECGArtifactError("unsupported ElectroTrace calibration schema")
    if payload.get("representation") != "median_beat_template":
        raise PatientECGArtifactError(
            "held-out ECG must use ElectroTrace median_beat_template representation"
        )
    leads = [str(item) for item in payload.get("lead_names") or []]
    template = payload.get("beat_template")
    if not leads or not isinstance(template, dict):
        raise PatientECGArtifactError("ElectroTrace ECG is missing leads or beat_template")
    try:
        signals = [[float(value) for value in template[lead]] for lead in leads]
    except (KeyError, TypeError, ValueError) as exc:
        raise PatientECGArtifactError("invalid ElectroTrace beat_template") from exc

    sample_rate = float(payload.get("sampling_rate_hz", 0.0))
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise PatientECGArtifactError("invalid ElectroTrace sampling_rate_hz")
    relative_time_s = np.asarray(payload.get("relative_time_s"), dtype=float)
    if relative_time_s.ndim != 1 or len(relative_time_s) != len(signals[0]):
        raise PatientECGArtifactError(
            "ElectroTrace relative_time_s does not match waveform length"
        )
    if not np.isfinite(relative_time_s).all():
        raise PatientECGArtifactError("ElectroTrace time axis is non-finite")

    qrs = payload.get("qrs") or {}
    onset = qrs.get("median_onset_relative_ms")
    offset = qrs.get("median_offset_relative_ms")
    unit = _single_voltage_unit(
        observation_unit=observation.get("units"),
        lead_units=dict(payload.get("lead_units") or {}),
        leads=leads,
    )
    waveform = ECGWaveform(
        waveform_id=str(artifact.get("artifact_id") or observation.get("observation_id")),
        lead_names=leads,
        sample_rate_hz=sample_rate,
        signals=signals,
        unit=unit,
        qrs_onset_ms=None if onset is None else float(onset),
        qrs_offset_ms=None if offset is None else float(offset),
        metadata={
            "source": "ElectroTrace",
            "schema_version": _ELECTROTRACE_SCHEMA,
            "evidence_id": evidence_id,
            "artifact_sha256": artifact.get("sha256"),
        },
    )
    return waveform, relative_time_s * 1000.0, evidence_id


def _prediction_artifact(confirmation: dict[str, Any]) -> dict[str, Any]:
    outputs = confirmation.get("outputs")
    if not isinstance(outputs, list):
        raise PatientECGArtifactError("EP confirmation has no output artifact list")
    matches = [
        item
        for item in outputs
        if isinstance(item, dict) and item.get("kind") in _PREDICTED_KINDS
    ]
    if len(matches) != 1:
        raise PatientECGArtifactError(
            "EP confirmation must contain exactly one lead-field 12-lead ECG artifact"
        )
    return matches[0]


def waveform_from_predicted_ecg(
    confirmation: dict[str, Any],
) -> tuple[ECGWaveform, np.ndarray]:
    """Load one immutable CardiEP/openCARP predicted ECG artifact."""
    artifact = _prediction_artifact(confirmation)
    payload = _verified_json(artifact, role="predicted lead-field ECG")
    schema = str(payload.get("schema_version") or "")
    if schema not in _PREDICTED_ECG_SCHEMAS:
        raise PatientECGArtifactError(
            "predicted ECG must use cardiep-ecg-v1 or cardiep-lead-field-ecg-v1"
        )
    leads = [str(item) for item in payload.get("lead_names") or []]
    values = payload.get("values")
    if not leads or not isinstance(values, list):
        raise PatientECGArtifactError("predicted ECG is missing lead_names or values")
    sample_rate = float(payload.get("sample_rate_hz", 0.0))
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise PatientECGArtifactError("predicted ECG has invalid sample_rate_hz")
    time_ms = np.asarray(payload.get("time_ms"), dtype=float)
    if time_ms.ndim != 1 or not values or len(time_ms) != len(values[0]):
        raise PatientECGArtifactError("predicted ECG time_ms does not match waveform length")
    if not np.isfinite(time_ms).all():
        raise PatientECGArtifactError("predicted ECG time axis is non-finite")
    unit = str(payload.get("units") or artifact.get("metadata", {}).get("units") or "")
    if unit not in _SUPPORTED_UNITS:
        raise PatientECGArtifactError(
            "predicted lead-field ECG must declare physical voltage units V, mV, or uV"
        )

    waveform = ECGWaveform(
        waveform_id=str(artifact.get("artifact_id") or "predicted-ecg"),
        lead_names=leads,
        sample_rate_hz=sample_rate,
        signals=values,
        unit=unit,
        qrs_onset_ms=(
            None if payload.get("qrs_onset_ms") is None else float(payload["qrs_onset_ms"])
        ),
        qrs_offset_ms=(
            None if payload.get("qrs_offset_ms") is None else float(payload["qrs_offset_ms"])
        ),
        metadata={
            "source": "CardiEP",
            "schema_version": schema,
            "artifact_sha256": artifact.get("sha256"),
            "forward_model": artifact.get("metadata", {}).get("forward_model"),
        },
    )
    return waveform, time_ms


def _require_compatible_time_axes(
    measured_ms: np.ndarray,
    predicted_ms: np.ndarray,
    *,
    sample_rate_hz: float,
) -> None:
    if len(measured_ms) != len(predicted_ms):
        raise PatientECGArtifactError(
            "measured and predicted ECG artifacts have different sample counts; "
            "implicit resampling/cropping is not allowed"
        )
    tolerance = max(1e-6, 0.01 * (1000.0 / sample_rate_hz))
    if not np.allclose(measured_ms, predicted_ms, rtol=0.0, atol=tolerance):
        raise PatientECGArtifactError(
            "measured and predicted ECG time axes differ; provide a predeclared compatible "
            "forward-output grid rather than post-hoc alignment"
        )


def evaluate_patient_ep_holdouts(
    result: PatientEPWorkflowResult | dict[str, Any],
    *,
    spec: ECGEvaluationSpec,
) -> list[ECGValidationReport]:
    """Evaluate a completed patient EP prediction against all held-out ECG artifacts."""
    payload = (
        result.model_dump(mode="json")
        if isinstance(result, PatientEPWorkflowResult)
        else dict(result)
    )
    confirmation = payload.get("confirmation")
    if not isinstance(confirmation, dict):
        raise PatientECGArtifactError("patient EP result has no confirmation payload")
    predicted, predicted_time_ms = waveform_from_predicted_ecg(confirmation)

    holdouts = payload.get("holdout_observations")
    if not isinstance(holdouts, list) or not holdouts:
        raise PatientECGArtifactError("patient EP result has no held-out ECG observations")

    reports: list[ECGValidationReport] = []
    for observation in holdouts:
        if not isinstance(observation, dict):
            raise PatientECGArtifactError("held-out observation must be an object")
        measured, measured_time_ms, evidence_id = waveform_from_electrotrace_holdout(
            observation
        )
        if abs(measured.sample_rate_hz - predicted.sample_rate_hz) > 1e-9:
            raise PatientECGArtifactError(
                "measured and predicted ECG sample rates differ; implicit resampling is not allowed"
            )
        _require_compatible_time_axes(
            measured_time_ms,
            predicted_time_ms,
            sample_rate_hz=measured.sample_rate_hz,
        )
        reports.append(
            evaluate_heldout_ecg(
                measured=measured,
                predicted=predicted,
                evidence_id=evidence_id,
                withheld_from_fit=True,
                spec=spec,
            )
        )
    return reports
