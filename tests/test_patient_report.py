from __future__ import annotations

from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    PatientModelInput,
    SourceFileRef,
)
from hearttwin.patient_report import build_patient_model_report


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
                source_files=[SourceFileRef(file_id="ct-file", uri="file:///ct.dcm", sha256=SHA)],
                coordinate_frame_id="image",
            ),
            PatientAcquisition(
                acquisition_id="fit",
                subject_id="P1",
                modality="ecg",
                source_files=[SourceFileRef(file_id="fit-file", uri="file:///fit.xml", sha256=SHA)],
                sampling_rate_hz=500.0,
                lead_definitions=LEADS,
            ),
            PatientAcquisition(
                acquisition_id="holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[SourceFileRef(file_id="holdout-file", uri="file:///holdout.xml", sha256=SHA)],
                sampling_rate_hz=500.0,
                lead_definitions=LEADS,
            ),
        ],
        model_inputs=[
            PatientModelInput(
                input_id="geometry",
                domain="anatomy",
                variable="ventricular_geometry",
                value="mesh.vtu",
                status="ESTIMATED_FROM_PATIENT",
                source_acquisition_ids=["ct"],
            ),
            PatientModelInput(
                input_id="fibres",
                domain="fibres",
                variable="fibre_field",
                value="rule_based",
                status="POPULATION_PRIOR",
                population_reference="rule-based ventricular fibre prior",
            ),
            PatientModelInput(
                input_id="scar",
                domain="scar",
                variable="scar_distribution",
                value=None,
                status="UNKNOWN",
            ),
        ],
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit-evidence",
                subject_id="P1",
                role="calibration",
                acquisition_id="fit",
                data_kind="12_lead_qrs",
            ),
            PatientEvidenceItem(
                evidence_id="holdout-evidence",
                subject_id="P1",
                role="validation",
                acquisition_id="holdout",
                data_kind="12_lead_qrs",
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


def test_report_exposes_personalization_depth_and_claim_ceiling() -> None:
    report = build_patient_model_report(case=_case(), spec=_spec())
    assert report.claim_ceiling == "software_plumbing_only"
    assert report.personalization_counts["ESTIMATED_FROM_PATIENT"] == 1
    assert report.personalization_counts["POPULATION_PRIOR"] == 1
    assert report.personalization_counts["UNKNOWN"] == 1

    domains = {item.domain: item for item in report.personalization_domains}
    assert domains["anatomy"].patient_derived_fraction == 1.0
    assert domains["fibres"].patient_derived_fraction == 0.0
    assert domains["scar"].patient_derived_fraction is None


def test_report_keeps_generic_fibres_and_unknown_scar_visible() -> None:
    report = build_patient_model_report(case=_case(), spec=_spec())
    assert any("fibre" in item.lower() for item in report.limitations)
    assert any("scar" in item.lower() for item in report.limitations)
    assert any("torso" in item.lower() for item in report.limitations)
    assert any("electrode" in item.lower() for item in report.limitations)


def test_report_does_not_treat_within_patient_holdout_as_cohort_generalization() -> None:
    report = build_patient_model_report(case=_case(), spec=_spec())
    assert report.evidence_split.mode == "within_patient_holdout"
    assert report.evidence_split.passed is True
    assert any("cohort generalization" in item.lower() for item in report.use_constraints)
