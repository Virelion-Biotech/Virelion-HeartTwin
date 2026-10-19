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


SHA = "a" * 64


def _source(file_id: str) -> SourceFileRef:
    return SourceFileRef(file_id=file_id, uri=f"file:///{file_id}", sha256=SHA)


def _case(*, anatomy_modality: str = "ct", fit_role: str = "calibration") -> PatientCase:
    leads = [
        "I", "II", "III", "aVR", "aVL", "aVF",
        "V1", "V2", "V3", "V4", "V5", "V6",
    ]
    acquisitions = [
        PatientAcquisition(
            acquisition_id="anatomy",
            subject_id="P1",
            modality=anatomy_modality,
            source_files=[_source("anatomy")],
            coordinate_frame_id="image" if anatomy_modality in {"ct", "mri"} else None,
            sampling_rate_hz=500 if anatomy_modality == "ecg" else None,
            lead_definitions=leads if anatomy_modality == "ecg" else [],
        ),
        PatientAcquisition(
            acquisition_id="fit",
            subject_id="P1",
            modality="ecg",
            source_files=[_source("fit")],
            sampling_rate_hz=500,
            lead_definitions=leads,
        ),
        PatientAcquisition(
            acquisition_id="holdout",
            subject_id="P1",
            modality="ecg",
            source_files=[_source("holdout")],
            sampling_rate_hz=500,
            lead_definitions=leads,
        ),
    ]
    # Keep the patient globally EP-ready even when the deliberately selected
    # anatomy acquisition has the wrong modality. This isolates selection logic.
    if anatomy_modality not in {"ct", "mri"}:
        acquisitions.append(
            PatientAcquisition(
                acquisition_id="valid-ct-not-selected",
                subject_id="P1",
                modality="ct",
                source_files=[_source("valid-ct")],
                coordinate_frame_id="image",
            )
        )

    return PatientCase(
        patient_id="P1",
        coordinate_frames=[
            CoordinateFrame(frame_id="image", convention="DICOM_LPS", length_unit="mm")
        ],
        acquisitions=acquisitions,
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit-evidence",
                subject_id="P1",
                role=fit_role,
                acquisition_id="fit",
                data_kind="qrs",
                withheld_from_fit=(fit_role == "validation"),
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


def _bundle(acquisition_id: str = "anatomy") -> AnatomyBundlePayload:
    return AnatomyBundlePayload(
        subject_id="P1",
        study_id="study",
        acquisition_id=acquisition_id,
        artifacts=[
            {
                "artifact_id": "mesh",
                "kind": "volume_mesh",
                "uri": "file:///mesh.vtu",
                "sha256": "b" * 64,
                "frame_id": "image",
            }
        ],
        bundle_fingerprint="c" * 64,
    )


def _spec() -> PatientEPWorkflowSpec:
    return PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="anatomy",
        calibration_evidence_ids=["fit-evidence"],
        validation_evidence_ids=["holdout-evidence"],
        fast_backend="numpy-eikonal-v1",
        confirmation_backend="numpy-eikonal-v1",
        observation_model="pseudo_ecg",
        scientific_grade=False,
    )


def _prepare(case: PatientCase, bundle: AnatomyBundlePayload):
    return prepare_patient_ep_requests(
        case=case,
        spec=_spec(),
        anatomy_bundle=bundle,
        parameter_bounds={"isotropic_speed": (0.03, 0.10)},
    )


def test_selected_anatomy_must_be_ct_or_mri() -> None:
    case = _case(anatomy_modality="ecg")
    with pytest.raises(PatientEPWorkflowError, match="CT or MRI"):
        _prepare(case, _bundle())


def test_bundle_must_come_from_selected_anatomy_acquisition() -> None:
    with pytest.raises(PatientEPWorkflowError, match="bundle acquisition"):
        _prepare(_case(), _bundle(acquisition_id="different-acquisition"))


def test_workflow_cannot_reinterpret_declared_validation_as_calibration() -> None:
    case = _case(fit_role="validation")
    with pytest.raises(PatientEPWorkflowError, match="declared as calibration"):
        _prepare(case, _bundle())
