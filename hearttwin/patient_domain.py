"""Evidence-bound scalar mechanics/circulation personalization.

Narrow v1: explicit scalar observables and units, bounded MAP calibration,
independent forward replay and downstream held-out residuals. No clinical or
posterior-coverage claim is inferred from fitting success.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from .patient_model import PatientCase, assess_evidence_split
from .patient_execution import write_stage, runtime_manifest
from .provenance import sha256


class ScalarTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    source_file_id: str
    model_output: str
    unit: str = Field(min_length=1)
    noise_sd: float = Field(gt=0, allow_inf_nan=False)
    max_abs_error: float = Field(ge=0, allow_inf_nan=False)


class PatientDomainJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: PatientCase
    case_revision: int = Field(ge=1)
    case_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain: Literal["mechanics", "circulation"]
    forward_template: dict[str, Any]
    parameter_paths: dict[str, str]
    parameter_bounds: dict[str, tuple[float, float]]
    parameter_units: dict[str, str]
    targets: list[ScalarTarget]
    sampler_settings: dict[str, Any] = Field(default_factory=dict)
    seed: int = 42
    record_trace: bool = False


def source_for_target(job, target):
    evidence = {x.evidence_id: x for x in job.case.evidence}[target.evidence_id]
    if not evidence.acquisition_id:
        raise ValueError("Scalar patient targets require acquisition-backed evidence")
    acquisition = next(
        x for x in job.case.acquisitions if x.acquisition_id == evidence.acquisition_id
    )
    sources = [
        x for x in acquisition.source_files if x.file_id == target.source_file_id
    ]
    if len(sources) != 1 or sources[0].sha256 is None:
        raise ValueError("Target requires exactly one hash-pinned acquisition source")
    return evidence, sources[0]


def read_scalar(source, unit):
    parsed = urlparse(source.uri)
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise ValueError("Scalar patient evidence requires a local/file artifact")
    path = Path(unquote(parsed.path) if parsed.scheme else source.uri)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source.sha256:
        raise ValueError("Scalar evidence failed SHA-256 verification")
    item = json.loads(raw)
    if (
        not isinstance(item, dict)
        or set(item) != {"value", "unit"}
        or item["unit"] != unit
    ):
        raise ValueError(
            "Scalar evidence must contain value and matching unit; convert explicitly before ingestion"
        )
    if (
        isinstance(item["value"], bool)
        or not isinstance(item["value"], (int, float))
        or not math.isfinite(item["value"])
    ):
        raise ValueError("Scalar evidence must be a finite number")
    return float(item["value"])


def value_at(value, path):
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def set_at(value, path, number):
    parts = path.split(".")
    for part in parts[:-1]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    if isinstance(value, list):
        value[int(parts[-1])] = number
    else:
        if parts[-1] not in value:
            raise ValueError(
                "Parameter paths must identify explicit existing template values"
            )
        value[parts[-1]] = number


def validate_domain_job(job):
    if job.forward_template.get("subject_id") != job.case.patient_id:
        raise ValueError("Forward template subject must match patient")
    if not job.forward_template.get("backend"):
        raise ValueError("Forward template backend must be explicit")
    if not job.targets or len({x.evidence_id for x in job.targets}) != len(job.targets):
        raise ValueError("Declare unique scalar target evidence IDs")
    if (
        not job.parameter_paths
        or set(job.parameter_paths) != set(job.parameter_bounds)
        or set(job.parameter_paths) != set(job.parameter_units)
    ):
        raise ValueError(
            "Parameter paths, bounds and units must have the same nonempty keys"
        )
    if len(set(job.parameter_paths.values())) != len(job.parameter_paths):
        raise ValueError("Parameter paths must be unique")
    allowed = (
        ("parameters.passive.", "parameters.active.", "circulation.parameters.")
        if job.domain == "mechanics"
        else ("boundary_conditions.",)
    )
    for name, path in job.parameter_paths.items():
        if not path.startswith(allowed):
            raise ValueError(
                "Parameters may only update declared physiology fields, not backend or geometry"
            )
        lo, hi = job.parameter_bounds[name]
        if (
            not math.isfinite(lo)
            or not math.isfinite(hi)
            or lo >= hi
            or not job.parameter_units[name].strip()
        ):
            raise ValueError("Bounds must be finite/increasing and units explicit")
        initial = value_at(job.forward_template, path)
        if (
            isinstance(initial, bool)
            or not isinstance(initial, (int, float))
            or not lo <= initial <= hi
        ):
            raise ValueError(
                "Initial template parameter must be numeric and within bounds"
            )
    evidence = []
    source_roles = {}
    for target in job.targets:
        item, source = source_for_target(job, target)
        evidence.append(item)
        if item.role not in {"calibration", "validation"}:
            raise ValueError(
                "Target evidence must be explicitly calibration or validation"
            )
        if source.sha256 in source_roles and source_roles[source.sha256] != item.role:
            raise ValueError(
                "Calibration and holdout sources have identical content hashes"
            )
        source_roles[source.sha256] = item.role
    split = assess_evidence_split(evidence, mode="within_patient_holdout")
    if not split.passed:
        raise ValueError("Invalid domain evidence split: " + "; ".join(split.reasons))

    def reject_embedded_observations(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key in {"observations", "likelihood", "observed", "evidence"}
                    and child
                ):
                    raise ValueError(
                        "Forward templates cannot embed fitting or validation observations"
                    )
                reject_embedded_observations(child)
        elif isinstance(value, list):
            for child in value:
                reject_embedded_observations(child)

    reject_embedded_observations(job.forward_template)
    encoded = json.dumps(job.forward_template)
    for target in job.targets:
        item, source = source_for_target(job, target)
        if item.role == "validation" and (
            source.uri in encoded or source.sha256 in encoded
        ):
            raise ValueError("Forward template contains a held-out source reference")
    return split


def invoke(registry, capability, payload):
    adapter = registry.capability(capability)
    if adapter is None or not adapter.available():
        raise RuntimeError(f"Required patient capability is unavailable: {capability}")
    result = adapter.invoke(capability, payload)
    if not isinstance(result, dict):
        raise ValueError("Patient service returned a non-object")
    if result.get("subject_id", payload.get("subject_id")) != payload.get("subject_id"):
        raise ValueError("Patient service returned another subject")
    if isinstance(result.get("qc"), dict) and result["qc"].get("passed") is False:
        raise ValueError("Patient forward solver failed QC")
    return result


def forward_payload(template, paths, parameters):
    if set(parameters) != set(paths):
        raise ValueError("Inference parameters differ from declared parameter paths")
    payload = deepcopy(template)
    for name, number in parameters.items():
        if not math.isfinite(float(number)):
            raise ValueError("Forward parameters must be finite")
        set_at(payload, paths[name], float(number))
    return payload


def execute_patient_domain(payload, registry, *, output_dir="patient-domain"):
    job = PatientDomainJob.model_validate(payload)
    split = validate_domain_job(job)
    root = Path(output_dir)
    write_stage(
        root / "binding.json",
        {
            "case_revision": job.case_revision,
            "case_fingerprint": job.case_fingerprint,
            "request_sha256": sha256(job.model_dump(mode="json")),
            "evidence_split": split.model_dump(mode="json"),
            "runtime": runtime_manifest(),
        },
    )
    capability = "mechanics.simulate" if job.domain == "mechanics" else "flow.simulate"
    template = deepcopy(job.forward_template)
    # Large intermediate artifacts belong to the run directory, not the input dataset.
    if job.domain == "mechanics":
        template.setdefault("settings", {})["output_dir"] = str(
            (root / "forward").resolve()
        )
        anatomy = template.get("anatomy_ref")
        if not isinstance(anatomy, dict) or not anatomy.get("sha256"):
            raise ValueError("Patient mechanics requires hash-pinned anatomy_ref")
        template["settings"]["inline_series"] = True
    likelihood = []
    for target in job.targets:
        evidence, source = source_for_target(job, target)
        if evidence.role == "calibration":
            read_scalar(source, target.unit)
            likelihood.append(
                {
                    "term_id": target.evidence_id,
                    "observation_ref": {
                        "artifact_id": source.file_id,
                        "kind": "patient_scalar",
                        "uri": source.uri,
                        "sha256": source.sha256,
                    },
                    "model_output": target.model_output,
                    "discrepancy": "gaussian",
                    "noise_parameters": {"sigma": target.noise_sd},
                    "weight": 1.0,
                    "metadata": {"observation_path": "value"},
                }
            )
    context = {
        "forward_model": {
            "mode": "command",
            "command": [sys.executable, "-m", "hearttwin.patient_domain_forward"],
            "timeout_s": 300,
        },
        "patient_forward": {
            "capability": capability,
            "template": template,
            "parameter_paths": job.parameter_paths,
        },
    }
    inference_request = {
        "subject_id": job.case.patient_id,
        "model_service": "CardiMech" if job.domain == "mechanics" else "CardiFlow",
        "model_capability": capability,
        "backend": "native-map-de-v1",
        "priors": [
            {
                "name": name,
                "distribution": "uniform",
                "bounds": list(bounds),
                "unit": job.parameter_units[name],
            }
            for name, bounds in job.parameter_bounds.items()
        ],
        "likelihood": likelihood,
        "model_context": context,
        "sampler_settings": job.sampler_settings,
        "seed": job.seed,
    }
    write_stage(root / "inference-request.json", inference_request)
    inference = invoke(registry, "infer.run", inference_request)
    write_stage(root / "inference.json", inference)
    selected = inference.get("diagnostics", {}).get("best_parameters")
    if not isinstance(selected, dict):
        raise ValueError("MAP inference did not return explicit best_parameters")
    prediction = invoke(
        registry, capability, forward_payload(template, job.parameter_paths, selected)
    )
    write_stage(root / "prediction.json", prediction)
    residuals = []
    # Read holdouts only after independent replay has emitted a prediction.
    for target in job.targets:
        evidence, source = source_for_target(job, target)
        if evidence.role != "validation":
            continue
        observed = read_scalar(source, target.unit)
        predicted = value_at(prediction, target.model_output)
        if (
            isinstance(predicted, bool)
            or not isinstance(predicted, (int, float))
            or not math.isfinite(predicted)
        ):
            raise ValueError("Declared output must resolve to a finite scalar")
        actual_unit = prediction.get("units", {}).get(
            target.model_output.split(".")[-1]
        )
        if actual_unit is not None and actual_unit != target.unit:
            raise ValueError("Predicted output units differ from declared target unit")
        error = float(predicted) - observed
        residuals.append(
            {
                "evidence_id": target.evidence_id,
                "source_sha256": source.sha256,
                "observed": observed,
                "predicted": predicted,
                "unit": target.unit,
                "error": error,
                "max_abs_error": target.max_abs_error,
                "passed": abs(error) <= target.max_abs_error,
            }
        )
    output = {
        "patient_id": job.case.patient_id,
        "case_revision": job.case_revision,
        "case_fingerprint": job.case_fingerprint,
        "domain": job.domain,
        "inference": inference,
        "prediction": prediction,
        "heldout_residuals": residuals,
        "heldout_acceptance_passed": bool(residuals)
        and all(x["passed"] for x in residuals),
        "uncertainty": "MAP point estimate; posterior uncertainty not computed",
        "clinical_qualification": "not_established",
    }
    write_stage(root / "report.json", output)
    if job.record_trace:
        trace_payload = {
            "entity_id": job.case.patient_id,
            "context": {
                "workflow": "patient_" + job.domain,
                "research_use_only": True,
                "case_revision": job.case_revision,
                "case_fingerprint": job.case_fingerprint,
            },
            "fingerprints": {
                "request_sha256": sha256(job.model_dump(mode="json")),
                "inference_sha256": sha256(inference),
                "prediction_sha256": sha256(prediction),
            },
            "heldout_residuals": residuals,
        }
        trace = invoke(registry, "trace.record", trace_payload)
        if not trace.get("run_id") or str(trace.get("status", "")).lower() not in {
            "succeeded",
            "success",
            "ok",
        }:
            raise ValueError("CardiTrace did not acknowledge the domain run")
        output["trace"] = trace
        write_stage(root / "trace.json", trace)
        write_stage(root / "report.json", output)
    return output
