from __future__ import annotations

from hearttwin import AnatomyBundlePayload
from hearttwin.patient_ep import prepare_patient_ep_requests
from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
)


SHA = "1" * 64


def _case() -> PatientCase:
    leads = [
        "I", "II", "III", "aVR", "aVL", "aVF",
        "V1", "V2", "V3", "V4", "V5", "V6",
    ]
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
                lead_definitions=leads,
            ),
            PatientAcquisition(
                acquisition_id="holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[SourceFileRef(file_id="holdout", uri="file:///holdout.xml", sha256=SHA)],
                sampling_rate_hz=500,
                lead_definitions=leads,
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


def _bundle() -> AnatomyBundlePayload:
    return AnatomyBundlePayload(
        subject_id="P1",
        study_id="study",
        acquisition_id="ct",
        artifacts=[
            {
                "artifact_id": "mesh",
                "kind": "volume_mesh",
                "uri": "file:///mesh.vtu",
                "sha256": "2" * 64,
                "frame_id": "image",
            }
        ],
        bundle_fingerprint="3" * 64,
    )


def test_default_patient_ep_backends_are_real_registered_backend_ids() -> None:
    spec = PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct",
        calibration_evidence_ids=["fit-evidence"],
        validation_evidence_ids=["holdout-evidence"],
    )
    assert spec.fast_backend == "camps-cdt-external-v1"
    assert spec.confirmation_backend == "opencarp-external-v1"

    prepared = prepare_patient_ep_requests(
        case=_case(),
        spec=spec,
        anatomy_bundle=_bundle(),
        parameter_bounds={"sheet_speed": (0.02, 0.08)},
    )
    assert prepared.calibration_request["backend"] == "camps-cdt-external-v1"
    assert prepared.confirmation_request_template["backend"] == "opencarp-external-v1"
    assert [
        item["observation_id"] for item in prepared.calibration_request["observations"]
    ] == ["fit-evidence"]
    assert prepared.confirmation_request_template["observations"] == []
    assert [item["observation_id"] for item in prepared.holdout_observations] == [
        "holdout-evidence"
    ]
