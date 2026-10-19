from __future__ import annotations

import pytest

from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
)
from hearttwin.patient_trace import build_patient_trace_payload


LEADS = [
    "I", "II", "III", "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
]


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


def _case(*, with_hashes: bool = True) -> PatientCase:
    digest = "a" * 64 if with_hashes else None
    return PatientCase(
        patient_id="P1",
        metadata={"free_text": "must not enter trace"},
        coordinate_frames=[
            CoordinateFrame(frame_id="image", convention="DICOM_LPS", length_unit="mm")
        ],
        acquisitions=[
            PatientAcquisition(
                acquisition_id="ct",
                subject_id="P1",
                modality="ct",
                source_files=[
                    SourceFileRef(
                        file_id="ct-file",
                        uri="file:///private/patient-name/ct.dcm",
                        sha256=digest,
                        metadata={"PatientName": "must-not-enter-trace"},
                    )
                ],
                coordinate_frame_id="image",
                metadata={"primary_file_id": "ct-file", "free_text": "remove-me"},
            ),
            PatientAcquisition(
                acquisition_id="fit",
                subject_id="P1",
                modality="ecg",
                source_files=[
                    SourceFileRef(
                        file_id="fit-file",
                        uri="file:///private/patient-name/fit.xml",
                        sha256=digest,
                    )
                ],
                sampling_rate_hz=500,
                lead_definitions=LEADS,
            ),
            PatientAcquisition(
                acquisition_id="holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[
                    SourceFileRef(
                        file_id="holdout-file",
                        uri="file:///private/patient-name/holdout.xml",
                        sha256=digest,
                    )
                ],
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


def test_patient_trace_redacts_source_uris_and_freeform_source_metadata() -> None:
    envelope = build_patient_trace_payload(case=_case(), spec=_spec())
    serialized = envelope.model_dump_json()
    assert "patient-name" not in serialized
    assert "must-not-enter-trace" not in serialized
    assert "remove-me" not in serialized
    assert envelope.context["source_locators_redacted"] is True

    ct = envelope.patient_case["acquisitions"][0]
    assert ct["source_files"][0]["uri"] == "redacted://ct-file"
    assert ct["source_files"][0]["metadata"] == {}
    assert ct["metadata"] == {"primary_file_id": "ct-file"}
    assert len(envelope.fingerprints["patient_case_sha256"]) == 64
    assert len(envelope.fingerprints["trace_patient_case_sha256"]) == 64


def test_patient_trace_requires_hash_pinned_patient_source_files() -> None:
    with pytest.raises(ValueError, match="SHA-256 pinned source file"):
        build_patient_trace_payload(case=_case(with_hashes=False), spec=_spec())
