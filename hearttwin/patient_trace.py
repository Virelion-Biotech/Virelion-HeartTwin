"""CardiTrace handoff for patient-specific HeartTwin workflows.

The trace boundary deliberately minimizes patient-data locators. Raw source URIs
and free-form source metadata are not copied into CardiTrace; hashes and stable
pseudonymous IDs preserve integrity and lineage without duplicating potentially
identifying filesystem/DICOM paths into the audit log.
"""

from __future__ import annotations

from copy import deepcopy
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


def _redacted_uri(record: dict[str, Any]) -> str:
    stable_id = record.get("artifact_id") or record.get("file_id")
    if stable_id:
        return f"redacted://{stable_id}"
    digest = record.get("sha256")
    if isinstance(digest, str) and digest:
        return f"redacted://sha256/{digest[:16]}"
    return "redacted://locator"


def _redact_artifact_uris(value: Any) -> Any:
    """Recursively remove storage locators while preserving artifact identity/hash."""
    if isinstance(value, list):
        return [_redact_artifact_uris(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _redact_artifact_uris(item) for key, item in value.items()}
    if "uri" in result:
        result["uri"] = _redacted_uri(result)
    return result


def _trace_safe_case(case_json: dict[str, Any]) -> dict[str, Any]:
    """Return a minimized case snapshot suitable for the provenance store.

    The exact original case is still bound by ``patient_case_sha256``. CardiTrace
    receives only redacted source locators and no free-form patient/acquisition/
    source-file metadata. Every source file must be content-hash pinned first.
    """
    safe = deepcopy(case_json)
    safe["metadata"] = {}
    for encounter in safe.get("encounters", []):
        if isinstance(encounter, dict):
            encounter["metadata"] = {}
    for acquisition in safe.get("acquisitions", []):
        if not isinstance(acquisition, dict):
            continue
        acquisition_metadata = acquisition.get("metadata")
        primary_file_id = None
        if isinstance(acquisition_metadata, dict):
            primary_file_id = acquisition_metadata.get("primary_file_id")
        acquisition["metadata"] = (
            {} if primary_file_id is None else {"primary_file_id": primary_file_id}
        )
        for source in acquisition.get("source_files", []):
            if not isinstance(source, dict):
                continue
            digest = source.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(
                    f"patient trace requires SHA-256 pinned source file {source.get('file_id')!r}"
                )
            source["uri"] = _redacted_uri(source)
            source["metadata"] = {}
    return safe


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

    original_case_json = case.model_dump(mode="json")
    case_json = _trace_safe_case(original_case_json)
    spec_json = spec.model_dump(mode="json")
    original_result_json = (
        None if workflow_result is None else workflow_result.model_dump(mode="json")
    )
    result_json = (
        None
        if original_result_json is None
        else _redact_artifact_uris(original_result_json)
    )
    report_json = None if report is None else report.model_dump(mode="json")
    mapping_json = None
    if parameter_mapping is not None:
        mapping_json = {
            **parameter_mapping.model_dump(mode="json"),
            "mapping_fingerprint": parameter_mapping.fingerprint,
        }

    fingerprints = {
        # Bind the exact in-memory case while storing a minimized copy.
        "patient_case_sha256": sha256(original_case_json),
        "trace_patient_case_sha256": sha256(case_json),
        "workflow_spec_sha256": sha256(spec_json),
    }
    if original_result_json is not None and result_json is not None:
        fingerprints["workflow_result_sha256"] = sha256(original_result_json)
        fingerprints["trace_workflow_result_sha256"] = sha256(result_json)
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
            "source_locators_redacted": True,
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
    lifecycle_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the minimized patient workflow through the CardiTrace boundary."""
    envelope = build_patient_trace_payload(
        case=case,
        spec=spec,
        workflow_result=workflow_result,
        report=report,
        parameter_mapping=parameter_mapping,
    )
    if lifecycle_binding is not None:
        if set(lifecycle_binding) != {
            "case_revision",
            "case_fingerprint",
            "request_sha256",
        }:
            raise ValueError("Trace lifecycle binding has unsupported fields")
        if (
            type(lifecycle_binding["case_revision"]) is not int
            or lifecycle_binding["case_revision"] < 1
        ):
            raise ValueError("Trace case revision must be positive")
        for key in ("case_fingerprint", "request_sha256"):
            digest = lifecycle_binding[key]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError("Trace lifecycle hashes must be SHA-256")
        envelope.context["patient_lifecycle"] = lifecycle_binding
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
        raise RuntimeError(
            f"CardiTrace did not report success: {output.get('status')!r}"
        )
    return output
