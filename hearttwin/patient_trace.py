"""CardiTrace handoff for patient-specific HeartTwin workflows."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .ep_parameter_mapping import EPParameterMappingSpec
from .patient_ep import PatientEPWorkflowResult
from .patient_model import PatientCase, PatientEPWorkflowSpec
from .patient_report import PatientModelReport
from .provenance import sha256
from .service_registry import ServiceRegistry


class PatientTraceEnvelope(BaseModel):
    """Pseudonymous, hash-linked payload sent to CardiTrace."""

    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    entity_id: str
    context: dict[str, Any] = Field(default_factory=dict)
    patient_case: dict[str, Any]
    workflow_spec: dict[str, Any]
    workflow_result: dict[str, Any] | None = None
    patient_model_report: dict[str, Any] | None = None
    parameter_mapping: dict[str, Any] | None = None
    fingerprints: dict[str, str] = Field(default_factory=dict)


def build_patient_trace_payload(
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    workflow_result: PatientEPWorkflowResult | None = None,
    report: PatientModelReport | None = None,
    parameter_mapping: EPParameterMappingSpec | None = None,
) -> PatientTraceEnvelope:
    if case.patient_id != spec.patient_id:
        raise ValueError("PatientCase and workflow spec patient IDs differ")
    if workflow_result is not None and workflow_result.patient_id != case.patient_id:
        raise ValueError("workflow result patient ID differs from PatientCase")
    if report is not None and report.patient_id != case.patient_id:
        raise ValueError("patient report ID differs from PatientCase")
    if parameter_mapping is not None:
        if parameter_mapping.source_backend != spec.fast_backend:
            raise ValueError("parameter mapping source backend differs from workflow")
        if parameter_mapping.target_backend != spec.confirmation_backend:
            raise ValueError("parameter mapping target backend differs from workflow")

    case_json = case.model_dump(mode="json")
    spec_json = spec.model_dump(mode="json")
    result_json = None if workflow_result is None else workflow_result.model_dump(mode="json")
    report_json = None if report is None else report.model_dump(mode="json")
    mapping_json = None
    if parameter_mapping is not None:
        mapping_json = {
            **parameter_mapping.model_dump(mode="json"),
            "mapping_fingerprint": parameter_mapping.fingerprint,
        }

    fingerprints = {
        "patient_case_sha256": sha256(case_json),
        "workflow_spec_sha256": sha256(spec_json),
    }
    if result_json is not None:
        fingerprints["workflow_result_sha256"] = sha256(result_json)
    if report_json is not None:
        fingerprints["patient_model_report_sha256"] = sha256(report_json)
    if mapping_json is not None:
        fingerprints["parameter_mapping_sha256"] = sha256(mapping_json)

    claim_ceiling = None if report is None else report.claim_ceiling
    return PatientTraceEnvelope(
        entity_id=case.patient_id,
        context={
            "workflow": "patient_specific_ep",
            "task": "ventricular_activation_and_qrs",
            "research_use_only": True,
            "claim_ceiling": claim_ceiling,
            "validation_mode": spec.validation_mode,
        },
        patient_case=case_json,
        workflow_spec=spec_json,
        workflow_result=result_json,
        patient_model_report=report_json,
        parameter_mapping=mapping_json,
        fingerprints=fingerprints,
    )


def record_patient_trace(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    workflow_result: PatientEPWorkflowResult | None = None,
    report: PatientModelReport | None = None,
    parameter_mapping: EPParameterMappingSpec | None = None,
) -> dict[str, Any]:
    """Record the patient workflow through the registered CardiTrace boundary."""
    envelope = build_patient_trace_payload(
        case=case,
        spec=spec,
        workflow_result=workflow_result,
        report=report,
        parameter_mapping=parameter_mapping,
    )
    adapter = registry.capability("trace.record")
    if adapter is None:
        raise RuntimeError("No service advertises trace.record")
    if not adapter.available():
        raise RuntimeError("CardiTrace is unavailable")
    output = adapter.invoke("trace.record", envelope.model_dump(mode="json"))
    if not isinstance(output, dict):
        raise RuntimeError("CardiTrace returned a non-object response")
    if not output.get("run_id"):
        raise RuntimeError("CardiTrace response has no run_id")
    if str(output.get("status", "")).lower() not in {"succeeded", "success", "ok"}:
        raise RuntimeError(f"CardiTrace did not report success: {output.get('status')!r}")
    return output
