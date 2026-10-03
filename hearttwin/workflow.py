"""Explicit typed multimodal HeartTwin workflow.

This module defines a dependency graph where service outputs are validated into
versioned contracts and simultaneously reduced into one canonical CardiacState.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from .cardibridge_adapter import _local_router
from .contracts import (
    AgentChallengePayload,
    AtlasContextPayload,
    BenchmarkResolutionPayload,
    BridgePublicationPayload,
    EvaluationResultPayload,
    LearningResultPayload,
    ModalityAnalysisPayload,
    Observation,
    Provenance,
    ServiceResult,
    SimulationResultPayload,
    VexObservationPayload,
    WorkflowRun,
    WorkflowState,
)
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry
from .state import CardiacStateStore
from .state_crosswalk import CrosswalkError, cardisim_summary_to_cardivex


class WorkflowError(RuntimeError):
    """Raised when a typed workflow stage cannot produce a valid output."""


def _call(registry: ServiceRegistry, capability: str, entity_id: str, payload: dict[str, Any]) -> ServiceResult:
    adapter = registry.capability(capability)
    if adapter is None:
        raise WorkflowError(f"Required HeartTwin capability is not registered: {capability}")
    try:
        data = adapter.invoke(capability, {"entity_id": entity_id, **payload})
    except Exception as exc:  # noqa: BLE001 - normalized at workflow boundary
        raise WorkflowError(f"{capability} failed: {exc}") from exc
    return ServiceResult(
        service=adapter.spec.name,
        capability=capability,
        status="ok",
        data=data,
        provenance=Provenance(
            source_service=adapter.spec.name,
            source_repository=adapter.spec.repository,
            run_id=new_run_id(entity_id, {"capability": capability, "data": data}),
            content_sha256=sha256(data),
        ),
    )



def _validate_binary_test_partition(
    assignments: dict[str, str],
    reference_labels: dict[str, int],
) -> list[str]:
    test_ids = [
        sample_id
        for sample_id, partition in assignments.items()
        if partition == "test"
    ]
    if not test_ids:
        raise WorkflowError(
            "Benchmark test partition is empty; predeclare benchmark_test_values "
            "or use a split policy that yields held-out samples"
        )
    test_classes = {reference_labels[sample_id] for sample_id in test_ids}
    if len(test_classes) < 2:
        raise WorkflowError(
            "Binary evaluation test partition must contain both reference classes; "
            "balanced accuracy and ROC AUC are undefined for a one-class holdout. "
            "Predeclare class-valid benchmark_test_values before fitting."
        )
    return test_ids

def _as_atlas(data: dict[str, Any]) -> AtlasContextPayload:
    try:
        return AtlasContextPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"atlas.context returned an invalid typed payload: {exc}") from exc


def _as_benchmark(data: dict[str, Any]) -> BenchmarkResolutionPayload:
    try:
        return BenchmarkResolutionPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"CardiBench returned an invalid typed payload: {exc}") from exc


def _as_learning(data: dict[str, Any]) -> LearningResultPayload:
    try:
        return LearningResultPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"CardiLearn returned an invalid typed payload: {exc}") from exc


def _as_simulation(data: dict[str, Any]) -> SimulationResultPayload:
    try:
        return SimulationResultPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"CardiSim returned an invalid typed payload: {exc}") from exc


def _as_evaluation(data: dict[str, Any]) -> EvaluationResultPayload:
    try:
        return EvaluationResultPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"CardiEval returned an invalid typed payload: {exc}") from exc


def _as_agent(data: dict[str, Any], entity_id: str) -> AgentChallengePayload:
    try:
        return AgentChallengePayload.model_validate({**data, "entity_id": entity_id})
    except Exception as exc:
        raise WorkflowError(f"CardiAgent returned an invalid typed payload: {exc}") from exc


def _as_vex(data: dict[str, Any]) -> VexObservationPayload:
    try:
        if not data.get("scenario_id") or not data.get("primary"):
            raise ValueError("Missing CardiVex scenario or primary observation")
        return VexObservationPayload.model_validate(data)
    except Exception as exc:
        raise WorkflowError(f"CardiVex returned an invalid typed payload: {exc}") from exc


def _run_specialist_modalities(
    registry: ServiceRegistry,
    entity_id: str,
    observations: list[Observation],
) -> list[tuple[ServiceResult, ModalityAnalysisPayload]]:
    """Consume file-backed modality observations with their native HeartTwin adapters."""
    capability_by_modality = {
        "electrical": "electrical.analyze",
        "mechanical": "mechanical.analyze",
        "imaging": "imaging.qc",
        "safety": "safety.score",
    }
    outputs: list[tuple[ServiceResult, ModalityAnalysisPayload]] = []
    for observation in observations:
        capability = capability_by_modality.get(observation.modality)
        input_path = observation.values.get("input_path")
        if capability is None or not input_path:
            continue
        adapter = registry.capability(capability)
        if adapter is None:
            raise WorkflowError(
                f"Observation {observation.observation_id} requires {capability}, but it is not registered"
            )
        if not adapter.available():
            raise WorkflowError(
                f"Observation {observation.observation_id} requires {capability}, but its service is unavailable"
            )
        result = _call(
            registry,
            capability,
            entity_id,
            {"observations": [observation.model_dump(mode="json")]},
        )
        payload = ModalityAnalysisPayload(
            observation_id=observation.observation_id,
            modality=observation.modality,
            capability=capability,
            service=adapter.spec.name,
            input_path=str(input_path),
            output=result.data,
            content_sha256=result.provenance.content_sha256 if result.provenance else sha256(result.data),
        )
        outputs.append((result, payload))
    return outputs


def _scenario_from_workflow(entity_id: str, simulation: SimulationResultPayload) -> dict[str, Any]:
    """Translate only direct identity/sign-inversion phenotypes into CardiVex."""
    try:
        initial_values, final_values, _ = cardisim_summary_to_cardivex(simulation.summary)
    except CrosswalkError as exc:
        raise WorkflowError(f"CardiSim/CardiVex state crosswalk failed: {exc}") from exc
    initial = {
        name: {"value": value, "evidence_status": "extrapolated"}
        for name, value in initial_values.items()
    }
    final = {
        name: {"value": value, "evidence_status": "extrapolated"}
        for name, value in final_values.items()
    }
    duration = float(simulation.summary["duration"])
    return {
        "scenario_id": f"CVX-HT-{sha256({'entity': entity_id, 'simulation': simulation.model_dump(mode='json')})[:12].upper()}",
        "version": "1.0",
        "name": "HeartTwin simulation-derived phenotype challenge",
        "target_model": "HeartTwin",
        "evidence_tier": "extrapolated",
        "confidence": "exploratory",
        "phenotype_domains": final,
        "temporal_profile": [
            {"state": "baseline", "relative_time": 0.0, "duration": 0.0, "domains": initial},
            {"state": "simulated", "relative_time": duration, "duration": duration, "domains": final},
        ],
        "description": "Uncalibrated computational proxies; not empirical patient states. Uncertainty is not estimated.",
        "severity_profile": {key: item["value"] for key, item in final.items()},
        "ood_status": "validation",
        "provenance_sources": ["Virelion-CardiSim", "Virelion-HeartTwin"],
        "provenance_transformations": [
            "HeartTwin state_crosswalk 0.1.0: direct identity mappings and complements only; no fitted biological calibration."
        ],
    }

def _register_local_vex_handler(registry: ServiceRegistry, entity_id: str, scenario: dict[str, Any]) -> None:
    """Route a real ``agent.challenge`` BridgeEnvelope into CardiVex in-process."""
    router, _ = _local_router()

    def handler(envelope: Any) -> dict[str, Any]:
        vex = registry.capability("vex.observe")
        if vex is None:
            raise WorkflowError("CardiVex is required as the CardiBridge consumer")
        message_scenario: dict[str, Any] | None = None
        raw_payload = getattr(envelope, "payload", None)
        if isinstance(raw_payload, dict):
            population = raw_payload.get("population")
            if isinstance(population, list) and population and isinstance(population[0], dict):
                candidate = population[0].get("scenario")
                if isinstance(candidate, dict):
                    message_scenario = candidate
        selected_scenario = message_scenario or scenario
        return vex.invoke(
            "vex.observe",
            {"entity_id": entity_id, "scenario": selected_scenario, "bridge_message_id": envelope.message_id},
        )

    router.register("agent.challenge", "CardiVex", handler)


def run_multimodal_workflow(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    observations: list[Observation],
    benchmark_samples: list[dict[str, Any]],
    learning_data: list[dict[str, Any]],
    feature_columns: list[str],
    reference_labels: dict[str, int],
    benchmark_test_values: list[str] | None = None,
    benchmark_validation_values: list[str] | None = None,
    atlas_record_ids: list[str] | None = None,
    atlas_records: list[dict[str, Any]] | None = None,
    simulation: dict[str, Any] | None = None,
    seed: int = 42,
) -> WorkflowRun:
    """Run a complete local multimodal HeartTwin workflow."""
    run_id = new_run_id(
        entity_id,
        {
            "observations": [item.model_dump(mode="json") for item in observations],
            "benchmark_samples": benchmark_samples,
            "learning_data": learning_data,
            "feature_columns": feature_columns,
            "reference_labels": reference_labels,
            "benchmark_test_values": sorted(benchmark_test_values or []),
            "benchmark_validation_values": sorted(
                benchmark_validation_values or []
            ),
            "simulation": simulation,
            "atlas_record_ids": atlas_record_ids,
            "atlas_records": atlas_records,
            "seed": seed,
        },
    )
    store = CardiacStateStore.new(
        entity_id,
        observations=observations,
        biological_context={"workflow_run_id": run_id},
    )
    state = WorkflowState(
        entity_id=entity_id,
        observations=observations,
        cardiac_state=store.state,
    )
    steps: list[ServiceResult] = []

    specialist_results = _run_specialist_modalities(registry, entity_id, observations)
    for result, typed in specialist_results:
        steps.append(result)
        state.modality_analyses.append(typed)
        store.record_modality(typed, result.provenance)

    atlas_result = _call(
        registry,
        "atlas.context",
        entity_id,
        {
            "context_id": f"{entity_id}-context",
            "record_ids": atlas_record_ids or [],
            "records": atlas_records or [],
        },
    )
    state.atlas = _as_atlas(atlas_result.data)
    store.record_atlas(state.atlas, atlas_result.provenance)
    steps.append(atlas_result)

    # Freeze the benchmark before fitting. The learner cannot choose evaluation IDs.
    benchmark_result = _call(
        registry, "benchmark.resolve", entity_id,
        {
            "benchmark_id": "hearttwin-multimodal",
            "version": "1.0",
            "policy": "subject_heldout",
            "seed": seed,
            "samples": benchmark_samples,
            "test_values": list(benchmark_test_values or []),
            "validation_values": list(benchmark_validation_values or []),
        },
    )
    state.benchmark = _as_benchmark(benchmark_result.data)
    assignments = state.benchmark.assignments
    if set(reference_labels) != set(assignments) or any(type(v) is not int or v not in (0, 1) for v in reference_labels.values()):
        raise WorkflowError("reference_labels must provide explicit 0/1 labels for exactly the benchmark samples")
    _validate_binary_test_partition(assignments, reference_labels)

    rows_by_id = {str(row["sample_id"]): row for row in learning_data}
    if len(rows_by_id) != len(learning_data) or set(rows_by_id) != set(assignments):
        raise WorkflowError("Learning data must contain exactly the unique benchmark sample IDs")
    for sample in state.benchmark.samples:
        row = rows_by_id[sample.sample_id]
        if str(row.get("group_id")) != sample.group_id or str(row.get("study_id")) != sample.study_id:
            raise WorkflowError("Learning and benchmark biological identifiers disagree")
        if row.get("target") != reference_labels[sample.sample_id]:
            raise WorkflowError("Learning targets disagree with benchmark reference labels")
    store.record_benchmark(state.benchmark, benchmark_result.provenance)
    steps.append(benchmark_result)
    learning_result = _call(
        registry, "learn.infer", entity_id,
        {"data": learning_data, "target_column": "target", "group_column": "group_id",
         "feature_columns": feature_columns, "split_assignments": assignments,
         "model": "logistic_regression", "task": "classification", "seed": seed},
    )
    state.learning = _as_learning(learning_result.data)
    store.record_learning(state.learning, learning_result.provenance)
    steps.append(learning_result)

    sim_payload = dict(simulation or {})
    if not sim_payload.get("preset"):
        raise WorkflowError("An explicit simulation preset is required; classifier scores do not identify a mechanistic disease model")
    sim_payload.setdefault("n_cells", 32)
    sim_payload.setdefault("duration", 3.0)
    sim_payload.setdefault("dt", 0.25)
    sim_payload.setdefault("seed", seed)
    simulation_result = _call(registry, "simulation.run", entity_id, sim_payload)
    state.simulation = _as_simulation(simulation_result.data)
    store.record_simulation(state.simulation, simulation_result.provenance)
    store.transition(
        "simulated",
        trigger="CardiSim:simulation.run",
        provenance=simulation_result.provenance,
        details={"backend": state.simulation.backend},
    )
    health = float(state.simulation.summary.get("cardiac_health_score", 0.5))
    store.add_derived_value(
        domain="simulation",
        variable="cardiac_health_score",
        value=health,
        status="simulated",
        confidence=None,
        method="CardiSim",
        provenance=simulation_result.provenance,
    )
    steps.append(simulation_result)

    agent_result = _call(
        registry,
        "agent.challenge",
        entity_id,
        {
            "observations": [{
                "modality": "structural",
                "values": {
                    "domain": "ischemic",
                    "severity": max(0.1, min(0.9, 1.0 - health)),
                    "count": 1,
                    "seed": seed,
                },
            }],
            "context": {"simulation": state.simulation.model_dump(mode="json")},
        },
    )
    state.agent = _as_agent(agent_result.data, entity_id)
    store.record_agent(state.agent, agent_result.provenance)
    steps.append(agent_result)

    scenario = _scenario_from_workflow(entity_id, state.simulation)
    _register_local_vex_handler(registry, entity_id, scenario)
    bridge_result = _call(
        registry,
        "bridge.publish",
        entity_id,
        {
            "message_type": "agent.challenge",
            "producer": "CardiAgent",
            "consumer": "CardiVex",
            "challenge_type": "hearttwin.multimodal",
            "population": [
                {"entity_id": entity_id, "challenge": challenge, "scenario": scenario}
                for challenge in state.agent.challenges
            ],
            "intended_task": "defensive-phenotype-evaluation",
        },
    )
    consumer = bridge_result.data.get("result")
    if not isinstance(consumer, dict):
        raise WorkflowError("CardiBridge publish did not return a consumer result")
    state.vex = _as_vex(consumer)
    state.bridge = BridgePublicationPayload.model_validate({
        "contract_version": "1.0",
        "message_type": "agent.challenge",
        "message_id": str(bridge_result.data.get("message_id", "unknown")),
        "status": str(bridge_result.data.get("status", "unknown")),
        "transport": str(bridge_result.data.get("transport", "unknown")),
        "content_sha256": bridge_result.data.get("content_sha256"),
        "consumer_result": consumer,
    })
    store.record_vex(state.vex, bridge_result.provenance)
    store.record_bridge(state.bridge, bridge_result.provenance)
    steps.append(bridge_result)

    evaluation_result = _call(
        registry,
        "evaluation.run",
        entity_id,
        {
            "benchmark": state.benchmark.model_dump(mode="json"),
            "predictions": [item.model_dump(mode="json") for item in state.learning.predictions],
            "model_id": state.learning.model_id,
            "reference_labels": {sid: reference_labels[sid] for sid, partition in assignments.items() if partition == "test"},
            "task_id": "binary-cardiac-state-detection",
        },
    )
    state.evaluation = _as_evaluation(evaluation_result.data)
    store.record_evaluation(state.evaluation, evaluation_result.provenance)
    if state.evaluation.errors:
        raise WorkflowError(f"Evaluation failed: {state.evaluation.errors}")
    steps.append(evaluation_result)

    # Close the benchmark lifecycle before CardiTrace seals the run. Discovery,
    # catalog, and search remain control-plane operations and never become
    # biological CardiacState observations.
    benchmark_history_supported = False
    benchmark_health_adapter = registry.capability("benchmark.health")
    if benchmark_health_adapter is not None:
        try:
            benchmark_health = benchmark_health_adapter.invoke(
                "benchmark.health", {"entity_id": entity_id}
            )
            benchmark_history_supported = "benchmark.result.record" in set(
                benchmark_health.get("capabilities") or []
            )
        except Exception:
            benchmark_history_supported = False
    if benchmark_history_supported:
        benchmark_history_result = _call(
            registry,
            "benchmark.result.record",
            entity_id,
            {
                "benchmark_id": state.evaluation.benchmark_id,
                "benchmark_version": state.evaluation.benchmark_version,
                "benchmark_provenance_sha256": state.benchmark.metadata_sha256,
                "model_id": state.evaluation.model_id,
                "model_version": "unknown",
                "split": "test",
                "metrics": state.evaluation.metrics,
                "sample_count": sum(1 for partition in assignments.values() if partition == "test"),
                "protocol_id": state.evaluation.task_id or "hearttwin-evaluation",
                "source": "CardiEval",
            },
        )
        steps.append(benchmark_history_result)

    pre_trace_state = store.snapshot()
    pre_trace_fingerprint = pre_trace_state.state_fingerprint or ""
    state.cardiac_state = pre_trace_state
    trace_result = _call(
        registry,
        "trace.record",
        entity_id,
        {
            "context": {"workflow_run_id": run_id},
            "observations": [item.model_dump(mode="json") for item in observations],
            "workflow_state": state.model_dump(mode="json"),
            "canonical_state_fingerprint": pre_trace_fingerprint,
            "results": [item.model_dump(mode="json") for item in steps],
            "hearttwin_run_id": run_id,
        },
    )
    state.trace = trace_result.data
    store.record_trace(trace_result.data, trace_result.provenance)
    steps.append(trace_result)

    canonical = store.snapshot()
    state.cardiac_state = canonical
    state.provenance = list(canonical.provenance)
    return WorkflowRun(run_id=run_id, entity_id=entity_id, status="ok", state=state, steps=steps)
