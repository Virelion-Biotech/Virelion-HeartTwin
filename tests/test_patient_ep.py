from __future__ import annotations

import pytest

from hearttwin import AnatomyBundlePayload
from hearttwin.patient_ep import PatientEPWorkflowError, prepare_patient_ep_requests
from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
)


SHA = "b" * 64
FINGERPRINT = "c" * 64


def _source(file_id: str) -> SourceFileRef:
    return SourceFileRef(file_id=file_id, uri=f"file:///{file_id}.json", sha256=SHA)


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
                source_files=[_source("ct")],
                coordinate_frame_id="image",
            ),
            PatientAcquisition(
                acquisition_id="ecg-fit",
                subject_id="P1",
                modality="ecg",
                source_files=[_source("ecg-fit")],
                sampling_rate_hz=500,
                lead_definitions=leads,
                units={"signal": "mV"},
            ),
            PatientAcquisition(
                acquisition_id="ecg-holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[_source("ecg-holdout")],
                sampling_rate_hz=500,
                lead_definitions=leads,
                units={"signal": "mV"},
            ),
        ],
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit",
                subject_id="P1",
                role="calibration",
                acquisition_id="ecg-fit",
                data_kind="qrs",
            ),
            PatientEvidenceItem(
                evidence_id="holdout",
                subject_id="P1",
                role="validation",
                acquisition_id="ecg-holdout",
                data_kind="qrs",
                withheld_from_fit=True,
            ),
        ],
    )


def _bundle() -> AnatomyBundlePayload:
    return AnatomyBundlePayload(
        subject_id="P1",
        study_id="study-1",
        acquisition_id="ct",
        artifacts=[
            {
                "artifact_id": "ventricles-vtu",
                "kind": "volume_mesh",
                "uri": "file:///ventricles.vtu",
                "sha256": "d" * 64,
                "frame_id": "image",
            }
        ],
        bundle_fingerprint=FINGERPRINT,
    )


def test_prepare_patient_ep_keeps_holdout_out_of_solver_requests() -> None:
    prepared = prepare_patient_ep_requests(
        case=_case(),
        spec=PatientEPWorkflowSpec(
            patient_id="P1",
            anatomy_acquisition_id="ct",
            calibration_evidence_ids=["fit"],
            validation_evidence_ids=["holdout"],
            fast_backend="numpy-eikonal-v1",
            confirmation_backend="numpy-eikonal-v1",
            observation_model="pseudo_ecg",
            scientific_grade=False,
        ),
        anatomy_bundle=_bundle(),
        parameter_bounds={"isotropic_speed": (0.03, 0.10)},
        initial_parameters={"isotropic_speed": 0.06},
        parameter_units={"isotropic_speed": "cm/ms"},
    )

    assert [item["observation_id"] for item in prepared.calibration_request["observations"]] == ["fit"]
    assert [item["observation_id"] for item in prepared.holdout_observations] == ["holdout"]
    assert prepared.confirmation_request_template["observations"] == []
    assert prepared.holdout_observations[0]["artifact"]["metadata"]["withheld_from_fit"] is True


def test_scientific_grade_lead_field_refuses_native_pseudo_ecg_backend() -> None:
    spec = PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct",
        calibration_evidence_ids=["fit"],
        validation_evidence_ids=["holdout"],
        fast_backend="numpy-eikonal-v1",
        confirmation_backend="opencarp-monodomain-v1",
        observation_model="lead_field",
        scientific_grade=True,
    )
    with pytest.raises(PatientEPWorkflowError, match="exploratory pseudo-ECG"):
        prepare_patient_ep_requests(
            case=_case(),
            spec=spec,
            anatomy_bundle=_bundle(),
            parameter_bounds={"isotropic_speed": (0.03, 0.10)},
        )


def test_patient_ep_refuses_ambiguous_volume_mesh_selection() -> None:
    bundle = _bundle().model_copy(
        update={
            "artifacts": [
                *_bundle().artifacts,
                {
                    "artifact_id": "second",
                    "kind": "volume_mesh",
                    "uri": "file:///second.vtu",
                    "sha256": "e" * 64,
                    "frame_id": "image",
                },
            ]
        }
    )
    with pytest.raises(PatientEPWorkflowError, match="multiple 'volume_mesh'"):
        prepare_patient_ep_requests(
            case=_case(),
            spec=PatientEPWorkflowSpec(
                patient_id="P1",
                anatomy_acquisition_id="ct",
                calibration_evidence_ids=["fit"],
                validation_evidence_ids=["holdout"],
                fast_backend="numpy-eikonal-v1",
                confirmation_backend="numpy-eikonal-v1",
                observation_model="pseudo_ecg",
                scientific_grade=False,
            ),
            anatomy_bundle=bundle,
            parameter_bounds={"isotropic_speed": (0.03, 0.10)},
        )
