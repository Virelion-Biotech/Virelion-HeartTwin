"""Canonical patient ECG measurement preparation through ElectroTrace.

Scientific patient-EP calibration must not hand vendor/raw ECG files directly to
an inverse solver.  This module turns acquisition-backed PatientCase evidence
into ElectroTrace's hash-pinned median-beat calibration artifact, verifies that
its source lineage agrees with the PatientCase, and only then emits a CardiEP
observation contract.
"""
from __future__ import annotations

import string
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from .patient_ep import (
    PatientEPWorkflowError,
    _evidence_acquisition,
    _primary_source,
)
from .patient_model import PatientCase, PatientEvidenceItem
from .service_registry import ServiceRegistry

ELECTROTRACE_CALIBRATION_SCHEMA = "electrotrace-ep-calibration-v1"
ELECTROTRACE_ARTIFACT_KIND = "electrotrace_ecg_calibration"
_STANDARD_12_LEADS = {
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
}


class PreparedECGEvidence(BaseModel):
    """Verified ElectroTrace handoff for one patient evidence item."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    acquisition_id: str
    source_file_id: str
    source_sha256: str
    observation: dict[str, Any]
    likelihood_hints: list[dict[str, Any]] = Field(default_factory=list)
    handoff_summary: dict[str, Any] = Field(default_factory=dict)


def _sha256(value: Any, *, field: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(char not in string.hexdigits for char in text):
        raise PatientEPWorkflowError(f"{field} must be a 64-character SHA-256 hex string")
    return text


def _source_local_path(uri: str) -> str:
    """Resolve only local/file URIs accepted by ElectroTrace's command adapter."""
    parsed = urlparse(str(uri))
    if parsed.scheme == "":
        return str(Path(str(uri)).expanduser())
    if parsed.scheme != "file":
        raise PatientEPWorkflowError(
            "ElectroTrace patient calibration currently requires a local/file ECG source URI"
        )
    path = unquote(parsed.path)
    # file:///C:/... is represented with a leading slash by urlparse.
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _invoke_electrotrace(
    registry: ServiceRegistry,
    *,
    patient_id: str,
    evidence_id: str,
    source_path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    adapter = registry.capability("electrical.prepare_calibration")
    if adapter is None:
        raise PatientEPWorkflowError(
            "Scientific patient ECG calibration requires electrical.prepare_calibration"
        )
    if not adapter.available():
        raise PatientEPWorkflowError("ElectroTrace calibration capability is unavailable")
    payload = {
        "entity_id": patient_id,
        "observations": [
            {
                "observation_id": evidence_id,
                "modality": "electrical",
                "values": {"input_path": source_path, **params},
                "subject_id": patient_id,
            }
        ],
    }
    output = adapter.invoke("electrical.prepare_calibration", payload)
    if not isinstance(output, dict):
        raise PatientEPWorkflowError("ElectroTrace returned a non-object calibration handoff")
    return output


def _validate_handoff(
    *,
    case: PatientCase,
    evidence: PatientEvidenceItem,
    handoff: dict[str, Any],
    require_standard_12_leads: bool,
) -> PreparedECGEvidence:
    acquisition = _evidence_acquisition(case, evidence)
    if acquisition.modality != "ecg":
        raise PatientEPWorkflowError(
            f"Evidence {evidence.evidence_id!r} does not resolve to an ECG acquisition"
        )
    source = _primary_source(acquisition)
    expected_source_sha = _sha256(
        source.sha256,
        field=f"patient source {source.file_id!r} sha256",
    )

    returned_entity = handoff.get("entity_id")
    if returned_entity is not None and str(returned_entity) != case.patient_id:
        raise PatientEPWorkflowError(
            "ElectroTrace calibration handoff belongs to a different patient"
        )
    if handoff.get("schema_version") != ELECTROTRACE_CALIBRATION_SCHEMA:
        raise PatientEPWorkflowError(
            "ElectroTrace calibration handoff uses an unsupported schema version"
        )
    observations = handoff.get("observations")
    if not isinstance(observations, list) or len(observations) != 1:
        raise PatientEPWorkflowError(
            "ElectroTrace calibration handoff must contain exactly one observation"
        )
    observation = observations[0]
    if not isinstance(observation, dict) or observation.get("kind") != "ecg":
        raise PatientEPWorkflowError("ElectroTrace handoff is not an ECG observation")
    expected_observation_id = evidence.observation_id or evidence.evidence_id
    if str(observation.get("observation_id")) != expected_observation_id:
        raise PatientEPWorkflowError(
            "ElectroTrace observation_id does not match the selected evidence"
        )
    artifact = observation.get("artifact")
    if not isinstance(artifact, dict):
        raise PatientEPWorkflowError("ElectroTrace ECG observation has no artifact object")
    if artifact.get("kind") != ELECTROTRACE_ARTIFACT_KIND:
        raise PatientEPWorkflowError(
            "ElectroTrace ECG artifact is not a calibration-grade median-beat artifact"
        )
    derived_sha = _sha256(
        artifact.get("sha256"),
        field="ElectroTrace calibration artifact sha256",
    )
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        raise PatientEPWorkflowError("ElectroTrace ECG artifact has no metadata")
    if metadata.get("schema_version") != ELECTROTRACE_CALIBRATION_SCHEMA:
        raise PatientEPWorkflowError("ElectroTrace artifact schema version does not match")
    if metadata.get("representation") != "median_beat_template":
        raise PatientEPWorkflowError(
            "Patient calibration requires ElectroTrace median_beat_template representation"
        )
    observed_source_sha = _sha256(
        metadata.get("source_sha256"),
        field="ElectroTrace source_sha256",
    )
    if observed_source_sha != expected_source_sha:
        raise PatientEPWorkflowError(
            "ElectroTrace source hash does not match the selected PatientCase source file"
        )

    sampling_rate = metadata.get("sampling_rate_hz")
    if sampling_rate is None or float(sampling_rate) <= 0:
        raise PatientEPWorkflowError("ElectroTrace artifact has no valid sampling_rate_hz")
    if acquisition.sampling_rate_hz is None:
        raise PatientEPWorkflowError("Patient ECG acquisition has no declared sampling rate")
    if abs(float(sampling_rate) - float(acquisition.sampling_rate_hz)) > 1e-9:
        raise PatientEPWorkflowError(
            "ElectroTrace sampling rate does not match the selected PatientCase acquisition"
        )

    lead_names = metadata.get("lead_names")
    if not isinstance(lead_names, list) or not lead_names:
        raise PatientEPWorkflowError("ElectroTrace artifact has no lead_names")
    observed_leads = [str(value) for value in lead_names]
    declared_leads = list(acquisition.lead_definitions)
    if observed_leads != declared_leads:
        raise PatientEPWorkflowError(
            "ElectroTrace lead order/names do not match the PatientCase acquisition declaration"
        )
    if require_standard_12_leads and set(observed_leads) != _STANDARD_12_LEADS:
        raise PatientEPWorkflowError(
            "Camps activation/QRS calibration requires the complete standard 12-lead ECG"
        )

    units = observation.get("units")
    declared_unit = acquisition.units.get("signal")
    if declared_unit is not None and units is not None and str(units) != str(declared_unit):
        raise PatientEPWorkflowError(
            "ElectroTrace signal units do not match the PatientCase acquisition declaration"
        )

    # Enrich the CardiEP artifact reference with patient/evidence lineage while
    # preserving ElectroTrace's immutable derived artifact identity.
    normalized = dict(observation)
    normalized_artifact = dict(artifact)
    normalized_metadata = dict(metadata)
    normalized_metadata.update(
        {
            "patient_id": case.patient_id,
            "acquisition_id": acquisition.acquisition_id,
            "encounter_id": acquisition.encounter_id,
            "evidence_id": evidence.evidence_id,
            "source_file_id": source.file_id,
            "withheld_from_fit": evidence.withheld_from_fit,
            "derived_sha256": derived_sha,
        }
    )
    normalized_artifact["metadata"] = normalized_metadata
    normalized["artifact"] = normalized_artifact
    normalized["acquired_at"] = (
        None if acquisition.acquired_at is None else acquisition.acquired_at.isoformat()
    )

    hints = handoff.get("likelihood_hints")
    if hints is None:
        hints = []
    if not isinstance(hints, list):
        raise PatientEPWorkflowError("ElectroTrace likelihood_hints must be a list")

    return PreparedECGEvidence(
        evidence_id=evidence.evidence_id,
        acquisition_id=acquisition.acquisition_id,
        source_file_id=source.file_id,
        source_sha256=expected_source_sha,
        observation=normalized,
        likelihood_hints=[dict(item) for item in hints if isinstance(item, dict)],
        handoff_summary=dict(handoff.get("summary") or {}),
    )


def prepare_patient_ecg_evidence(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    evidence: PatientEvidenceItem,
    settings: dict[str, Any] | None = None,
    require_standard_12_leads: bool = False,
    output_dir: str | Path | None = None,
) -> PreparedECGEvidence:
    """Prepare and verify one patient ECG via ElectroTrace."""
    acquisition = _evidence_acquisition(case, evidence)
    if acquisition.modality != "ecg":
        raise PatientEPWorkflowError("Patient ECG preparation requires ECG evidence")
    source = _primary_source(acquisition)
    source_path = _source_local_path(source.uri)
    params = dict(settings or {})
    params.update(
        {
            "observation_kind": "ecg",
            "observation_id": evidence.observation_id or evidence.evidence_id,
            "acquired_at": (
                None if acquisition.acquired_at is None else acquisition.acquired_at.isoformat()
            ),
            "coordinate_frame": acquisition.coordinate_frame_id or "clinical_ecg",
        }
    )
    signal_unit = acquisition.units.get("signal")
    if signal_unit is not None:
        params["units"] = signal_unit
    if output_dir is not None:
        params["calibration_output_dir"] = str(Path(output_dir))

    handoff = _invoke_electrotrace(
        registry,
        patient_id=case.patient_id,
        evidence_id=evidence.evidence_id,
        source_path=source_path,
        params=params,
    )
    return _validate_handoff(
        case=case,
        evidence=evidence,
        handoff=handoff,
        require_standard_12_leads=require_standard_12_leads,
    )


def prepare_patient_ecg_split(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    calibration_evidence: list[PatientEvidenceItem],
    validation_evidence: list[PatientEvidenceItem],
    settings: dict[str, Any] | None = None,
    require_standard_12_leads: bool = False,
    output_root: str | Path | None = None,
) -> tuple[list[PreparedECGEvidence], list[PreparedECGEvidence]]:
    """Prepare fit and holdout ECGs identically without mixing their roles."""
    fit: list[PreparedECGEvidence] = []
    holdout: list[PreparedECGEvidence] = []
    root = None if output_root is None else Path(output_root)
    for item in calibration_evidence:
        fit.append(
            prepare_patient_ecg_evidence(
                registry,
                case=case,
                evidence=item,
                settings=settings,
                require_standard_12_leads=require_standard_12_leads,
                output_dir=None if root is None else root / "calibration" / item.evidence_id,
            )
        )
    for item in validation_evidence:
        holdout.append(
            prepare_patient_ecg_evidence(
                registry,
                case=case,
                evidence=item,
                settings=settings,
                require_standard_12_leads=require_standard_12_leads,
                output_dir=None if root is None else root / "validation" / item.evidence_id,
            )
        )
    return fit, holdout
