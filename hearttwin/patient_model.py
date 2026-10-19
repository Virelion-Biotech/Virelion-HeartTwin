"""Patient-specific modeling contracts and fail-closed evidence/QC rules.

This module deliberately separates *patient identity and evidence provenance* from
solver-specific payloads.  A patient-specific mesh is not, by itself, a
patient-specific physiological model.  Each important input therefore carries
an explicit personalization status and links back to the acquisition(s) or
observation(s) that support it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import UncertaintySpec

PatientInputStatus = Literal[
    "MEASURED",
    "ESTIMATED_FROM_PATIENT",
    "POPULATION_PRIOR",
    "ASSUMED",
    "UNKNOWN",
]
EvidenceRole = Literal["calibration", "validation", "context", "excluded"]
ValidationMode = Literal["within_patient_holdout", "cohort_generalization"]
AcquisitionModality = Literal[
    "ct",
    "mri",
    "ecg",
    "eam",
    "echo",
    "pressure",
    "lab",
    "other",
]


class SourceFileRef(BaseModel):
    """Reference to one source object without embedding clinical file contents."""

    model_config = ConfigDict(extra="forbid")
    file_id: str
    uri: str
    media_type: str | None = None
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoordinateFrame(BaseModel):
    """Explicit coordinate system declaration for imaging/model artifacts."""

    model_config = ConfigDict(extra="forbid")
    frame_id: str
    convention: Literal["DICOM_LPS", "NIFTI_RAS", "MODEL", "SOLVER", "OTHER"]
    length_unit: Literal["mm", "cm", "m"]
    description: str | None = None


class CoordinateTransform(BaseModel):
    """A traceable transform connecting two coordinate frames."""

    model_config = ConfigDict(extra="forbid")
    transform_id: str
    source_frame_id: str
    target_frame_id: str
    kind: Literal["rigid", "affine", "deformable", "identity", "other"]
    matrix_4x4: list[list[float]] | None = None
    artifact: SourceFileRef | None = None
    method: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transform(self) -> CoordinateTransform:
        if self.source_frame_id == self.target_frame_id and self.kind != "identity":
            raise ValueError("same-frame transforms must be declared as identity")
        if self.matrix_4x4 is not None:
            if len(self.matrix_4x4) != 4 or any(len(row) != 4 for row in self.matrix_4x4):
                raise ValueError("matrix_4x4 must be a 4x4 matrix")
        if self.kind != "identity" and self.matrix_4x4 is None and self.artifact is None:
            raise ValueError("non-identity transforms require a matrix or transform artifact")
        return self


class PatientEncounter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encounter_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    physiologic_state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval(self) -> PatientEncounter:
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("encounter ended_at cannot precede started_at")
        return self


class PatientAcquisition(BaseModel):
    """One patient data acquisition, kept distinct from derived model artifacts."""

    model_config = ConfigDict(extra="forbid")
    acquisition_id: str
    subject_id: str
    encounter_id: str | None = None
    modality: AcquisitionModality
    acquired_at: datetime | None = None
    source_files: list[SourceFileRef] = Field(default_factory=list)
    coordinate_frame_id: str | None = None
    units: dict[str, str] = Field(default_factory=dict)
    physiologic_state: dict[str, Any] = Field(default_factory=dict)
    sampling_rate_hz: float | None = Field(default=None, gt=0)
    lead_definitions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_modality_metadata(self) -> PatientAcquisition:
        if self.modality != "ecg" and self.lead_definitions:
            raise ValueError("lead_definitions are only valid for ECG acquisitions")
        if self.modality != "ecg" and self.sampling_rate_hz is not None:
            raise ValueError("sampling_rate_hz is currently reserved for ECG acquisitions")
        return self


class PatientModelInput(BaseModel):
    """A model input with explicit patient-specificity and uncertainty."""

    model_config = ConfigDict(extra="forbid")
    input_id: str
    domain: Literal[
        "anatomy",
        "scar",
        "fibres",
        "electrophysiology",
        "ecg_observation",
        "mechanics",
        "circulation",
        "cellular",
        "other",
    ]
    variable: str
    value: Any | None = None
    unit: str | None = None
    status: PatientInputStatus
    source_acquisition_ids: list[str] = Field(default_factory=list)
    source_observation_ids: list[str] = Field(default_factory=list)
    uncertainty: UncertaintySpec | None = None
    population_reference: str | None = None
    assumption_reason: str | None = None
    method: str | None = None

    @model_validator(mode="after")
    def validate_status_provenance(self) -> PatientModelInput:
        patient_sources = bool(self.source_acquisition_ids or self.source_observation_ids)
        if self.status == "MEASURED" and not patient_sources:
            raise ValueError("MEASURED inputs require patient acquisition/observation provenance")
        if self.status == "ESTIMATED_FROM_PATIENT" and not patient_sources:
            raise ValueError(
                "ESTIMATED_FROM_PATIENT inputs require patient acquisition/observation provenance"
            )
        if self.status == "POPULATION_PRIOR" and not self.population_reference:
            raise ValueError("POPULATION_PRIOR inputs require population_reference")
        if self.status == "ASSUMED" and not self.assumption_reason:
            raise ValueError("ASSUMED inputs require assumption_reason")
        if self.status == "UNKNOWN" and self.value is not None:
            raise ValueError("UNKNOWN inputs must not silently carry a value")
        return self


class PatientEvidenceItem(BaseModel):
    """One role assignment for a patient measurement in model development/evaluation."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    subject_id: str
    role: EvidenceRole
    observation_id: str | None = None
    acquisition_id: str | None = None
    encounter_id: str | None = None
    data_kind: str
    withheld_from_fit: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_measurement_reference(self) -> PatientEvidenceItem:
        if self.role in {"calibration", "validation"} and not (
            self.observation_id or self.acquisition_id
        ):
            raise ValueError(
                "calibration/validation evidence must reference an observation or acquisition"
            )
        if self.role == "validation" and not self.withheld_from_fit:
            raise ValueError("validation evidence must be explicitly withheld_from_fit")
        if self.role == "calibration" and self.withheld_from_fit:
            raise ValueError("calibration evidence cannot be withheld_from_fit")
        return self


class EvidenceSplitReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ValidationMode
    passed: bool
    calibration_evidence_ids: list[str] = Field(default_factory=list)
    validation_evidence_ids: list[str] = Field(default_factory=list)
    calibration_subject_ids: list[str] = Field(default_factory=list)
    validation_subject_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def assess_evidence_split(
    evidence: list[PatientEvidenceItem],
    *,
    mode: ValidationMode,
) -> EvidenceSplitReport:
    """Check leakage rules without conflating within-patient and cohort validation.

    ``within_patient_holdout`` permits the same patient on both sides, but a
    validation acquisition/observation may not be reused for calibration.
    ``cohort_generalization`` requires patient-disjoint calibration and
    validation subjects.
    """

    calibration = [item for item in evidence if item.role == "calibration"]
    validation = [item for item in evidence if item.role == "validation"]
    reasons: list[str] = []
    warnings: list[str] = []

    ids = [item.evidence_id for item in evidence]
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        reasons.append("duplicate evidence_id values: " + ", ".join(duplicate_ids))

    if not calibration:
        reasons.append("no calibration evidence declared")
    if not validation:
        reasons.append("no validation evidence declared")

    calibration_subjects = {item.subject_id for item in calibration}
    validation_subjects = {item.subject_id for item in validation}

    calibration_observations = {
        (item.subject_id, item.observation_id)
        for item in calibration
        if item.observation_id
    }
    validation_observations = {
        (item.subject_id, item.observation_id)
        for item in validation
        if item.observation_id
    }
    observation_overlap = sorted(calibration_observations & validation_observations)
    if observation_overlap:
        reasons.append(
            "the same observation is assigned to calibration and validation: "
            + ", ".join(f"{subject}/{obs}" for subject, obs in observation_overlap)
        )

    calibration_acquisitions = {
        (item.subject_id, item.acquisition_id)
        for item in calibration
        if item.acquisition_id
    }
    validation_acquisitions = {
        (item.subject_id, item.acquisition_id)
        for item in validation
        if item.acquisition_id
    }
    acquisition_overlap = sorted(calibration_acquisitions & validation_acquisitions)
    if acquisition_overlap:
        reasons.append(
            "the same acquisition is assigned to calibration and validation: "
            + ", ".join(f"{subject}/{acq}" for subject, acq in acquisition_overlap)
        )

    if mode == "cohort_generalization":
        overlapping_subjects = sorted(calibration_subjects & validation_subjects)
        if overlapping_subjects:
            reasons.append(
                "cohort_generalization requires patient-disjoint evidence; overlapping "
                "subjects: " + ", ".join(overlapping_subjects)
            )
    else:
        if len(calibration_subjects | validation_subjects) > 1:
            warnings.append(
                "within_patient_holdout contains multiple subjects; evaluate each patient "
                "separately or use cohort_generalization for cohort claims"
            )

    return EvidenceSplitReport(
        mode=mode,
        passed=not reasons,
        calibration_evidence_ids=[item.evidence_id for item in calibration],
        validation_evidence_ids=[item.evidence_id for item in validation],
        calibration_subject_ids=sorted(calibration_subjects),
        validation_subject_ids=sorted(validation_subjects),
        reasons=reasons,
        warnings=warnings,
    )


class PatientCase(BaseModel):
    """Pseudonymous patient record used to construct one or more model versions."""

    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    patient_id: str
    pseudonymous: bool = True
    encounters: list[PatientEncounter] = Field(default_factory=list)
    acquisitions: list[PatientAcquisition] = Field(default_factory=list)
    coordinate_frames: list[CoordinateFrame] = Field(default_factory=list)
    transforms: list[CoordinateTransform] = Field(default_factory=list)
    model_inputs: list[PatientModelInput] = Field(default_factory=list)
    evidence: list[PatientEvidenceItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_internal_references(self) -> PatientCase:
        if not self.pseudonymous:
            raise ValueError("HeartTwin patient cases must use pseudonymous identifiers")

        def duplicates(values: list[str]) -> list[str]:
            return sorted(key for key, count in Counter(values).items() if count > 1)

        duplicate_groups = {
            "encounter_id": duplicates([item.encounter_id for item in self.encounters]),
            "acquisition_id": duplicates([item.acquisition_id for item in self.acquisitions]),
            "frame_id": duplicates([item.frame_id for item in self.coordinate_frames]),
            "transform_id": duplicates([item.transform_id for item in self.transforms]),
            "input_id": duplicates([item.input_id for item in self.model_inputs]),
            "evidence_id": duplicates([item.evidence_id for item in self.evidence]),
        }
        problems = [
            f"duplicate {name} values: {', '.join(items)}"
            for name, items in duplicate_groups.items()
            if items
        ]
        if problems:
            raise ValueError("; ".join(problems))

        encounter_ids = {item.encounter_id for item in self.encounters}
        acquisition_ids = {item.acquisition_id for item in self.acquisitions}
        frame_ids = {item.frame_id for item in self.coordinate_frames}

        for acquisition in self.acquisitions:
            if acquisition.subject_id != self.patient_id:
                raise ValueError(
                    f"acquisition {acquisition.acquisition_id!r} belongs to subject "
                    f"{acquisition.subject_id!r}, not patient {self.patient_id!r}"
                )
            if acquisition.encounter_id and acquisition.encounter_id not in encounter_ids:
                raise ValueError(
                    f"acquisition {acquisition.acquisition_id!r} references unknown encounter"
                )
            if acquisition.coordinate_frame_id and acquisition.coordinate_frame_id not in frame_ids:
                raise ValueError(
                    f"acquisition {acquisition.acquisition_id!r} references unknown coordinate frame"
                )

        for transform in self.transforms:
            if transform.source_frame_id not in frame_ids or transform.target_frame_id not in frame_ids:
                raise ValueError(
                    f"transform {transform.transform_id!r} references an unknown coordinate frame"
                )

        for item in self.model_inputs:
            unknown = sorted(set(item.source_acquisition_ids) - acquisition_ids)
            if unknown:
                raise ValueError(
                    f"model input {item.input_id!r} references unknown acquisitions: "
                    + ", ".join(unknown)
                )

        for item in self.evidence:
            if item.subject_id != self.patient_id:
                raise ValueError(
                    f"evidence {item.evidence_id!r} belongs to subject {item.subject_id!r}, "
                    f"not patient {self.patient_id!r}"
                )
            if item.acquisition_id and item.acquisition_id not in acquisition_ids:
                raise ValueError(
                    f"evidence {item.evidence_id!r} references unknown acquisition"
                )
            if item.encounter_id and item.encounter_id not in encounter_ids:
                raise ValueError(f"evidence {item.evidence_id!r} references unknown encounter")
        return self


class PatientCaseQC(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    ready_for_ep_reconstruction: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    personalization_counts: dict[str, int] = Field(default_factory=dict)


def assess_patient_case_for_ep(case: PatientCase) -> PatientCaseQC:
    """Fail-closed readiness checks for the first anatomy + activation + ECG task."""

    blocking: list[str] = []
    warnings: list[str] = []
    imaging = [item for item in case.acquisitions if item.modality in {"ct", "mri"}]
    ecgs = [item for item in case.acquisitions if item.modality == "ecg"]

    if not imaging:
        blocking.append("no patient CT/MRI acquisition is available for ventricular anatomy")
    if not ecgs:
        blocking.append("no patient digital ECG acquisition is available")

    for item in imaging:
        if not item.source_files:
            blocking.append(f"imaging acquisition {item.acquisition_id} has no source files")
        if not item.coordinate_frame_id:
            blocking.append(
                f"imaging acquisition {item.acquisition_id} has no declared coordinate frame"
            )

    expected_12_leads = {"I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"}
    for item in ecgs:
        if not item.source_files:
            blocking.append(f"ECG acquisition {item.acquisition_id} has no source files")
        if item.sampling_rate_hz is None:
            blocking.append(f"ECG acquisition {item.acquisition_id} has no sampling rate")
        if not item.lead_definitions:
            blocking.append(f"ECG acquisition {item.acquisition_id} has no lead definitions")
        elif not expected_12_leads.issubset(set(item.lead_definitions)):
            warnings.append(
                f"ECG acquisition {item.acquisition_id} is not a complete standard 12-lead set"
            )

    frame_graph: dict[str, set[str]] = defaultdict(set)
    for transform in case.transforms:
        frame_graph[transform.source_frame_id].add(transform.target_frame_id)
        frame_graph[transform.target_frame_id].add(transform.source_frame_id)
    if len(case.coordinate_frames) > 1 and not case.transforms:
        warnings.append(
            "multiple coordinate frames exist but no transforms connect them; downstream "
            "registration-dependent operations must fail closed"
        )

    for item in case.model_inputs:
        if item.domain == "fibres" and item.status in {"POPULATION_PRIOR", "ASSUMED"}:
            warnings.append(
                "fibre architecture is not patient-measured and must remain labelled as an assumption/prior"
            )
        if item.domain == "scar" and item.status == "UNKNOWN":
            warnings.append("scar status is unknown")

    counts = Counter(item.status for item in case.model_inputs)
    return PatientCaseQC(
        patient_id=case.patient_id,
        ready_for_ep_reconstruction=not blocking,
        blocking_issues=blocking,
        warnings=warnings,
        personalization_counts={key: int(value) for key, value in sorted(counts.items())},
    )


class PatientEPWorkflowSpec(BaseModel):
    """Narrow first HeartTwin patient-specific task: ventricular activation + QRS."""

    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    patient_id: str
    anatomy_acquisition_id: str
    calibration_evidence_ids: list[str]
    validation_evidence_ids: list[str]
    validation_mode: ValidationMode = "within_patient_holdout"
    fast_backend: str = "camps-cdt-external-v1"
    confirmation_backend: str = "opencarp-external-v1"
    observation_model: Literal["lead_field", "pseudo_ecg"] = "lead_field"
    scientific_grade: bool = True
    torso_mode: Literal["patient_specific", "standardized", "not_modeled"] = "standardized"
    electrode_mode: Literal["recorded", "protocol_derived", "generic"] = "protocol_derived"
    calibratable_parameters: list[str] = Field(
        default_factory=lambda: [
            "longitudinal_conduction_velocity",
            "transverse_conduction_velocity",
            "activation_root_configuration",
            "activation_onset_ms",
        ]
    )
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("calibration_evidence_ids", "validation_evidence_ids")
    @classmethod
    def require_unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence ID lists must not contain duplicates")
        return value

    @model_validator(mode="after")
    def enforce_scientific_path(self) -> PatientEPWorkflowSpec:
        if not self.calibration_evidence_ids:
            raise ValueError("at least one calibration evidence item is required")
        if not self.validation_evidence_ids:
            raise ValueError("at least one held-out validation evidence item is required")
        overlap = sorted(set(self.calibration_evidence_ids) & set(self.validation_evidence_ids))
        if overlap:
            raise ValueError(
                "calibration and validation evidence IDs overlap: " + ", ".join(overlap)
            )
        if self.scientific_grade and self.observation_model == "pseudo_ecg":
            raise ValueError("scientific-grade patient workflows cannot use pseudo_ecg")
        return self


def validate_patient_ep_workflow(case: PatientCase, spec: PatientEPWorkflowSpec) -> EvidenceSplitReport:
    """Validate case readiness and the requested evidence split before solver execution."""
    if spec.patient_id != case.patient_id:
        raise ValueError("workflow patient_id does not match PatientCase.patient_id")
    acquisition_ids = {item.acquisition_id for item in case.acquisitions}
    if spec.anatomy_acquisition_id not in acquisition_ids:
        raise ValueError("anatomy_acquisition_id is not present in the patient case")

    qc = assess_patient_case_for_ep(case)
    if not qc.ready_for_ep_reconstruction:
        raise ValueError("patient case is not EP-ready: " + "; ".join(qc.blocking_issues))

    evidence_by_id = {item.evidence_id: item for item in case.evidence}
    requested_ids = set(spec.calibration_evidence_ids) | set(spec.validation_evidence_ids)
    missing = sorted(requested_ids - set(evidence_by_id))
    if missing:
        raise ValueError("workflow references unknown evidence IDs: " + ", ".join(missing))

    selected: list[PatientEvidenceItem] = []
    for evidence_id in spec.calibration_evidence_ids:
        item = evidence_by_id[evidence_id]
        selected.append(item.model_copy(update={"role": "calibration", "withheld_from_fit": False}))
    for evidence_id in spec.validation_evidence_ids:
        item = evidence_by_id[evidence_id]
        selected.append(item.model_copy(update={"role": "validation", "withheld_from_fit": True}))
    report = assess_evidence_split(selected, mode=spec.validation_mode)
    if not report.passed:
        raise ValueError("invalid patient EP evidence split: " + "; ".join(report.reasons))
    return report