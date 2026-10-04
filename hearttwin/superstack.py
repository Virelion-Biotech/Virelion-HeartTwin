"""Full-stack HeartTwin orchestration and continuity sealing.

The superstack deliberately composes the established multimodal and mechanistic
workflows instead of replacing their validated boundaries.  Cross-domain links
are conservative and explicit: measured timing can set timing, calibrated EP
parameters can replace matching EP parameters, and QC/control-plane services
gate downstream execution without inventing biological transformations.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    CardiacState,
    InferenceResultPayload,
    Observation,
    Provenance,
    ServiceResult,
    WorkflowRun,
    WorkflowState,
)
from .ep_calibration import run_ep_calibration
from .mechanistic_workflow import run_mechanistic_twin_workflow
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry
from .state import CardiacStateStore
from .workflow import _call, run_multimodal_workflow


class SuperstackWorkflowError(RuntimeError):
    """Raised when a cross-stack handoff is missing, ambiguous, or unsafe."""


_COLLECTION_FIELDS = (
    "anatomy_bundles",
    "benchmarks",
    "modality_analyses",
    "derived_values",
    "simulation_artifacts",
    "prediction_artifacts",
    "ep_artifacts",
    "mechanics_artifacts",
    "flow_artifacts",
    "therapy_artifacts",
    "posterior_artifacts",
    "evaluation_artifacts",
    "validation_gates",
    "challenges",
    "vex_observations",
    "bridge_publications",
)


def _item_key(item: Any) -> str:
    if hasattr(item, "model_dump"):
        return sha256(item.model_dump(mode="json"))
    return sha256(item)


def _extend_unique(target: list[Any], source: Iterable[Any]) -> None:
    existing = {_item_key(item) for item in target}
    for item in source:
        key = _item_key(item)
        if key not in existing:
            target.append(item.model_copy(deep=True) if hasattr(item, "model_copy") else deepcopy(item))
            existing.add(key)


def _merge_states(
    entity_id: str,
    observations: list[Observation],
    states: list[CardiacState],
) -> CardiacStateStore:
    if not states:
        raise SuperstackWorkflowError("No canonical states were supplied for superstack merge")
    if any(state.entity_id != entity_id for state in states):
        raise SuperstackWorkflowError("Cannot merge canonical states from different subjects")

    merged = states[0].model_copy(deep=True)
    merged.state_fingerprint = None
    merged.entity_id = entity_id
    merged.observations = [item.model_copy(deep=True) for item in observations]
    merged.state_phase = "evaluated"
    # Source workflows retain their own transition histories and fingerprints.
    # A merged superstack snapshot is a new execution view, so it starts a new
    # transition history while retaining the source fingerprints below.
    merged.transitions = []

    source_fingerprints = []
    for state in states:
        if state.state_fingerprint:
            source_fingerprints.append(state.state_fingerprint)
        if merged.atlas_context is None and state.atlas_context is not None:
            merged.atlas_context = state.atlas_context.model_copy(deep=True)
        for field in _COLLECTION_FIELDS:
            _extend_unique(getattr(merged, field), getattr(state, field))
        _extend_unique(merged.trace_records, state.trace_records)
        _extend_unique(merged.simulations, state.simulations)
        _extend_unique(merged.predictions, state.predictions)
        merged.inferred_state.update(deepcopy(state.inferred_state))
        merged.validation.update(deepcopy(state.validation))

    provenance_by_id = {}
    for state in states:
        for provenance in state.provenance:
            provenance_by_id.setdefault(provenance.run_id, provenance.model_copy(deep=True))
    for observation in observations:
        provenance_by_id.setdefault(
            observation.provenance.run_id,
            observation.provenance.model_copy(deep=True),
        )
    merged.provenance = list(provenance_by_id.values())
    merged.biological_context = {
        **deepcopy(merged.biological_context),
        "superstack": {
            "source_state_fingerprints": sorted(set(source_fingerprints)),
            "merge_policy": "typed-artifact-union-v1",
        },
    }
    return CardiacStateStore(merged)


def _analysis_by_modality(run: WorkflowRun) -> dict[str, Any]:
    return {item.modality: item for item in run.state.modality_analyses}


def _myotrace_cycle_length(run: WorkflowRun) -> tuple[float, ServiceResult]:
    analyses = _analysis_by_modality(run)
    analysis = analyses.get("mechanical")
    if analysis is None:
        raise SuperstackWorkflowError(
            "Superstack requires a MyoTrace mechanical observation to couple measured timing into mechanics"
        )
    summary = analysis.output.get("summary")
    if not isinstance(summary, dict):
        raise SuperstackWorkflowError("MyoTrace result is missing summary")
    raw_frequency = summary.get("dominant_frequency_hz")
    try:
        frequency = float(raw_frequency)
    except (TypeError, ValueError) as exc:
        raise SuperstackWorkflowError(
            "MyoTrace summary lacks a numeric dominant_frequency_hz"
        ) from exc
    if not isfinite(frequency) or frequency <= 0.0:
        raise SuperstackWorkflowError("MyoTrace dominant_frequency_hz must be positive and finite")
    step = next(
        (item for item in run.steps if item.capability == "mechanical.analyze"),
        None,
    )
    if step is None:
        raise SuperstackWorkflowError("MyoTrace analysis has no workflow provenance step")
    return 1.0 / frequency, step


def _require_imaging_gate(run: WorkflowRun) -> ServiceResult:
    analysis = _analysis_by_modality(run).get("imaging")
    if analysis is None:
        raise SuperstackWorkflowError("Superstack requires an OptiCell imaging-QC observation")
    if int(analysis.output.get("n_rows") or 0) < 1:
        raise SuperstackWorkflowError("OptiCell imaging QC produced no analyzable rows")
    step = next((item for item in run.steps if item.capability == "imaging.qc"), None)
    if step is None:
        raise SuperstackWorkflowError("OptiCell analysis has no workflow provenance step")
    return step


def _require_safety_gate(
    run: WorkflowRun,
    allowed_risk_classes: set[str] | None,
) -> tuple[list[str], ServiceResult]:
    analysis = _analysis_by_modality(run).get("safety")
    if analysis is None:
        raise SuperstackWorkflowError("Superstack requires a CardioScore safety observation")
    summary = analysis.output.get("summary")
    if not isinstance(summary, list) or not summary:
        raise SuperstackWorkflowError("CardioScore produced no safety summary")
    risk_classes = sorted(
        {
            str(row["risk_class"])
            for row in summary
            if isinstance(row, dict) and row.get("risk_class") is not None
        }
    )
    if not risk_classes:
        raise SuperstackWorkflowError("CardioScore summary contains no risk_class")
    if allowed_risk_classes is not None:
        rejected = sorted(set(risk_classes) - set(allowed_risk_classes))
        if rejected:
            raise SuperstackWorkflowError(
                "CardioScore therapy gate rejected risk classes: " + ", ".join(rejected)
            )
    step = next((item for item in run.steps if item.capability == "safety.score"), None)
    if step is None:
        raise SuperstackWorkflowError("CardioScore analysis has no workflow provenance step")
    return risk_classes, step


def _control_result(
    *,
    entity_id: str,
    service: str,
    repository: str,
    capability: str,
    data: dict[str, Any],
    parent_run_ids: list[str] | None = None,
) -> ServiceResult:
    return ServiceResult(
        service=service,
        capability=capability,
        status="ok",
        data=data,
        provenance=Provenance(
            source_service=service,
            source_repository=repository,
            run_id=new_run_id(entity_id, {"capability": capability, "data": data}),
            parent_run_ids=list(parent_run_ids or []),
            content_sha256=sha256(data),
        ),
    )


def _posterior_parameter_overrides(
    result: dict[str, Any],
    parameter_map: dict[str, str],
) -> dict[str, float]:
    if not parameter_map:
        raise SuperstackWorkflowError(
            "EP calibration requires a non-empty parameter_map so the measured result actually affects CardiEP"
        )
    summaries = {
        str(item.get("parameter")): item
        for item in result.get("posterior", [])
        if isinstance(item, dict) and item.get("parameter")
    }
    overrides: dict[str, float] = {}
    for source, target in parameter_map.items():
        item = summaries.get(str(source))
        if item is None:
            raise SuperstackWorkflowError(
                f"EP calibration posterior is missing mapped parameter {source!r}"
            )
        raw = item.get("median")
        if raw is None:
            raw = item.get("mean")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise SuperstackWorkflowError(
                f"EP calibration parameter {source!r} has no finite median/mean"
            ) from exc
        if not isfinite(value):
            raise SuperstackWorkflowError(
                f"EP calibration parameter {source!r} is non-finite"
            )
        overrides[str(target)] = value
    return overrides


def run_superstack_workflow(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    observations: list[Observation],
    multimodal: dict[str, Any],
    mechanistic: dict[str, Any],
    cardistudio: dict[str, Any],
    dccp: dict[str, Any],
    ep_calibration: dict[str, Any],
    allowed_safety_risk_classes: set[str] | None = None,
) -> WorkflowRun:
    """Run one subject through every HeartTwin component and seal one final state.

    The function intentionally requires explicit configuration for cross-domain
    transformations.  It will fail rather than invent a mapping between unlike
    biological quantities.
    """
    # CardiStudio is a control-plane gate: the experiment design must explicitly
    # contain every benchmark label before the benchmark is frozen.
    factors = deepcopy(cardistudio.get("factors") or {})
    if not isinstance(factors, dict) or not factors:
        raise SuperstackWorkflowError("cardistudio.factors must be a non-empty mapping")
    studio_result = _call(
        registry,
        "design.generate",
        entity_id,
        {
            "factors": factors,
            "replicates": int(cardistudio.get("replicates", 1)),
            "blocks": int(cardistudio.get("blocks", 1)),
            "seed": int(cardistudio.get("seed", 42)),
        },
    )
    factor_name = str(cardistudio.get("benchmark_factor", "condition"))
    design_levels = {
        str(row[factor_name])
        for row in studio_result.data.get("design", [])
        if isinstance(row, dict) and factor_name in row
    }
    benchmark_labels = {
        str(row["label"])
        for row in multimodal.get("benchmark_samples", [])
        if isinstance(row, dict) and row.get("label") is not None
    }
    if not benchmark_labels or not benchmark_labels <= design_levels:
        raise SuperstackWorkflowError(
            "CardiStudio design does not cover every benchmark label: "
            f"labels={sorted(benchmark_labels)}, design={sorted(design_levels)}"
        )

    # DCCP host mapping drives the challenge severity through one caller-selected
    # axis.  No cross-axis averaging or fitted biological calibration is invented.
    module_scores = deepcopy(dccp.get("module_scores") or {})
    challenge_axis = str(dccp.get("challenge_axis") or "")
    if not module_scores or not challenge_axis:
        raise SuperstackWorkflowError(
            "dccp.module_scores and dccp.challenge_axis are required"
        )
    dccp_result = _call(
        registry,
        "host.map",
        entity_id,
        {"module_scores": module_scores},
    )
    axes = dccp_result.data.get("axes")
    continuous = axes.get("continuous") if isinstance(axes, dict) else None
    if not isinstance(continuous, dict) or challenge_axis not in continuous:
        raise SuperstackWorkflowError(
            f"DCCP host mapping did not produce challenge axis {challenge_axis!r}"
        )
    challenge_severity = float(continuous[challenge_axis])
    if not isfinite(challenge_severity) or not 0.0 <= challenge_severity <= 1.0:
        raise SuperstackWorkflowError("DCCP challenge severity must be within [0, 1]")

    multimodal_args = deepcopy(multimodal)
    multimodal_args["challenge_severity"] = challenge_severity
    multimodal_run = run_multimodal_workflow(
        registry,
        entity_id=entity_id,
        observations=observations,
        **multimodal_args,
    )
    if multimodal_run.state.cardiac_state is None:
        raise SuperstackWorkflowError("Multimodal workflow did not produce canonical state")

    cycle_length_s, myotrace_step = _myotrace_cycle_length(multimodal_run)
    imaging_step = _require_imaging_gate(multimodal_run)
    risk_classes, safety_step = _require_safety_gate(
        multimodal_run,
        allowed_safety_risk_classes,
    )

    # ElectroTrace -> CardiInfer -> CardiEP is the calibrated electrical handoff.
    calibration_observation_id = str(ep_calibration.get("observation_id") or "")
    calibration_observation = next(
        (
            item
            for item in observations
            if item.observation_id == calibration_observation_id
        ),
        None,
    )
    if calibration_observation is None:
        raise SuperstackWorkflowError(
            f"EP calibration observation {calibration_observation_id!r} was not found"
        )
    calibration = run_ep_calibration(
        registry,
        entity_id=entity_id,
        electrical_observation=calibration_observation,
        anatomy_ref=deepcopy(ep_calibration["anatomy_ref"]),
        priors=deepcopy(ep_calibration["priors"]),
        inference_backend=str(ep_calibration["inference_backend"]),
        ep_backend=str(ep_calibration["ep_backend"]),
        ep_settings=deepcopy(ep_calibration.get("ep_settings") or {}),
        fixed_parameters=deepcopy(ep_calibration.get("fixed_parameters") or {}),
        sampler_settings=deepcopy(ep_calibration.get("sampler_settings") or {}),
        seed=ep_calibration.get("seed"),
    )
    ep_overrides = _posterior_parameter_overrides(
        calibration["inference_result"],
        {
            str(key): str(value)
            for key, value in dict(ep_calibration.get("parameter_map") or {}).items()
        },
    )
    electrotrace_step = _control_result(
        entity_id=entity_id,
        service="ElectroTrace",
        repository="Virelion-Biotech/Virelion-ElectroTrace",
        capability="electrical.prepare_calibration",
        data=calibration["measurement_handoff"],
        parent_run_ids=[multimodal_run.run_id],
    )
    ep_infer_step = _control_result(
        entity_id=entity_id,
        service="CardiInfer",
        repository="Virelion-Biotech/Virelion-CardiInfer",
        capability="infer.run",
        data=calibration["inference_result"],
        parent_run_ids=[electrotrace_step.provenance.run_id],
    )

    mechanistic_args = deepcopy(mechanistic)
    ep_parameters = {
        str(key): float(value)
        for key, value in dict(mechanistic_args.get("ep_parameters") or {}).items()
    }
    ep_parameters.update(ep_overrides)
    mechanistic_args["ep_parameters"] = ep_parameters

    mechanics_settings = deepcopy(mechanistic_args.get("mechanics_settings") or {})
    declared_cycle = mechanics_settings.get("cycle_length_s")
    if declared_cycle is not None and abs(float(declared_cycle) - cycle_length_s) > 1e-9:
        raise SuperstackWorkflowError(
            "Mechanics cycle_length_s conflicts with MyoTrace-derived beat period"
        )
    mechanics_settings["cycle_length_s"] = cycle_length_s
    mechanistic_args["mechanics_settings"] = mechanics_settings

    # Preserve specialist evidence in the mechanics likelihood and therapy plan.
    calibration_cfg = deepcopy(mechanistic_args.get("mechanics_calibration") or {})
    upstream_hashes = {
        "myotrace_sha256": myotrace_step.provenance.content_sha256,
        "opticell_sha256": imaging_step.provenance.content_sha256,
        "cardioscore_sha256": safety_step.provenance.content_sha256,
        "ep_calibration_sha256": calibration["result_sha256"],
    }
    for observation in calibration_cfg.get("observations", []):
        if isinstance(observation, dict):
            metadata = dict(observation.get("metadata") or {})
            metadata["hearttwin_upstream_evidence"] = upstream_hashes
            observation["metadata"] = metadata
    mechanistic_args["mechanics_calibration"] = calibration_cfg

    therapy_plan = deepcopy(mechanistic_args.get("therapy_plan") or {})
    therapy_metadata = dict(therapy_plan.get("metadata") or {})
    therapy_metadata["hearttwin_safety_evidence"] = {
        "cardioscore_sha256": safety_step.provenance.content_sha256,
        "risk_classes": risk_classes,
    }
    therapy_plan["metadata"] = therapy_metadata
    mechanistic_args["therapy_plan"] = therapy_plan

    mechanistic_run = run_mechanistic_twin_workflow(
        registry,
        entity_id=entity_id,
        **mechanistic_args,
    )
    if mechanistic_run.state.cardiac_state is None:
        raise SuperstackWorkflowError("Mechanistic workflow did not produce canonical state")

    store = _merge_states(
        entity_id,
        observations,
        [
            multimodal_run.state.cardiac_state,
            mechanistic_run.state.cardiac_state,
        ],
    )
    store.add_derived_value(
        domain="other",
        variable="superstack.cardistudio_n_runs",
        value=int(studio_result.data.get("n_runs", 0)),
        status="inferred",
        method="CardiStudio",
        provenance=studio_result.provenance,
    )
    store.add_derived_value(
        domain="molecular",
        variable=f"superstack.dccp.{challenge_axis}",
        value=challenge_severity,
        status="inferred",
        method="DCCP",
        provenance=dccp_result.provenance,
    )
    store.add_derived_value(
        domain="mechanical",
        variable="superstack.myotrace_cycle_length_s",
        value=cycle_length_s,
        unit="s",
        status="inferred",
        method="MyoTrace",
        provenance=myotrace_step.provenance,
    )
    store.add_derived_value(
        domain="imaging",
        variable="superstack.opticell_gate",
        value="passed",
        status="inferred",
        method="OptiCell",
        provenance=imaging_step.provenance,
    )
    store.add_derived_value(
        domain="safety",
        variable="superstack.cardioscore_gate",
        value={"status": "passed", "risk_classes": risk_classes},
        status="inferred",
        method="CardioScore",
        provenance=safety_step.provenance,
    )
    for target, value in sorted(ep_overrides.items()):
        store.add_derived_value(
            domain="electrical",
            variable=f"superstack.ep_calibrated.{target}",
            value=value,
            status="inferred",
            method="ElectroTrace→CardiInfer",
            provenance=ep_infer_step.provenance,
        )
    store.record_inference(
        InferenceResultPayload.model_validate(calibration["inference_result"]),
        ep_infer_step.provenance,
    )

    pre_trace = store.snapshot()
    run_id = new_run_id(
        entity_id,
        {
            "multimodal_run_id": multimodal_run.run_id,
            "mechanistic_run_id": mechanistic_run.run_id,
            "studio": studio_result.data,
            "dccp": dccp_result.data,
            "ep_calibration": calibration["problem_sha256"],
            "pre_trace_state": pre_trace.state_fingerprint,
        },
    )

    combined_steps = [
        studio_result,
        dccp_result,
        *multimodal_run.steps,
        electrotrace_step,
        ep_infer_step,
        *mechanistic_run.steps,
    ]
    trace_result = _call(
        registry,
        "trace.record",
        entity_id,
        {
            "hearttwin_run_id": run_id,
            "context": {
                "workflow": "superstack",
                "workflow_run_id": run_id,
                "source_run_ids": [
                    multimodal_run.run_id,
                    mechanistic_run.run_id,
                ],
            },
            "observations": [item.model_dump(mode="json") for item in observations],
            "workflow_state": {
                "entity_id": entity_id,
                "cardiac_state": pre_trace.model_dump(mode="json"),
            },
            "canonical_state_fingerprint": pre_trace.state_fingerprint,
            "results": [
                item.model_dump(mode="json")
                for item in combined_steps
            ],
        },
    )
    store.record_trace(trace_result.data, trace_result.provenance)
    final_state = store.snapshot()
    service_contributions = {
        item.service for item in combined_steps + [trace_result]
    }
    if multimodal_run.state.vex is not None:
        service_contributions.add("CardiVex")
    final_state.biological_context.setdefault("superstack", {})[
        "service_contributions"
    ] = sorted(service_contributions)
    # The context update changes the fingerprint; seal it as the returned state.
    final_state = CardiacStateStore(
        final_state.model_copy(update={"state_fingerprint": None})
    ).snapshot()

    state = WorkflowState(
        entity_id=entity_id,
        observations=observations,
        alignment=multimodal_run.state.alignment,
        cardiac_state=final_state,
        modality_analyses=list(multimodal_run.state.modality_analyses),
        atlas=multimodal_run.state.atlas,
        anatomy=mechanistic_run.state.anatomy,
        benchmark=multimodal_run.state.benchmark,
        learning=multimodal_run.state.learning,
        ep=mechanistic_run.state.ep,
        inference=mechanistic_run.state.inference,
        mechanics=mechanistic_run.state.mechanics,
        flow=mechanistic_run.state.flow,
        therapy=mechanistic_run.state.therapy,
        simulation=multimodal_run.state.simulation,
        agent=multimodal_run.state.agent,
        vex=multimodal_run.state.vex,
        evaluation=mechanistic_run.state.evaluation,
        bridge=multimodal_run.state.bridge,
        trace=trace_result.data,
        provenance=list(final_state.provenance),
    )
    return WorkflowRun(
        run_id=run_id,
        entity_id=entity_id,
        status="ok",
        state=state,
        steps=combined_steps + [trace_result],
    )
