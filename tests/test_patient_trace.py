from __future__ import annotations

from dataclasses import dataclass

from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
)
from hearttwin.patient_trace import build_patient_trace_payload, record_patient_trace


SHA = "a" * 64
LEADS = [
    "I", "II", "III", "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
]


def _case() -> PatientCase:
    return PatientCase(
        patient_id="P1",
        coordinate_frames=[
            CoordinateFrame(frame_id="image", convention="DICOM_LPS", length_unit="mm")
        ],
        acquisitions=[
            PatientAcquisition(
                acquisition_id="ct",
                subject_id="P1",
                modality="ct",
                source_files=[SourceFileRef(file_id="ct", uri="file:///ct.dcm", sha256=SHA)],
                coordinate_frame_id="image",
            ),
            PatientAcquisition(
                acquisition_id="fit",
                subject_id="P1",
                modality="ecg",
                source_files=[SourceFileRef(file_id="fit", uri="file:///fit.xml", sha256=SHA)],
                sampling_rate_hz=500,
                lead_definitions=LEADS,
            ),
            PatientAcquisition(
                acquisition_id="holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[SourceFileRef(file_id="holdout", uri="file:///holdout.xml", sha256=SHA)],
                sampling_rate_hz=500,
                lead_definitions=LEADS,
            ),
        ],
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit-evidence",
                subject_id="P1",
                role="calibration",
                acquisition_id="fit",
                data_kind="qrs",
            ),
            PatientEvidenceItem(
                evidence_id="holdout-evidence",
                subject_id="P1",
                role="validation",
                acquisition_id="holdout",
                data_kind="qrs",
                withheld_from_fit=True,
            ),
        ],
    )


def _spec() -> PatientEPWorkflowSpec:
    return PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct",
        calibration_evidence_ids=["fit-evidence"],
        validation_evidence_ids=["holdout-evidence"],
        fast_backend="numpy-eikonal-v1",
        confirmation_backend="numpy-eikonal-v1",
        observation_model="pseudo_ecg",
        scientific_grade=False,
    )


@dataclass
class _ServiceSpec:
    name: str = "CardiTrace"


class _TraceAdapter:
    def __init__(self) -> None:
        self.spec = _ServiceSpec()
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return True

    def invoke(self, capability: str, payload: dict) -> dict:
        self.calls.append((capability, payload))
        return {"run_id": "trace-1", "status": "succeeded"}


class _Registry:
    def __init__(self, adapter: _TraceAdapter) -> None:
        self.adapter = adapter

    def capability(self, capability: str):
        return self.adapter if capability == "trace.record" else None


def test_patient_trace_payload_is_hash_linked_and_research_scoped() -> None:
    envelope = build_patient_trace_payload(case=_case(), spec=_spec())
    assert envelope.entity_id == "P1"
    assert envelope.context["research_use_only"] is True
    assert envelope.context["validation_mode"] == "within_patient_holdout"
    assert len(envelope.fingerprints["patient_case_sha256"]) == 64
    assert len(envelope.fingerprints["workflow_spec_sha256"]) == 64


def test_patient_trace_records_through_carditrace_boundary() -> None:
    adapter = _TraceAdapter()
    output = record_patient_trace(
        _Registry(adapter),  # type: ignore[arg-type]
        case=_case(),
        spec=_spec(),
    )
    assert output["run_id"] == "trace-1"
    assert adapter.calls[0][0] == "trace.record"
    assert adapter.calls[0][1]["entity_id"] == "P1"
    assert "patient_case_sha256" in adapter.calls[0][1]["fingerprints"]
