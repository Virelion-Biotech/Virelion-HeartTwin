from __future__ import annotations

import pytest
from pydantic import ValidationError

from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEvidenceItem,
    PatientModelInput,
    PatientEPWorkflowSpec,
    SourceFileRef,
    assess_evidence_split,
    assess_patient_case_for_ep,
    validate_patient_ep_workflow,
)


SHA = "a" * 64


def _file(name: str) -> SourceFileRef:
    return SourceFileRef(file_id=name, uri=f"file:///{name}", sha256=SHA)


def _ep_ready_case() -> PatientCase:
    return PatientCase(
        patient_id="PSEUDO-001",
        coordinate_frames=[
            CoordinateFrame(frame_id="ct-lps", convention="DICOM_LPS", length_unit="mm")
        ],
        acquisitions=[
            PatientAcquisition(
                acquisition_id="ct-1",
                subject_id="PSEUDO-001",
                modality="ct",
                source_files=[_file("ct.dcm")],
                coordinate_frame_id="ct-lps",
            ),
            PatientAcquisition(
                acquisition_id="ecg-fit",
                subject_id="PSEUDO-001",
                modality="ecg",
                source_files=[_file("fit.xml")],
                sampling_rate_hz=500.0,
                lead_definitions=[
                    "I", "II", "III", "aVR", "aVL", "aVF",
                    "V1", "V2", "V3", "V4", "V5", "V6",
                ],
            ),
            PatientAcquisition(
                acquisition_id="ecg-holdout",
                subject_id="PSEUDO-001",
                modality="ecg",
                source_files=[_file("holdout.xml")],
                sampling_rate_hz=500.0,
                lead_definitions=[
                    "I", "II", "III", "aVR", "aVL", "aVF",
                    "V1", "V2", "V3", "V4", "V5", "V6",
                ],
            ),
        ],
        model_inputs=[
            PatientModelInput(
                input_id="geometry",
                domain="anatomy",
                variable="ventricular_geometry",
                status="ESTIMATED_FROM_PATIENT",
                value="mesh.vtu",
                source_acquisition_ids=["ct-1"],
                method="segmentation_and_meshing",
            ),
            PatientModelInput(
                input_id="fibres",
                domain="fibres",
                variable="fibre_field",
                status="POPULATION_PRIOR",
                value="rule_based",
                population_reference="rule-based ventricular fibre prior",
            ),
        ],
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit-qrs",
                subject_id="PSEUDO-001",
                role="calibration",
                acquisition_id="ecg-fit",
                data_kind="12_lead_qrs",
            ),
            PatientEvidenceItem(
                evidence_id="holdout-qrs",
                subject_id="PSEUDO-001",
                role="validation",
                acquisition_id="ecg-holdout",
                data_kind="12_lead_qrs",
                withheld_from_fit=True,
            ),
        ],
    )


def test_patient_case_rejects_cross_patient_acquisition() -> None:
    with pytest.raises(ValidationError, match="not patient"):
        PatientCase(
            patient_id="P1",
            acquisitions=[
                PatientAcquisition(
                    acquisition_id="ct-1",
                    subject_id="P2",
                    modality="ct",
                )
            ],
        )


def test_patient_specific_input_requires_patient_provenance() -> None:
    with pytest.raises(ValidationError, match="require patient acquisition"):
        PatientModelInput(
            input_id="cv",
            domain="electrophysiology",
            variable="longitudinal_conduction_velocity",
            value=0.6,
            unit="m/s",
            status="ESTIMATED_FROM_PATIENT",
        )


def test_population_prior_cannot_masquerade_as_patient_measurement() -> None:
    with pytest.raises(ValidationError, match="population_reference"):
        PatientModelInput(
            input_id="fibres",
            domain="fibres",
            variable="fibre_field",
            value="rule_based",
            status="POPULATION_PRIOR",
        )


def test_same_patient_can_have_independent_fit_and_holdout_acquisitions() -> None:
    case = _ep_ready_case()
    report = assess_evidence_split(case.evidence, mode="within_patient_holdout")
    assert report.passed is True
    assert report.calibration_subject_ids == ["PSEUDO-001"]
    assert report.validation_subject_ids == ["PSEUDO-001"]


def test_within_patient_rejects_reusing_same_acquisition() -> None:
    evidence = [
        PatientEvidenceItem(
            evidence_id="fit",
            subject_id="P1",
            role="calibration",
            acquisition_id="ecg-1",
            data_kind="qrs",
        ),
        PatientEvidenceItem(
            evidence_id="val",
            subject_id="P1",
            role="validation",
            acquisition_id="ecg-1",
            data_kind="qrs",
            withheld_from_fit=True,
        ),
    ]
    report = assess_evidence_split(evidence, mode="within_patient_holdout")
    assert report.passed is False
    assert any("same acquisition" in reason for reason in report.reasons)


def test_cohort_generalization_requires_patient_disjoint_split() -> None:
    evidence = [
        PatientEvidenceItem(
            evidence_id="fit",
            subject_id="P1",
            role="calibration",
            acquisition_id="a1",
            data_kind="qrs",
        ),
        PatientEvidenceItem(
            evidence_id="val",
            subject_id="P1",
            role="validation",
            acquisition_id="a2",
            data_kind="qrs",
            withheld_from_fit=True,
        ),
    ]
    report = assess_evidence_split(evidence, mode="cohort_generalization")
    assert report.passed is False
    assert any("patient-disjoint" in reason for reason in report.reasons)


def test_ep_readiness_flags_generic_fibres_without_blocking() -> None:
    report = assess_patient_case_for_ep(_ep_ready_case())
    assert report.ready_for_ep_reconstruction is True
    assert any("fibre architecture" in warning for warning in report.warnings)


def test_scientific_patient_workflow_rejects_pseudo_ecg() -> None:
    with pytest.raises(ValidationError, match="pseudo_ecg"):
        PatientEPWorkflowSpec(
            patient_id="P1",
            anatomy_acquisition_id="ct-1",
            calibration_evidence_ids=["fit"],
            validation_evidence_ids=["holdout"],
            observation_model="pseudo_ecg",
            scientific_grade=True,
        )


def test_patient_ep_workflow_accepts_independent_same_patient_holdout() -> None:
    case = _ep_ready_case()
    spec = PatientEPWorkflowSpec(
        patient_id=case.patient_id,
        anatomy_acquisition_id="ct-1",
        calibration_evidence_ids=["fit-qrs"],
        validation_evidence_ids=["holdout-qrs"],
    )
    report = validate_patient_ep_workflow(case, spec)
    assert report.passed is True
