"""Explicit typed multimodal HeartTwin workflow.

This module defines a dependency graph where service outputs are validated into
versioned contracts and simultaneously reduced into one canonical CardiacState.
"""
from __future__ import annotations

from statistics import mean
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
    """Translate a simulation summary into a phenotype-level CardiVex proxy scenario."""
    health = float(simulation.summary.get("cardiac_health_score", 0.5))
    burden = max(0.0, min(1.0, 1.0 - health))
    axes = {
        "inflammatory": burden,
        "vascular_endothelial": min(1.0, burden * 0.8),
        "metabolic_mitochondrial": min(1.0, burden * 1.1),
        "contractile_functional": burden,
        "structural_injury": min(1.0, burden * 0.9),
        "cell_death": min(1.0, burden * 0.7),
        "remodeling": min(1.0, burden * 0.85),
    }

    def domain(value: float) -> dict[str, Any]:
        return {
            "value": round(float(max(0.0, min(1.0, value))), 6),
            "uncertainty": 0.05,
            "evidence_status": "proxy",
        }

    return {
        "scenario_id": f"CVX-HT-{sha256({'entity': entity_id})[:12].upper()}",
        "version": "1.0",
        "name": "HeartTwin simulation-derived phenotype challenge",
        "target_model": "HeartTwin",
        "evidence_tier": "characterized_proxy",
        "confidence": "exploratory",
        "phenotype_domains": {name: domain(value) for name, value in axes.items()},
        "temporal_profile": [
            {"state": "baseline", "relative_time": 0.0, "duration": 1.0, "domains": {name: domain(0.0) for name in axes}},
            {"state": "simulated", "relative_time": 1.0, "duration": 1.0, "domains": {name: domain(value) for name, value in axes.items()}},
        ],
        "description": "Computational phenotype proxy derived from CardiSim output; not an empirical patient state.",
        "severity_profile": axes,
        "ood_status": "validation",
        "provenance_sources": ["Virelion-CardiSim", "Virelion-HeartTwin"],
        "provenance_transformations": ["simulation.summary -> phenotype-domain proxy"],
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

    try:
        router.register("agent.challenge", "CardiVex", handler)
    except (KeyError, ValueError):
        return


def run_multimodal_workflow(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    observations: list[Observation],
    benchmark_samples: list[dict[str, Any]],
    learning_data: list[dict[str, Any]],
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

    benchmark_result = _call(
        registry,
        "benchmark.resolve",
        entity_id,
        {
            "benchmark_id": "hearttwin-multimodal-e2e",
            "version": "1.0",
            "policy": "subject_heldout",
            "seed": seed,
            "samples": benchmark_samples,
        },
    )
    state.benchmark = _as_benchmark(benchmark_result.data)
    store.record_benchmark(state.benchmark, benchmark_result.provenance)
    steps.append(benchmark_result)

    by_sample_id = {str(row.get("sample_id")): row for row in learning_data}
    benchmark_ids = set(state.benchmark.assignments)
    missing_learning_rows = sorted(benchmark_ids - set(by_sample_id))
    if missing_learning_rows:
        raise WorkflowError(
            "CardiBench samples are missing from CardiLearn input data: "
            f"{missing_learning_rows}"
        )
    test_ids = {
        sample_id
        for sample_id, split in state.benchmark.assignments.items()
        if split == "test"
    }
    train_ids = {
        sample_id
        for sample_id, split in state.benchmark.assignments.items()
        if split == "train"
    }
    if not test_ids:
        raise WorkflowError("CardiBench produced no test holdout; evaluation cannot proceed")
    if not train_ids:
        raise WorkflowError("CardiBench produced no training split")

    training_rows = [by_sample_id[sample_id] for sample_id in sorted(train_ids)]
    prediction_rows = [by_sample_id[sample_id] for sample_id in sorted(test_ids)]

    learning_result = _call(
        registry,
        "learn.infer",
        entity_id,
        {
            "data": training_rows,
            "prediction_data": prediction_rows,
            "target_column": "target",
            "group_column": "group_id",
            "model": "logistic_regression",
            "task": "classification",
            "seed": seed,
        },
    )
    state.learning = _as_learning(learning_result.data)
    predicted_ids = {item.sample_id for item in state.learning.predictions}
    if predicted_ids != test_ids:
        raise WorkflowError(
            "CardiLearn predictions do not exactly match the locked CardiBench test set: "
            f"expected={sorted(test_ids)}, observed={sorted(predicted_ids)}"
        )
    if any(item.y_true is None for item in state.learning.predictions):
        raise WorkflowError(
            "CardiLearn returned unlabeled holdout predictions; CardiEval requires independent ground truth"
        )
    store.record_learning(state.learning, learning_result.provenance)
    steps.append(learning_result)

    scores = [float(item.score) for item in state.learning.predictions if item.score is not None]
    score_mean = mean(scores) if scores else 0.5
    sim_payload = dict(simulation or {})
    sim_payload.setdefault("preset", "mi" if score_mean >= 0.5 else "baseline")
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
            "task_id": "binary-cardiac-state-detection",
        },
    )
    state.evaluation = _as_evaluation(evaluation_result.data)
    store.record_evaluation(state.evaluation, evaluation_result.provenance)
    steps.append(evaluation_result)

    pre_trace_fingerprint = store.fingerprint()
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
