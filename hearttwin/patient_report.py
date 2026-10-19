"""Machine-readable patient-model reporting for HeartTwin.

The report is intentionally conservative: it describes provenance and the
highest claim supported by the executed workflow.  It never converts a passing
software/QC gate into a clinical-validation claim.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .ep_parameter_mapping import EPParameterMappingSpec
from .patient_ecg_eval import ECGValidationReport
from .patient_ep import PatientEPWorkflowResult
from .patient_model import (
    EvidenceSplitReport,
    PatientCase,
    PatientCaseQC,
    PatientEPWorkflowSpec,
    PatientInputStatus,
    PatientModelInput,
    assess_patient_case_for_ep,
    validate_patient_ep_workflow,
)

ClaimCeiling = Literal[
    "blocked",
    "software_plumbing_only",
    "patient_specific_research_prediction",
    "heldout_patient_evaluation_available",
]


class PersonalizationDomainReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str
    input_ids: list[str]
    statuses: dict[PatientInputStatus, int]
    patient_derived_fraction: float | None
    notes: list[str] = Field(default_factory=list)


class PatientModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    patient_id: str
    task: str = "ventricular_activation_and_qrs"
    claim_ceiling: ClaimCeiling
    scientific_grade_requested: bool
    anatomy_acquisition_id: str
    calibration_backend: str
    confirmation_backend: str
    observation_model: str
    torso_mode: str
    electrode_mode: str
    evidence_split: EvidenceSplitReport
    patient_qc: PatientCaseQC
    personalization_counts: dict[PatientInputStatus, int]
    personalization_domains: list[PersonalizationDomainReport]
    model_inputs: list[PatientModelInput]
    parameter_mapping: dict[str, Any] | None = None
    heldout_ecg_validation: ECGValidationReport | None = None
    limitations: list[str] = Field(default_factory=list)
    use_constraints: list[str] = Field(default_factory=list)


def _domain_reports(inputs: list[PatientModelInput]) -> list[PersonalizationDomainReport]:
    grouped: dict[str, list[PatientModelInput]] = defaultdict(list)
    for item in inputs:
        grouped[item.domain].append(item)

    reports: list[PersonalizationDomainReport] = []
    for domain, items in sorted(grouped.items()):
        counts = Counter(item.status for item in items)
        known = [item for item in items if item.status != "UNKNOWN"]
        patient_derived = [
            item
            for item in known
            if item.status in {"MEASURED", "ESTIMATED_FROM_PATIENT"}
        ]
        fraction = None if not known else len(patient_derived) / len(known)
        notes: list[str] = []
        if any(item.status == "POPULATION_PRIOR" for item in items):
            notes.append("contains population-derived prior information")
        if any(item.status == "ASSUMED" for item in items):
            notes.append("contains explicit model assumptions")
        if any(item.status == "UNKNOWN" for item in items):
            notes.append("contains unknown model inputs")
        reports.append(
            PersonalizationDomainReport(
                domain=domain,
                input_ids=[item.input_id for item in items],
                statuses={status: int(count) for status, count in sorted(counts.items())},
                patient_derived_fraction=fraction,
                notes=notes,
            )
        )
    return reports


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def build_patient_model_report(
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    workflow_result: PatientEPWorkflowResult | None = None,
    parameter_mapping: EPParameterMappingSpec | dict[str, Any] | None = None,
    heldout_ecg_validation: ECGValidationReport | dict[str, Any] | None = None,
) -> PatientModelReport:
    """Build an auditable summary without inflating the validation claim."""
    if spec.patient_id != case.patient_id:
        raise ValueError("workflow spec patient does not match PatientCase")

    split = validate_patient_ep_workflow(case, spec)
    qc = assess_patient_case_for_ep(case)

    if workflow_result is not None and workflow_result.patient_id != case.patient_id:
        raise ValueError("workflow result patient does not match PatientCase")

    mapping_model = None
    if parameter_mapping is not None:
        mapping_model = (
            parameter_mapping
            if isinstance(parameter_mapping, EPParameterMappingSpec)
            else EPParameterMappingSpec.model_validate(parameter_mapping)
        )
        if mapping_model.source_backend != spec.fast_backend:
            raise ValueError("parameter mapping source backend does not match workflow")
        if mapping_model.target_backend != spec.confirmation_backend:
            raise ValueError("parameter mapping target backend does not match workflow")

    ecg_report = None
    if heldout_ecg_validation is not None:
        ecg_report = (
            heldout_ecg_validation
            if isinstance(heldout_ecg_validation, ECGValidationReport)
            else ECGValidationReport.model_validate(heldout_ecg_validation)
        )
        if ecg_report.evidence_id not in set(spec.validation_evidence_ids):
            raise ValueError(
                "held-out ECG validation evidence_id is not declared in validation_evidence_ids"
            )
        if not ecg_report.withheld_from_fit:
            raise ValueError("held-out ECG validation must remain withheld_from_fit")

    limitations: list[str] = list(qc.warnings)
    if workflow_result is not None:
        limitations.extend(workflow_result.limitations)
    if spec.torso_mode != "patient_specific":
        limitations.append("torso geometry is not patient-specific")
    if spec.electrode_mode != "recorded":
        limitations.append("electrode coordinates are not directly recorded")
    if any(
        item.domain == "fibres" and item.status not in {"MEASURED", "ESTIMATED_FROM_PATIENT"}
        for item in case.model_inputs
    ):
        limitations.append("myocardial fibre architecture is not patient-derived")
    if any(item.domain == "scar" and item.status == "UNKNOWN" for item in case.model_inputs):
        limitations.append("patient scar characterization is unknown")
    if ecg_report is None:
        limitations.append("no held-out ECG evaluation report is attached")

    if not qc.ready_for_ep_reconstruction:
        claim_ceiling: ClaimCeiling = "blocked"
    elif workflow_result is None or not spec.scientific_grade:
        claim_ceiling = "software_plumbing_only"
    elif ecg_report is None:
        claim_ceiling = "patient_specific_research_prediction"
    else:
        claim_ceiling = "heldout_patient_evaluation_available"

    use_constraints = [
        "research use only; this report is not evidence of clinical qualification",
        "do not interpret fitted parameters as uniquely identified biological truth without identifiability evidence",
        "do not use calibration observations as validation evidence",
    ]
    if spec.validation_mode == "within_patient_holdout":
        use_constraints.append(
            "within-patient holdout supports prediction for this patient; cohort generalization requires patient-disjoint evaluation"
        )
    if ecg_report is not None and ecg_report.acceptance_passed is False:
        use_constraints.append("predeclared held-out ECG acceptance criteria were not met")

    counts = Counter(item.status for item in case.model_inputs)
    return PatientModelReport(
        patient_id=case.patient_id,
        claim_ceiling=claim_ceiling,
        scientific_grade_requested=spec.scientific_grade,
        anatomy_acquisition_id=spec.anatomy_acquisition_id,
        calibration_backend=spec.fast_backend,
        confirmation_backend=spec.confirmation_backend,
        observation_model=spec.observation_model,
        torso_mode=spec.torso_mode,
        electrode_mode=spec.electrode_mode,
        evidence_split=(workflow_result.evidence_split if workflow_result else split),
        patient_qc=(workflow_result.patient_qc if workflow_result else qc),
        personalization_counts={status: int(count) for status, count in sorted(counts.items())},
        personalization_domains=_domain_reports(case.model_inputs),
        model_inputs=list(case.model_inputs),
        parameter_mapping=(
            None
            if mapping_model is None
            else {
                **mapping_model.model_dump(mode="json"),
                "mapping_fingerprint": mapping_model.fingerprint,
            }
        ),
        heldout_ecg_validation=ecg_report,
        limitations=_deduplicate(limitations),
        use_constraints=_deduplicate(use_constraints),
    )
