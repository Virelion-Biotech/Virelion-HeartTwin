"""Patient-bound native service plans with explicit input and artifact handoffs.

This changes orchestration, not the scientific scope of installed solvers.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .patient_model import PatientCase
from .patient_observations import verified_bytes
from .provenance import sha256

CAPABILITIES = {
    "anatomy.build", "anatomy.validate", "anatomy.geometry.measure",
    "electrical.analyze", "electrical.prepare_calibration", "mechanical.analyze",
    "ep.simulate", "mechanics.simulate", "flow.simulate", "therapy.run",
    "simulation.run", "simulation.cardiac_twin", "imaging.qc", "safety.score",
}


class PatientStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    capability: str
    template: dict[str, Any]
    purpose: str = Field(min_length=1)
    # Enumerate any physiological literals, solver knobs and scenario choices.
    literal_rationale: dict[str, str] = Field(default_factory=dict)


class PatientPlanJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: PatientCase
    case_revision: int = Field(ge=1)
    case_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    encounter_id: str
    stages: list[PatientStage] = Field(min_length=1, max_length=30)
    carry_forward: dict[str, str] = Field(default_factory=dict)
    purpose: Literal["baseline", "followup", "intervention_comparison"] = "baseline"


def source_in_case(case, acquisition_id, file_id):
    acquisition = next((a for a in case.acquisitions if a.acquisition_id == acquisition_id), None)
    if acquisition is None:
        raise ValueError("Patient source acquisition is not in this case")
    source = next((s for s in acquisition.source_files if s.file_id == file_id), None)
    if source is None:
        raise ValueError("Patient source file is not in the declared acquisition")
    return acquisition, source


def usable_acquisition(case, acquisition_id, encounter_id, carry_forward):
    acquisition = next(a for a in case.acquisitions if a.acquisition_id == acquisition_id)
    if acquisition.encounter_id != encounter_id and not carry_forward.get(acquisition_id, "").strip():
        raise ValueError("Different or unknown physiologic encounter requires explicit carry-forward rationale: " + acquisition_id)
    if any(e.acquisition_id == acquisition_id and e.role in {"validation", "excluded"} for e in case.evidence):
        raise ValueError("Held-out or excluded acquisition cannot supply patient forward inputs")
    heldout_hashes = {s.sha256 for a in case.acquisitions
                      if any(e.acquisition_id == a.acquisition_id and e.role == "validation" for e in case.evidence)
                      for s in a.source_files if s.sha256}
    if any(s.sha256 in heldout_hashes for s in acquisition.source_files):
        raise ValueError("Patient input duplicates held-out content")
    return acquisition


def resolve_patient_template(value, job, *, results=None, audit=None, path="", verify=True):
    results = {} if results is None else results
    audit = [] if audit is None else audit
    if isinstance(value, dict):
        if "$input" in value:
            if not {"$input", "unit"} <= set(value) or set(value) - {"$input", "unit", "path"}:
                raise ValueError("Input binding requires exactly $input and unit")
            item = next((x for x in job.case.model_inputs if x.input_id == value["$input"]), None)
            if item is None or item.status == "UNKNOWN" or item.value is None:
                raise ValueError("Patient input is missing or UNKNOWN")
            if item.unit != value["unit"]:
                raise ValueError("Input units differ; explicit conversion required")
            if item.status in {"MEASURED", "ESTIMATED_FROM_PATIENT"} and not item.source_acquisition_ids:
                raise ValueError("Patient plan inputs require acquisition-backed provenance")
            for acquisition_id in item.source_acquisition_ids:
                acquisition = usable_acquisition(job.case, acquisition_id, job.encounter_id, job.carry_forward)
                if not acquisition.source_files:
                    raise ValueError("Patient input acquisition lacks source artifacts")
                if verify:
                    for source in acquisition.source_files:
                        verified_bytes(source)
            audit.append({"path": path, "input_id": item.input_id, "status": item.status,
                          "unit": item.unit, "source_acquisition_ids": item.source_acquisition_ids,
                          "method": item.method, "assumption_reason": item.assumption_reason,
                          "population_reference": item.population_reference})
            result = item.value
            if value.get("path"):
                from .patient_domain import value_at
                result = value_at(result, value["path"])
            return deepcopy(result)
        if "$source" in value:
            if set(value) != {"$source"} or set(value["$source"]) != {"acquisition_id", "file_id", "kind"}:
                raise ValueError("Source binding requires acquisition_id, file_id and native artifact kind")
            spec = value["$source"]
            acquisition, source = source_in_case(job.case, spec["acquisition_id"], spec["file_id"])
            usable_acquisition(job.case, acquisition.acquisition_id, job.encounter_id, job.carry_forward)
            if verify:
                verified_bytes(source)
            audit.append({"path": path, "acquisition_id": acquisition.acquisition_id,
                          "source_sha256": source.sha256, "status": "PATIENT_ARTIFACT"})
            return {"artifact_id": source.file_id, "kind": spec["kind"], "uri": source.uri, "sha256": source.sha256}
        if "$result" in value:
            if set(value) != {"$result"}:
                raise ValueError("Result binding requires exactly $result")
            from .patient_domain import value_at
            stage, _, output = value["$result"].partition(".")
            if stage not in results:
                raise ValueError("Result binding must reference a preceding stage")
            audit.append({"path": path, "upstream_stage": stage, "output_path": output,
                          "status": "DERIVED_FROM_PATIENT_RUN"})
            if results[stage] is None:  # admission validation: output exists only at execution
                return None
            return deepcopy(value_at(results[stage], output))
        if isinstance(job, PatientPlanJob) and "uri" in value and "artifact_id" in value and not str(value["uri"]).startswith("urn:hearttwin:case:"):
            raise ValueError("Native artifact inputs must use a patient $source or preceding $result binding")
        if any(key.startswith("$") for key in value):
            raise ValueError("Unsupported patient binding")
        return {k: resolve_patient_template(v, job, results=results, audit=audit,
                path=path + "." + k if path else k, verify=verify) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_patient_template(v, job, results=results, audit=audit,
                path=f"{path}.{i}", verify=verify) for i, v in enumerate(value)]
    return value


def numeric_literals(value, path=""):
    if isinstance(value, dict):
        if any(k.startswith("$") for k in value):
            return []
        return [p for k, v in value.items() for p in numeric_literals(v, path + "." + k if path else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in numeric_literals(v, f"{path}.{i}")]
    return [path] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


def validate_patient_plan(job, *, verify=False):
    if job.encounter_id not in {e.encounter_id for e in job.case.encounters}:
        raise ValueError("Select an encounter in this patient case")
    if len({s.stage_id for s in job.stages}) != len(job.stages):
        raise ValueError("Patient stage IDs must be unique")
    results, manifest = {}, []
    for stage in job.stages:
        if stage.capability not in CAPABILITIES:
            raise ValueError("Unsupported patient plan capability")
        if stage.template.get("subject_id") != job.case.patient_id:
            raise ValueError("Each stage must explicitly identify this patient")
        missing = [p for p in numeric_literals(stage.template) if not stage.literal_rationale.get(p, "").strip()]
        if missing:
            raise ValueError("Declare rationale for every numeric literal: " + ", ".join(missing))
        audit = []
        resolve_patient_template(stage.template, job, results=results, audit=audit, verify=verify)
        if not any(x["status"] in {"MEASURED", "ESTIMATED_FROM_PATIENT", "PATIENT_ARTIFACT", "DERIVED_FROM_PATIENT_RUN"} for x in audit):
            raise ValueError("Every patient stage requires measured/estimated patient evidence, a patient source or a prior-stage binding")
        manifest.append({"stage_id": stage.stage_id, "capability": stage.capability,
                         "purpose": stage.purpose, "bindings": audit, "literal_rationale": stage.literal_rationale})
        results[stage.stage_id] = None
    return manifest


def execute_patient_plan(payload, registry, *, output_dir="patient-plan"):
    from .patient_domain import invoke
    from .patient_execution import runtime_manifest, write_stage

    job = PatientPlanJob.model_validate(payload)
    manifest = validate_patient_plan(job, verify=True)
    root = Path(output_dir)
    write_stage(root / "binding.json", {"patient_id": job.case.patient_id,
                "case_revision": job.case_revision, "case_fingerprint": job.case_fingerprint,
                "encounter_id": job.encounter_id, "request_sha256": sha256(job.model_dump(mode="json")),
                "carry_forward": job.carry_forward, "stages": manifest, "runtime": runtime_manifest()})
    results = {}
    for stage in job.stages:
        audit = []
        request = resolve_patient_template(stage.template, job, results=results, audit=audit)
        if request.get("subject_id") != job.case.patient_id:
            raise ValueError("Resolved patient identity mismatch")
        write_stage(root / (stage.stage_id + "-request.json"), request)
        result = invoke(registry, stage.capability, request)
        # Downstream artifact refs must preserve their bytes, not just their labels.
        def check_artifacts(value):
            if isinstance(value, dict):
                if {"uri", "sha256"} <= value.keys():
                    from .patient_model import SourceFileRef
                    verified_bytes(SourceFileRef(file_id=value.get("artifact_id", "derived"), uri=value["uri"], sha256=value["sha256"]))
                for child in value.values():
                    check_artifacts(child)
            elif isinstance(value, list):
                for child in value:
                    check_artifacts(child)
        check_artifacts(result)
        write_stage(root / (stage.stage_id + "-result.json"), result)
        results[stage.stage_id] = result
    report = {"patient_id": job.case.patient_id, "case_revision": job.case_revision,
              "case_fingerprint": job.case_fingerprint, "encounter_id": job.encounter_id,
              "purpose": job.purpose, "stages": results, "personalization_manifest": manifest,
              "clinical_qualification": "not_established",
              "scope": "Patient-bound native simulations; literal assumptions and native solver limits remain explicit"}
    write_stage(root / "report.json", report)
    return report


def longitudinal_changes(older, newer):
    """Explain when a prior personalization must be reconsidered; never reuse fitted values."""
    old = PatientCase.model_validate(older)
    new = PatientCase.model_validate(newer)
    if old.patient_id != new.patient_id:
        raise ValueError("Longitudinal comparison requires the same patient")
    changed = {}
    for name, key in [("encounters", "encounter_id"), ("acquisitions", "acquisition_id"),
                      ("model_inputs", "input_id"), ("evidence", "evidence_id")]:
        a = {getattr(x, key): x.model_dump(mode="json") for x in getattr(old, name)}
        b = {getattr(x, key): x.model_dump(mode="json") for x in getattr(new, name)}
        changed[name] = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    return {"changed": changed, "requires_new_run": any(changed.values()),
            "prior_fit_reusable_automatically": False,
            "reason": "Changed evidence or physiologic state requires explicit new-revision execution"}
