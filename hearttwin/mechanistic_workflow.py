"""Explicit cross-physics HeartTwin workflow.

This module intentionally refuses implicit cross-service conversions. Every
downstream stage consumes the exact artifact reference emitted upstream and the
workflow verifies subject, anatomy, activation, mechanics, posterior, and state
lineage before proceeding.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .contracts import (
    AnatomyBundlePayload,
    EPResultPayload,
    EvaluationResultPayload,
    FlowResultPayload,
    InferenceResultPayload,
    MechanicsResultPayload,
    Provenance,
    ServiceResult,
    TherapyResultPayload,
    ValidationGateArtifact,
    WorkflowRun,
    WorkflowState,
)
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry
from .state import CardiacStateStore


class MechanisticWorkflowError(RuntimeError):
    pass


def _call(
    registry: ServiceRegistry,
    capability: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    parent_run_ids: list[str],
) -> ServiceResult:
    adapter = registry.capability(capability)
    if adapter is None:
        raise MechanisticWorkflowError(f"No service advertises {capability}")
    if not adapter.available():
        raise MechanisticWorkflowError(
            f"{adapter.spec.name} is unavailable for {capability}"
        )
    output = adapter.invoke(capability, payload)
    if not isinstance(output, dict):
        raise MechanisticWorkflowError(f"{capability} returned a non-object payload")
    returned_subject = output.get("subject_id", output.get("entity_id"))
    if returned_subject is not None and str(returned_subject) != entity_id:
        raise MechanisticWorkflowError(
            f"{capability} returned subject {returned_subject!r}, expected {entity_id!r}"
        )
    step_run_id = new_run_id(
        entity_id,
        {"capability": capability, "payload": payload, "output": output},
    )
    provenance = Provenance(
        source_service=adapter.spec.name,
        source_repository=adapter.spec.repository,
        run_id=step_run_id,
        parent_run_ids=list(parent_run_ids),
        content_sha256=sha256(output),
    )
    return ServiceResult(
        service=adapter.spec.name,
        capability=capability,
        status="ok",
        data=output,
        provenance=provenance,
    )


def _service_ref(artifact: dict[str, Any], bundle_fingerprint: str) -> dict[str, Any]:
    metadata = dict(artifact.get("metadata") or {})
    metadata["bundle_fingerprint"] = bundle_fingerprint
    return {
        "artifact_id": str(artifact["artifact_id"]),
        "kind": str(artifact["kind"]),
        "uri": str(artifact["uri"]),
        "sha256": artifact.get("sha256"),
        "coordinate_frame": artifact.get("frame_id"),
        "metadata": metadata,
    }


def _therapy_ref(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(artifact.get("metadata") or {})
    if artifact.get("coordinate_frame") is not None:
        metadata.setdefault("coordinate_frame", str(artifact["coordinate_frame"]))
    return {
        "artifact_id": str(artifact["artifact_id"]),
        "kind": str(artifact["kind"]),
        "uri": str(artifact["uri"]),
        "sha256": artifact.get("sha256"),
        "metadata": metadata,
    }


def _one_output(outputs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [item for item in outputs if item.get("kind") == kind]
    if len(matches) != 1:
        raise MechanisticWorkflowError(
            f"Expected exactly one {kind!r} artifact, found {len(matches)}"
        )
    return dict(matches[0])


def _one_anatomy_artifact(
    bundle: AnatomyBundlePayload,
    preferred: tuple[str, ...],
) -> dict[str, Any]:
    for kind in preferred:
        matches = [item for item in bundle.artifacts if item.get("kind") == kind]
        if len(matches) == 1:
            return dict(matches[0])
        if len(matches) > 1:
            raise MechanisticWorkflowError(
                f"Anatomy bundle has multiple {kind!r} artifacts; selection must be explicit"
            )
    raise MechanisticWorkflowError(
        "Anatomy bundle lacks required artifact kind(s): " + ", ".join(preferred)
    )


def _apply_flat_parameters(
    parameters: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    calibrated = deepcopy(parameters)
    for name, raw_value in values.items():
        parts = str(name).split(".")
        if len(parts) != 2 or parts[0] not in {"passive", "active"}:
            continue
        calibrated.setdefault(parts[0], {})[parts[1]] = float(raw_value)
    calibrated["source"] = "calibrated"
    return calibrated


def _apply_posterior_means(
    parameters: dict[str, Any],
    posterior: list[dict[str, Any]],
) -> dict[str, Any]:
    values = {
        str(item.get("parameter", "")): item["mean"]
        for item in posterior
        if item.get("parameter") and item.get("mean") is not None
    }
    return _apply_flat_parameters(parameters, values)


def _posterior_sample_path(artifact: dict[str, Any]) -> Path:
    parsed = urlparse(str(artifact["uri"]))
    if parsed.scheme not in {"", "file"}:
        raise MechanisticWorkflowError(
            "Mechanistic replay requires a local/file posterior-samples artifact"
        )
    raw = unquote(parsed.path) if parsed.scheme == "file" else str(artifact["uri"])
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise MechanisticWorkflowError(f"Posterior-samples artifact is missing: {path}")
    expected = artifact.get("sha256")
    if expected is not None:
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed.lower() != str(expected).lower():
            raise MechanisticWorkflowError(
                "Posterior-samples artifact failed SHA-256 verification"
            )
    return path


def _replay_parameters(
    parameters: dict[str, Any],
    inference: InferenceResultPayload,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose an actually evaluated posterior point for forward replay.

    Marginal posterior means are not guaranteed to form a jointly supported
    parameter vector. When weighted posterior samples are available, replay the
    accepted sample with the lowest recorded objective. MAP-only backends fall
    back to their point summaries.
    """
    artifact = inference.posterior_samples
    if artifact is None:
        return (
            _apply_posterior_means(parameters, inference.posterior),
            {"selection": "posterior_summary_fallback"},
        )

    path = _posterior_sample_path(artifact)
    payload = json.loads(path.read_text(encoding="utf-8"))
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise MechanisticWorkflowError(
            "Posterior-samples artifact does not contain accepted samples"
        )
    valid = [
        item
        for item in samples
        if isinstance(item, dict)
        and isinstance(item.get("parameters"), dict)
        and item.get("objective") is not None
    ]
    if not valid:
        raise MechanisticWorkflowError(
            "Posterior-samples artifact has no replayable parameter vectors"
        )
    selected = min(valid, key=lambda item: float(item["objective"]))
    return (
        _apply_flat_parameters(parameters, selected["parameters"]),
        {
            "selection": "accepted_posterior_sample",
            "objective": float(selected["objective"]),
            "weight": (
                None
                if selected.get("weight") is None
                else float(selected["weight"])
            ),
            "posterior_artifact_id": artifact.get("artifact_id"),
            "posterior_sha256": artifact.get("sha256"),
        },
    )


def _write_state_artifact(
    root: Path,
    state: Any,
    *,
    entity_id: str,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    path = (root / "cardiac-state.json").resolve()
    payload = state.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "artifact_id": f"{entity_id}-cardiac-state",
        "kind": "cardiac_state",
        "uri": path.as_uri(),
        "sha256": digest,
        "metadata": {"state_fingerprint": state.state_fingerprint},
    }


def run_mechanistic_twin_workflow(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    anatomy_bundle: dict[str, Any] | None = None,
    anatomy_request: dict[str, Any] | None = None,
    ep_backend: str,
    ep_parameters: dict[str, float],
    ep_parameter_units: dict[str, str] | None = None,
    ep_settings: dict[str, Any] | None = None,
    mechanics_backend: str,
    mechanics_parameters: dict[str, Any],
    mechanics_calibration: dict[str, Any],
    mechanics_settings: dict[str, Any] | None = None,
    flow_backend: str,
    flow_fluid: dict[str, Any],
    flow_boundary_conditions: list[dict[str, Any]],
    flow_domain: dict[str, Any] | None = None,
    flow_settings: dict[str, Any] | None = None,
    therapy_backend: str,
    therapy_plan: dict[str, Any],
    evaluation_reference_outcomes: dict[str, float],
    therapy_settings: dict[str, Any] | None = None,
    evaluation_primary_metric: str = "rmse",
    workdir: str | Path = "hearttwin-mechanistic-run",
) -> WorkflowRun:
    if (anatomy_bundle is None) == (anatomy_request is None):
        raise MechanisticWorkflowError(
            "Provide exactly one of anatomy_bundle or anatomy_request"
        )

    root = Path(workdir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id(entity_id, {"workflow": "mechanistic", "workdir": str(root)})
    store = CardiacStateStore.new(
        entity_id,
        biological_context={"workflow_run_id": run_id, "workflow": "mechanistic"},
    )
    steps: list[ServiceResult] = []

    input_prov: Provenance | None = None
    if anatomy_request is not None:
        request = deepcopy(anatomy_request)
        declared = request.get("subject_id")
        if declared is not None and str(declared) != entity_id:
            raise MechanisticWorkflowError("Anatomy request subject does not match entity_id")
        request["subject_id"] = entity_id
        acquisition = request.get("acquisition")
        if isinstance(acquisition, dict):
            acquisition = deepcopy(acquisition)
            acquisition["subject_id"] = entity_id
            request["acquisition"] = acquisition
        anatomy_result = _call(
            registry,
            "anatomy.build",
            entity_id,
            request,
            parent_run_ids=[run_id],
        )
        store.reduce_service_result(anatomy_result)
        steps.append(anatomy_result)
        bundle = AnatomyBundlePayload.model_validate(anatomy_result.data)
    else:
        bundle = AnatomyBundlePayload.model_validate(anatomy_bundle)
        if bundle.subject_id != entity_id:
            raise MechanisticWorkflowError("Anatomy bundle subject does not match entity_id")
        input_prov = Provenance(
            source_service="HeartTwinInput",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id=new_run_id(entity_id, {"anatomy_bundle": bundle.model_dump(mode="json")}),
            parent_run_ids=[run_id],
            content_sha256=sha256(bundle.model_dump(mode="json")),
        )

    validated_targets: set[str] = set()
    if bundle.bundle_fingerprint is None:
        canonical = _call(
            registry,
            "anatomy.validate",
            entity_id,
            {"target": "ep", "bundle": bundle.model_dump(mode="json")},
            parent_run_ids=[run_id],
        )
        canonical_fingerprint = canonical.data.get("bundle_fingerprint")
        if (
            not isinstance(canonical_fingerprint, str)
            or len(canonical_fingerprint) != 64
        ):
            raise MechanisticWorkflowError(
                "CardiAnatomy did not return a canonical bundle fingerprint"
            )
        bundle = bundle.model_copy(
            update={"bundle_fingerprint": canonical_fingerprint}
        )
        steps.append(canonical)
        validated_targets.add("ep")

    if input_prov is not None:
        store.record_anatomy(bundle, input_prov)

    bundle_fingerprint = bundle.bundle_fingerprint
    if bundle_fingerprint is None:
        raise MechanisticWorkflowError(
            "Anatomy bundle reached the mechanistic pipe without a fingerprint"
        )

    for target in ("ep", "mechanics", "flow"):
        if target in validated_targets:
            continue
        readiness = _call(
            registry,
            "anatomy.validate",
            entity_id,
            {"target": target, "bundle": bundle.model_dump(mode="json")},
            parent_run_ids=[run_id],
        )
        if readiness.data.get("bundle_fingerprint") != bundle_fingerprint:
            raise MechanisticWorkflowError(
                f"CardiAnatomy changed the bundle fingerprint during {target} readiness"
            )
        steps.append(readiness)

    ep_anatomy = _service_ref(
        _one_anatomy_artifact(bundle, ("surface_mesh", "volume_mesh")),
        bundle_fingerprint,
    )
    mechanics_anatomy = _service_ref(
        _one_anatomy_artifact(bundle, ("volume_mesh",)),
        bundle_fingerprint,
    )
    flow_anatomy = _service_ref(
        _one_anatomy_artifact(bundle, ("surface_mesh",)),
        bundle_fingerprint,
    )

    ep_cfg = dict(ep_settings or {})
    ep_cfg.setdefault("output_dir", str(root / "ep"))
    ep_result = _call(
        registry,
        "ep.simulate",
        entity_id,
        {
            "subject_id": entity_id,
            "anatomy_ref": ep_anatomy,
            "backend": ep_backend,
            "parameters": {
                "values": {str(k): float(v) for k, v in ep_parameters.items()},
                "units": dict(ep_parameter_units or {}),
                "source": "fixed",
            },
            "observations": [],
            "settings": ep_cfg,
        },
        parent_run_ids=[run_id],
    )
    ep_payload = EPResultPayload.model_validate(ep_result.data)
    if ep_payload.provenance.get("anatomy_bundle_fingerprint") != bundle_fingerprint:
        raise MechanisticWorkflowError("EP anatomy bundle fingerprint was not preserved")
    store.reduce_service_result(ep_result)
    steps.append(ep_result)
    activation_ref = _one_output(ep_payload.outputs, "activation_map")

    calibration_request = deepcopy(mechanics_calibration)
    calibration_request.update(
        {
            "subject_id": entity_id,
            "anatomy_ref": mechanics_anatomy,
            "activation_ref": activation_ref,
            "backend": mechanics_backend,
            "initial_parameters": deepcopy(mechanics_parameters),
        }
    )
    calibration_settings = dict(calibration_request.get("settings") or {})
    forward_settings = dict(mechanics_settings or {})
    for execution_only in ("output_dir", "inline_series", "posterior_replay_selection"):
        forward_settings.pop(execution_only, None)
    for key, value in forward_settings.items():
        if key in calibration_settings and calibration_settings[key] != value:
            raise MechanisticWorkflowError(
                f"Mechanics setting {key!r} differs between calibration and replay"
            )
        calibration_settings[key] = deepcopy(value)
    sampler = dict(calibration_settings.get("sampler_settings") or {})
    sampler.setdefault("output_dir", str(root / "inference"))
    calibration_settings["sampler_settings"] = sampler
    calibration_request["settings"] = calibration_settings

    prepared = _call(
        registry,
        "mechanics.prepare_calibration",
        entity_id,
        calibration_request,
        parent_run_ids=[ep_result.provenance.run_id],
    )
    steps.append(prepared)
    inference_request = deepcopy(prepared.data.get("cardiinfer_request"))
    if not isinstance(inference_request, dict):
        raise MechanisticWorkflowError(
            "CardiMech calibration did not provide a CardiInfer request"
        )
    inference_result = _call(
        registry,
        "infer.run",
        entity_id,
        inference_request,
        parent_run_ids=[prepared.provenance.run_id],
    )
    inference_payload = InferenceResultPayload.model_validate(inference_result.data)
    if inference_payload.model_service != "CardiMech":
        raise MechanisticWorkflowError("Inference result is not bound to CardiMech")
    store.reduce_service_result(inference_result)
    steps.append(inference_result)

    model_context = prepared.data.get("model_context")
    if not isinstance(model_context, dict):
        raise MechanisticWorkflowError(
            "CardiMech calibration did not provide model_context"
        )
    replay_template = model_context.get("cardimech_request")
    if not isinstance(replay_template, dict):
        raise MechanisticWorkflowError(
            "CardiMech calibration did not provide the authoritative replay template"
        )
    mechanics_request = deepcopy(replay_template)
    template_parameters = mechanics_request.get("parameters")
    if not isinstance(template_parameters, dict):
        raise MechanisticWorkflowError(
            "CardiMech replay template is missing parameters"
        )
    calibrated_parameters, replay_selection = _replay_parameters(
        template_parameters,
        inference_payload,
    )
    mechanics_request["subject_id"] = entity_id
    mechanics_request["anatomy_ref"] = mechanics_anatomy
    mechanics_request["activation_ref"] = activation_ref
    mechanics_request["backend"] = mechanics_backend
    mechanics_request["parameters"] = calibrated_parameters

    mech_cfg = dict(mechanics_request.get("settings") or {})
    requested_mech_cfg = dict(mechanics_settings or {})
    mech_cfg["output_dir"] = str(
        requested_mech_cfg.get("output_dir") or (root / "mechanics")
    )
    mech_cfg["inline_series"] = False
    mech_cfg["posterior_replay_selection"] = replay_selection
    mechanics_request["settings"] = mech_cfg

    mechanics_result = _call(
        registry,
        "mechanics.simulate",
        entity_id,
        mechanics_request,
        parent_run_ids=[inference_result.provenance.run_id, ep_result.provenance.run_id],
    )
    mechanics_payload = MechanicsResultPayload.model_validate(mechanics_result.data)
    if mechanics_payload.provenance.get("anatomy_bundle_fingerprint") != bundle_fingerprint:
        raise MechanisticWorkflowError("Mechanics anatomy bundle fingerprint was not preserved")
    if mechanics_payload.provenance.get("activation_artifact_id") != activation_ref["artifact_id"]:
        raise MechanisticWorkflowError("Mechanics did not consume the EP activation artifact")
    if activation_ref.get("sha256") and mechanics_payload.provenance.get("activation_sha256") != activation_ref.get("sha256"):
        raise MechanisticWorkflowError("Mechanics activation SHA-256 does not match EP output")
    store.reduce_service_result(mechanics_result)
    steps.append(mechanics_result)
    mechanics_ref = _one_output(mechanics_payload.outputs, "mechanics_timeseries")

    domain = dict(flow_domain or {})
    domain.setdefault("domain_id", "systemic")
    domain.setdefault("region", "systemic_circulation")
    domain["anatomy_ref"] = flow_anatomy
    flow_cfg = dict(flow_settings or {})
    if "inlet_flow" in flow_cfg:
        raise MechanisticWorkflowError(
            "Mechanistic workflow forbids a separate inlet_flow; CardiFlow must consume mechanics_ref"
        )
    flow_result = _call(
        registry,
        "flow.simulate",
        entity_id,
        {
            "subject_id": entity_id,
            "domain": domain,
            "backend": flow_backend,
            "fluid": flow_fluid,
            "boundary_conditions": flow_boundary_conditions,
            "mechanics_ref": mechanics_ref,
            "settings": flow_cfg,
        },
        parent_run_ids=[mechanics_result.provenance.run_id],
    )
    flow_payload = FlowResultPayload.model_validate(flow_result.data)
    if flow_payload.provenance.get("anatomy_bundle_fingerprint") != bundle_fingerprint:
        raise MechanisticWorkflowError("Flow anatomy bundle fingerprint was not preserved")
    if flow_payload.provenance.get("mechanics_artifact_id") != mechanics_ref["artifact_id"]:
        raise MechanisticWorkflowError("Flow did not consume the CardiMech artifact")
    if mechanics_ref.get("sha256") and flow_payload.provenance.get("mechanics_sha256") != mechanics_ref.get("sha256"):
        raise MechanisticWorkflowError("Flow mechanics SHA-256 does not match CardiMech output")
    if flow_payload.provenance.get("coupling_mode") != "mechanics_aortic_flow":
        raise MechanisticWorkflowError("Flow backend did not execute the mechanics-coupled path")
    store.reduce_service_result(flow_result)
    steps.append(flow_result)

    store.record_validation_gate(
        ValidationGateArtifact(
            gate_id=f"mechanistic-lineage-{run_id[:16]}",
            policy_id="hearttwin.mechanistic-lineage.v1",
            evidence_level="numerical_verification",
            passed=True,
            criteria=[
                {"name": "ep_anatomy_fingerprint", "passed": True},
                {"name": "mechanics_activation_hash", "passed": True},
                {"name": "flow_mechanics_hash", "passed": True},
            ],
            evidence_ids=[
                activation_ref["artifact_id"],
                mechanics_ref["artifact_id"],
            ],
        ),
        provenance=flow_result.provenance,
    )

    store.transition(
        "intervention",
        trigger="CardiTherapy:therapy.run",
        provenance=flow_result.provenance,
        details={"plan_id": str(therapy_plan.get("plan_id", ""))},
    )
    pretherapy_state = store.snapshot()
    state_ref = _write_state_artifact(
        root / "state",
        pretherapy_state,
        entity_id=entity_id,
    )
    posterior_ref = None
    if inference_payload.posterior_samples is not None:
        source = inference_payload.posterior_samples
        posterior_ref = {
            "artifact_id": str(source["artifact_id"]),
            "kind": str(source["kind"]),
            "uri": str(source["uri"]),
            "sha256": source.get("sha256"),
            "metadata": dict(source.get("metadata") or {}),
        }

    therapy_cfg = dict(therapy_settings or {})
    therapy_cfg.setdefault("ep_backend", ep_backend)
    therapy_cfg.setdefault("ep_parameters", dict(ep_parameters))
    therapy_cfg.setdefault("ep_parameter_units", dict(ep_parameter_units or {}))
    therapy_ep_settings = dict(therapy_cfg.get("ep_settings") or ep_cfg)
    therapy_ep_settings["output_dir"] = str(root / "therapy-ep")
    therapy_cfg["ep_settings"] = therapy_ep_settings

    therapy_result = _call(
        registry,
        "therapy.run",
        entity_id,
        {
            "subject_id": entity_id,
            "backend": therapy_backend,
            "twin_state_ref": state_ref,
            "posterior_ref": posterior_ref,
            "baseline_refs": [_therapy_ref(ep_anatomy)],
            "plan": therapy_plan,
            "settings": therapy_cfg,
        },
        parent_run_ids=[flow_result.provenance.run_id, inference_result.provenance.run_id],
    )
    therapy_payload = TherapyResultPayload.model_validate(therapy_result.data)
    if therapy_payload.provenance.get("twin_state_sha256") != state_ref["sha256"]:
        raise MechanisticWorkflowError("Therapy did not preserve the canonical state hash")
    if posterior_ref is not None and therapy_payload.provenance.get("posterior_sha256") != posterior_ref.get("sha256"):
        raise MechanisticWorkflowError("Therapy did not preserve the posterior hash")
    store.reduce_service_result(therapy_result)
    steps.append(therapy_result)
    store.transition(
        "post_intervention",
        trigger="CardiTherapy:therapy.run",
        provenance=therapy_result.provenance,
        details={"plan_id": therapy_payload.plan_id},
    )

    predictions: list[dict[str, Any]] = []
    for outcome in therapy_payload.outcomes:
        value = outcome.get("value")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MechanisticWorkflowError(
                "Mechanistic evaluation supports scalar numeric therapy outcomes only"
            )
        numeric = float(value)
        if not math.isfinite(numeric):
            raise MechanisticWorkflowError(
                "Mechanistic therapy outcome contains a non-finite value"
            )
        sample_id = f"{outcome['arm_id']}:{outcome['endpoint']}"
        predictions.append({"sample_id": sample_id, "y_pred": numeric})

    prediction_ids = [item["sample_id"] for item in predictions]
    if not prediction_ids or len(prediction_ids) != len(set(prediction_ids)):
        raise MechanisticWorkflowError(
            "Therapy outcomes must provide unique scalar arm:endpoint values for evaluation"
        )
    reference = {
        str(key): float(value)
        for key, value in evaluation_reference_outcomes.items()
    }
    if any(not math.isfinite(value) for value in reference.values()):
        raise MechanisticWorkflowError(
            "Mechanistic evaluation reference outcomes must be finite"
        )
    if set(reference) != set(prediction_ids):
        missing = sorted(set(prediction_ids) - set(reference))
        extra = sorted(set(reference) - set(prediction_ids))
        raise MechanisticWorkflowError(
            "Mechanistic evaluation references must exactly match therapy outcomes; "
            f"missing={missing}, extra={extra}"
        )

    evaluation_benchmark = {
        "benchmark_id": "hearttwin-mechanistic-therapy",
        "version": "1.0",
        "assignments": {sample_id: "test" for sample_id in prediction_ids},
        "metadata_sha256": sha256(
            {
                "reference_outcomes": reference,
                "therapy_plan_id": therapy_payload.plan_id,
            }
        ),
    }
    evaluation_result = _call(
        registry,
        "evaluation.run",
        entity_id,
        {
            "benchmark": evaluation_benchmark,
            "predictions": predictions,
            "reference_labels": reference,
            "model_id": f"{therapy_payload.backend}:{therapy_payload.plan_id}",
            "task_id": "mechanistic-therapy-outcomes",
            "task_type": "regression",
            "primary_metric": evaluation_primary_metric,
        },
        parent_run_ids=[therapy_result.provenance.run_id],
    )
    evaluation_payload = EvaluationResultPayload.model_validate(
        evaluation_result.data
    )
    if evaluation_payload.ground_truth_source != "benchmark_manifest":
        raise MechanisticWorkflowError(
            "Mechanistic evaluation did not use evaluator-controlled reference outcomes"
        )
    store.reduce_service_result(evaluation_result)
    steps.append(evaluation_result)
    store.record_validation_gate(
        ValidationGateArtifact(
            gate_id=f"mechanistic-e2e-{run_id[:16]}",
            policy_id="hearttwin.mechanistic-e2e.v1",
            evidence_level="numerical_verification",
            passed=True,
            criteria=[
                {"name": "therapy_state_hash", "passed": True},
                {
                    "name": "independent_regression_evaluation",
                    "passed": True,
                    "primary_metric": evaluation_payload.primary_metric,
                    "primary_value": evaluation_payload.primary_value,
                },
            ],
            evidence_ids=[
                state_ref["artifact_id"],
                evaluation_payload.evaluation_fingerprint or "",
            ],
        ),
        provenance=evaluation_result.provenance,
    )

    traced_state = store.snapshot()
    trace_result = _call(
        registry,
        "trace.record",
        entity_id,
        {
            "entity_id": entity_id,
            "hearttwin_run_id": run_id,
            "context": {"workflow": "mechanistic", "workflow_run_id": run_id},
            "observations": [],
            "workflow_state": {
                "entity_id": entity_id,
                "cardiac_state": traced_state.model_dump(mode="json"),
            },
            "canonical_state_fingerprint": traced_state.state_fingerprint,
            "results": [
                {
                    "service": item.service,
                    "capability": item.capability,
                    "status": item.status,
                    "data": item.data,
                }
                for item in steps
            ],
        },
        parent_run_ids=[evaluation_result.provenance.run_id],
    )
    store.reduce_service_result(trace_result)
    steps.append(trace_result)

    final_state = store.snapshot()
    return WorkflowRun(
        run_id=run_id,
        entity_id=entity_id,
        status="ok",
        state=WorkflowState(
            entity_id=entity_id,
            cardiac_state=final_state,
            anatomy=bundle,
            ep=ep_payload,
            inference=inference_payload,
            mechanics=mechanics_payload,
            flow=flow_payload,
            therapy=therapy_payload,
            evaluation=evaluation_payload,
            trace=trace_result.data,
        ),
        steps=steps,
    )
