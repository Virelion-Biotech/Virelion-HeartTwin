"""Evidence-bound scalar, curve and labelled-field mechanics/circulation personalization.

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
from .patient_observations import output_unit, read_observation, sample_prediction


class ScalarTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    source_file_id: str
    model_output: str
    unit: str = Field(min_length=1)
    noise_sd: float = Field(gt=0, allow_inf_nan=False)
    max_abs_error: float = Field(ge=0, allow_inf_nan=False)
    observation_kind: Literal["scalar", "curve", "field"] = "scalar"
    model_time: str | None = None
    model_labels: str | None = None
    prediction_unit: str | None = None
    field_quantity: str | None = None
    field_anatomy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PatientDomainJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: PatientCase
    case_revision: int = Field(ge=1)
    case_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain: Literal["mechanics", "circulation"]
    native_capability: Literal["patient.circulation.simulate", "patient.coupled.simulate"] | None = None
    forward_template: dict[str, Any]
    parameter_paths: dict[str, str]
    parameter_bounds: dict[str, tuple[float, float]]
    parameter_units: dict[str, str]
    targets: list[ScalarTarget]
    sampler_settings: dict[str, Any] = Field(default_factory=dict)
    seed: int = 42
    record_trace: bool = False
    encounter_id: str | None = None
    carry_forward: dict[str, str] = Field(default_factory=dict)


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


def effective_template(job, *, verify=False):
    from types import SimpleNamespace
    from .patient_personalization import resolve_patient_template

    audit = []
    context = SimpleNamespace(
        case=job.case, encounter_id=job.encounter_id, carry_forward=job.carry_forward
    )
    template = resolve_patient_template(
        job.forward_template, context, audit=audit, verify=verify
    )
    if audit and job.encounter_id is None:
        raise ValueError("Patient input mappings require an explicit encounter")
    return template, audit


def validate_domain_job(job):
    template, _ = effective_template(job)
    if job.native_capability:
        expected_domain = "mechanics" if job.native_capability == "patient.coupled.simulate" else "circulation"
        expected_backend = "monolithic-four-cavity-v1" if job.domain == "mechanics" else "native-four-chamber-v1"
        if job.domain != expected_domain or template.get("backend") != expected_backend or job.encounter_id is None:
            raise ValueError("Native calibration requires matching domain/backend and a frozen encounter")
    if job.encounter_id is not None:
        if job.encounter_id not in {e.encounter_id for e in job.case.encounters}:
            raise ValueError("Unknown patient encounter")
        for target in job.targets:
            evidence, _ = source_for_target(job, target)
            acquisition = next(
                a
                for a in job.case.acquisitions
                if a.acquisition_id == evidence.acquisition_id
            )
            if (
                acquisition.encounter_id != job.encounter_id
                and not job.carry_forward.get(acquisition.acquisition_id, "").strip()
            ):
                raise ValueError(
                    "Target from another physiologic state requires a carry-forward rationale"
                )
    if template.get("subject_id") != job.case.patient_id:
        raise ValueError("Forward template subject must match patient")
    if not template.get("backend"):
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
        if job.native_capability:
            expected_unit = native_parameter_unit(job.native_capability, path)
            if expected_unit is None or job.parameter_units[name] != expected_unit:
                raise ValueError("Native calibration parameters must name supported physiology fields with exact units")
        elif not path.startswith(allowed):
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
        initial = value_at(template, path)
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
    all_source_roles = {}
    for target in job.targets:
        if job.native_capability:
            native_units = {"pressure_mmHg": "mmHg", "volume_mL": "mL", "flow_mL_per_s": "mL/s"}
            namespace = target.model_output.split(".")[0]
            expected = native_units.get(namespace)
            if expected is not None and (target.unit != expected or target.prediction_unit not in {None, expected}):
                raise ValueError("Native prediction output and observation units disagree")
        if target.observation_kind == "curve" and not target.model_time:
            raise ValueError("Curve targets require an explicit prediction time path")
        if target.observation_kind == "field" and not (
            target.model_labels
            or (target.field_quantity and target.field_anatomy_sha256)
        ):
            raise ValueError("Field targets require an explicit prediction label path")
        item, source = source_for_target(job, target)
        evidence.append(item)
        acquisition = next(
            a for a in job.case.acquisitions if a.acquisition_id == item.acquisition_id
        )
        for artifact in acquisition.source_files:
            if (
                artifact.sha256
                and artifact.sha256 in all_source_roles
                and all_source_roles[artifact.sha256] != item.role
            ):
                raise ValueError(
                    "Calibration and holdout acquisitions share source bytes"
                )
            if artifact.sha256:
                all_source_roles[artifact.sha256] = item.role
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

    reject_embedded_observations(template)
    encoded = json.dumps(template)
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


def native_parameter_unit(capability, path):
    import re

    if re.fullmatch(r"edge_resistance_mmHg_s_per_mL\.\d+", path):
        return "mmHg*s/mL"
    if capability == "patient.coupled.simulate":
        if path in {"passive_Pa.mu", "passive_Pa.kappa"} or re.fullmatch(r"activation.maximum_tension_Pa\.\d+", path):
            return "Pa"
        if path in {"activation.rise_tau_s", "activation.decay_tau_s"}:
            return "s"
        if path in {"activation.voltage_threshold_V", "activation.voltage_slope_V"}:
            return "V"
        match = re.fullmatch(r"vessels\.(SA|SV|PA|PV)\.(compliance_mL_per_mmHg|unstressed_volume_mL|external_pressure_mmHg)", path)
    else:
        match = re.fullmatch(r"compartments\.(LA|LV|RA|RV|SA|SV|PA|PV)\.(compliance_mL_per_mmHg|unstressed_volume_mL|external_pressure_mmHg|emin_mmHg_per_mL|emax_mmHg_per_mL|activation_start_s|activation_duration_s)", path)
    if match:
        field = match.group(2)
        return {"compliance_mL_per_mmHg": "mL/mmHg", "unstressed_volume_mL": "mL", "external_pressure_mmHg": "mmHg",
                "emin_mmHg_per_mL": "mmHg/mL", "emax_mmHg_per_mL": "mmHg/mL", "activation_start_s": "s", "activation_duration_s": "s"}[field]
    return None


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
    template, input_audit = effective_template(job, verify=True)
    root = Path(output_dir)
    write_stage(
        root / "binding.json",
        {
            "case_revision": job.case_revision,
            "case_fingerprint": job.case_fingerprint,
            "request_sha256": sha256(job.model_dump(mode="json")),
            "evidence_split": split.model_dump(mode="json"),
            "runtime": runtime_manifest(),
            "input_bindings": input_audit,
            "encounter_id": job.encounter_id,
            "carry_forward": job.carry_forward,
        },
    )
    capability = job.native_capability or ("mechanics.simulate" if job.domain == "mechanics" else "flow.simulate")
    template = deepcopy(template)
    # Large intermediate artifacts belong to the run directory, not the input dataset.
    if job.native_capability:
        template["output_dir"] = str((root / "forward").resolve())
        from .patient_personalization import usable_acquisition
        from .patient_observations import verified_bytes

        def check_native_sources(value):
            if isinstance(value, dict):
                if "uri" in value and "sha256" in value:
                    matches = [(a, s) for a in job.case.acquisitions for s in a.source_files
                               if s.uri == value["uri"] and s.sha256 == value["sha256"]]
                    if len(matches) != 1:
                        raise ValueError("Native calibration sources must be uniquely frozen in the patient case")
                    acquisition, source = matches[0]
                    usable_acquisition(job.case, acquisition.acquisition_id, job.encounter_id, job.carry_forward)
                    verified_bytes(source)
                for child in value.values():
                    check_native_sources(child)
            elif isinstance(value, list):
                for child in value:
                    check_native_sources(child)
        check_native_sources(template)
    elif job.domain == "mechanics":
        template.setdefault("settings", {})["output_dir"] = str(
            (root / "forward").resolve()
        )
        anatomy = template.get("anatomy_ref")
        if not isinstance(anatomy, dict) or not anatomy.get("sha256"):
            raise ValueError("Patient mechanics requires hash-pinned anatomy_ref")
        from .patient_observations import verified_bytes

        matches = [
            (a, source)
            for a in job.case.acquisitions
            for source in a.source_files
            if source.uri == anatomy.get("uri")
            and source.sha256 == anatomy.get("sha256")
        ]
        if len(matches) != 1:
            raise ValueError(
                "Mechanics anatomy must be a unique hash-pinned artifact in this patient case"
            )
        acquisition, source = matches[0]
        if any(
            e.acquisition_id == acquisition.acquisition_id
            and e.role in {"validation", "excluded"}
            for e in job.case.evidence
        ):
            raise ValueError(
                "Mechanics anatomy cannot consume held-out or excluded evidence"
            )
        verified_bytes(source)
        if template["backend"] == "numpy-lumped-v1":
            template["settings"]["inline_series"] = True
    likelihood = []
    sampling = []
    for target in job.targets:
        evidence, source = source_for_target(job, target)
        if evidence.role == "calibration":
            if target.observation_kind == "scalar":
                read_scalar(source, target.unit)
                output_path, observation_path = target.model_output, "value"
            else:
                observation = read_observation(source, target.unit)
                if target.observation_kind == "curve" and observation.time_s is None:
                    raise ValueError("Curve source requires explicit time_s")
                if target.observation_kind == "field" and observation.labels is None:
                    raise ValueError("Field source requires explicit labels")
                spec = target.model_dump(mode="json")
                spec.update(time_s=observation.time_s, labels=observation.labels)
                # Only fit axes/correspondences enter inference, never withheld values/axes.
                sampling.append(spec)
                output_path, observation_path = (
                    "patient_sampled.t" + str(len(sampling) - 1),
                    "values",
                )
            likelihood.append(
                {
                    "term_id": target.evidence_id,
                    "observation_ref": {
                        "artifact_id": source.file_id,
                        "kind": "patient_scalar",
                        "uri": source.uri,
                        "sha256": source.sha256,
                    },
                    "model_output": output_path,
                    "discrepancy": "gaussian",
                    "noise_parameters": {"sigma": target.noise_sd},
                    "weight": 1.0,
                    "metadata": {"observation_path": observation_path},
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
            "sampling": sampling,
        },
    }
    inference_request = {
        "subject_id": job.case.patient_id,
        "model_service": "PatientNative" if job.native_capability else ("CardiMech" if job.domain == "mechanics" else "CardiFlow"),
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
        if target.observation_kind == "scalar":
            observed_values = [read_scalar(source, target.unit)]
            predicted_values = [value_at(prediction, target.model_output)]
            actual_unit = output_unit(prediction, target.model_output)
            if actual_unit is not None and actual_unit != target.unit:
                raise ValueError(
                    "Predicted output units differ from declared target unit"
                )
        else:
            observation = read_observation(source, target.unit)
            if target.observation_kind == "curve" and observation.time_s is None:
                raise ValueError("Curve holdout requires explicit time_s")
            if target.observation_kind == "field" and observation.labels is None:
                raise ValueError("Field holdout requires explicit labels")
            spec = target.model_dump(mode="json")
            spec.update(time_s=observation.time_s, labels=observation.labels)
            observed_values = observation.values
            predicted_values = sample_prediction(prediction, spec)
        if len(predicted_values) != len(observed_values) or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            for v in predicted_values
        ):
            raise ValueError(
                "Declared prediction must align with finite observation values"
            )
        errors = [float(p) - o for p, o in zip(predicted_values, observed_values)]
        residuals.append(
            {
                "evidence_id": target.evidence_id,
                "source_sha256": source.sha256,
                "observed": observed_values[0]
                if target.observation_kind == "scalar"
                else observed_values,
                "predicted": predicted_values[0]
                if target.observation_kind == "scalar"
                else predicted_values,
                "unit": target.unit,
                "observation_kind": target.observation_kind,
                "error": errors[0] if target.observation_kind == "scalar" else errors,
                "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
                "maximum_absolute_error": max(abs(e) for e in errors),
                "max_abs_error": target.max_abs_error,
                "passed": all(abs(e) <= target.max_abs_error for e in errors),
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
