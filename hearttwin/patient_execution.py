"""Patient EP execution for the isolated worker, with recoverable stage artifacts."""

from __future__ import annotations

import importlib.metadata
import json
import platform
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .patient_ecg_eval import ECGEvaluationSpec
from .patient_model import (
    PatientCase,
    PatientEPWorkflowSpec,
    validate_patient_ep_workflow,
)
from .patient_report import build_patient_model_report
from .patient_trace import record_patient_trace
from .provenance import sha256
from .scientific_api import ScientificVirelionServices


class PatientEPJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: PatientCase
    spec: PatientEPWorkflowSpec
    case_revision: int = Field(ge=1)
    case_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_spec: ECGEvaluationSpec
    solver: dict[str, Any]
    record_trace: bool = True

    @model_validator(mode="after")
    def enforce_frozen_roles(self):
        evidence = {item.evidence_id: item for item in self.case.evidence}
        for role, ids in (
            ("calibration", self.spec.calibration_evidence_ids),
            ("validation", self.spec.validation_evidence_ids),
        ):
            for evidence_id in ids:
                item = evidence.get(evidence_id)
                if (
                    item is None
                    or item.role != role
                    or item.withheld_from_fit != (role == "validation")
                ):
                    raise ValueError(
                        "Patient job evidence roles must match the frozen case revision; create a reviewed revision to change roles"
                    )
        return self


def write_stage(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def execute_patient_ep(payload, registry, *, output_dir="patient"):
    job = PatientEPJob.model_validate(payload)
    validate_patient_ep_workflow(job.case, job.spec)
    from .patient_integrity import verify_patient_sources

    verify_patient_sources(
        job.case, job.spec.calibration_evidence_ids, job.spec.validation_evidence_ids
    )
    if job.evaluation_spec.acceptance is None:
        raise ValueError(
            "Patient jobs require predeclared held-out acceptance criteria"
        )
    if not job.spec.scientific_grade:
        raise ValueError("Patient workbench execution requires scientific_grade=True")
    root = Path(output_dir)
    packages = {}
    for name in (
        "virelion-hearttwin",
        "virelion-cardiep",
        "virelion-cardianatomy",
        "electrotrace",
        "virelion-cardiinfer",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not_installed_as_distribution"
    binding = {
        "patient_id": job.case.patient_id,
        "case_revision": job.case_revision,
        "case_fingerprint": job.case_fingerprint,
        "case_sha256": sha256(job.case.model_dump(mode="json")),
        "request_sha256": sha256(job.model_dump(mode="json")),
        "python": platform.python_version(),
        "packages": packages,
    }
    binding["runtime"] = runtime_manifest()
    write_stage(root / "binding.json", binding)
    services = ScientificVirelionServices(registry)
    result = services.run_patient_ep(job.case, job.spec, **job.solver)
    write_stage(root / "prediction.json", result.model_dump(mode="json"))
    # Prediction is durable before evaluation can read the held-out measurements.
    reports = services.evaluate_patient_ep_holdouts(result, spec=job.evaluation_spec)
    validation = [report.model_dump(mode="json") for report in reports]
    write_stage(root / "validation.json", validation)
    model_reports = [
        build_patient_model_report(
            case=job.case,
            spec=job.spec,
            workflow_result=result,
            parameter_mapping=job.solver.get("parameter_mapping"),
            heldout_ecg_validation=report,
        ).model_dump(mode="json")
        for report in reports
    ]
    if not reports:
        raise ValueError("Patient run returned no held-out evaluation reports")
    complete = set(job.spec.validation_evidence_ids) == {
        report.evidence_id for report in reports
    }
    passed = complete and all(report.acceptance_passed is True for report in reports)
    output = {
        "binding": binding,
        "workflow": result.model_dump(mode="json"),
        "validation": validation,
        "patient_reports": model_reports,
        "all_holdouts_evaluated": complete,
        "heldout_acceptance_passed": passed,
        "clinical_qualification": "not_established",
    }
    write_stage(root / "report.json", output)
    if job.record_trace:
        # Record every holdout report rather than arbitrarily choosing one.
        traces = []
        from .ep_parameter_mapping import EPParameterMappingSpec

        mapping = job.solver.get("parameter_mapping")
        mapping = (
            None if mapping is None else EPParameterMappingSpec.model_validate(mapping)
        )
        from .patient_report import PatientModelReport

        for report in model_reports:
            traces.append(
                record_patient_trace(
                    registry,
                    case=job.case,
                    spec=job.spec,
                    workflow_result=result,
                    report=PatientModelReport.model_validate(report),
                    parameter_mapping=mapping,
                    lifecycle_binding={
                        key: binding[key]
                        for key in (
                            "case_revision",
                            "case_fingerprint",
                            "request_sha256",
                        )
                    },
                )
            )
        output["traces"] = traces
        write_stage(root / "trace.json", traces)
    write_stage(root / "report.json", output)
    return output


def runtime_manifest():
    """Bind installed versions, VCS commits when available and source-file bytes."""
    import hashlib
    from importlib.resources import files

    records = {}
    for distribution in (
        "virelion-hearttwin",
        "virelion-cardiep",
        "virelion-cardianatomy",
        "electrotrace",
        "virelion-cardiinfer",
        "virelion-cardimech",
        "virelion-cardiflow",
    ):
        try:
            dist = importlib.metadata.distribution(distribution)
        except importlib.metadata.PackageNotFoundError:
            records[distribution] = {"installed": False}
            continue
        direct = dist.read_text("direct_url.json")
        info = {} if direct is None else json.loads(direct)
        record = {
            "installed": True,
            "version": dist.version,
            "vcs": info.get("vcs_info"),
        }
        # Editable installs may not inventory source modules in RECORD. Hash the
        # package directories explicitly so versions alone never stand in for code.
        packages = {
            "virelion-hearttwin": "hearttwin",
            "virelion-cardiep": "cardiep",
            "virelion-cardianatomy": "cardianatomy",
            "electrotrace": "electrotrace",
            "virelion-cardiinfer": "cardiinfer",
            "virelion-cardimech": "cardimech",
            "virelion-cardiflow": "cardiflow",
        }
        from importlib.util import find_spec

        spec = find_spec(packages[distribution])
        hashes = {}
        if spec and spec.submodule_search_locations:
            for location in spec.submodule_search_locations:
                base = Path(location)
                for path in sorted(base.rglob("*.py")):
                    hashes[str(path.relative_to(base))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
        record["source_files"] = hashes
        record["source_fingerprint"] = sha256(hashes)
        records[distribution] = record
    return {
        "python": platform.python_version(),
        "packages": records,
        "services_sha256": hashlib.sha256(
            files("hearttwin").joinpath("services.yaml").read_bytes()
        ).hexdigest(),
    }
