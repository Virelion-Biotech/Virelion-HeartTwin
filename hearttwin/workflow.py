"""Explicit typed multimodal HeartTwin workflow.

This module is deliberately separate from ``HeartTwin.run``. The latter remains a
low-level capability runner; this module defines an actual dependency graph where
outputs are validated into typed contracts before becoming inputs to downstream
services.
"""
from __future__ import annotations

from statistics import mean
from typing import Any, Iterable

from .cardibridge_adapter import _local_router
from .contracts import (
    AgentChallengePayload,
    AtlasContextPayload,
    BenchmarkResolutionPayload,
    EvaluationResultPayload,
    LearningResultPayload,
    Observation,
    Provenance,
    ServiceResult,
    SimulationResultPayload,
    WorkflowRun,
    WorkflowState,
)
from .orchestrator import HeartTwin
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry


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


def _benchmark_lock_to_learning_test(
    benchmark_samples: list[dict[str, Any]], learning: LearningResultPayload, *, seed: int
) -> BenchmarkResolutionPayload:
    """Re-materialize CardiBench using the model's already-locked test biological groups.

    CardiLearn and CardiBench intentionally use different split implementations. This
    explicit transformation prevents the common but dangerous mistake of evaluating a
    model on one test split while claiming another benchmark split.
    """
    try:
        from cardi_bench import Sample, materialize
    except Exception as exc:  # pragma: no cover - environment dependent
        raise WorkflowError(f"CardiBench native package is required: {exc}") from exc

    by_id = {str(row["sample_id"]): row for row in benchmark_samples}
    test_ids = {prediction.sample_id for prediction in learning.predictions}
    missing = sorted(test_ids - set(by_id))
    if missing:
        raise WorkflowError(f"CardiLearn test predictions are absent from CardiBench samples: {missing}")
    test_groups = {str(by_id[sample_id]["group_id"]) for sample_id in test_ids}
    rows = [
        Sample(
            sample_id=str(item["sample_id"]),
            group_id=str(item["group_id"]),
            study_id=str(item["study_id"]),
            label=str(item["label"]),
            technical_group=item.get("technical_group"),
            organism=item.get("organism"),
            timepoint=item.get("timepoint"),
            cell_context=item.get("cell_context"),
            region=item.get("region"),
        )
        for item in benchmark_samples
    ]
    materialized = materialize(
        rows,
        benchmark_id="hearttwin-multimodal-e2e",
        version="1.0",
        policy="subject_heldout",
        test_values=test_groups,
        seed=seed,
    )
    return BenchmarkResolutionPayload.model_validate(
        {
            "contract_version": "1.0",
            **materialized.to_dict(),
            "samples": benchmark_samples,
        }
    )


def _scenario_from_workflow(entity_id: str, simulation: SimulationResultPayload) -> dict[str, Any]:
    """Translate simulation-level outputs into a phenotype-level CardiVex scenario."""
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
        return {"value": round(float(max(0.0, min(1.0, value))), 6), "uncertainty": 0.05, "evidence_status": "proxy"}

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
        return vex.invoke(
            "vex.observe",
            {"entity_id": entity_id, "scenario": scenario, "bridge_message_id": envelope.message_id},
        )

    router.register("agent.challenge", "CardiVex", handler)


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
    """Run a complete local multimodal HeartTwin research pipeline.

    Dataflow: Atlas context -> CardiLearn -> CardiBench lock -> CardiSim -> CardiAgent
    -> CardiBridge -> CardiVex -> CardiEval -> CardiTrace.
    Every service-to-service transition is validated against a typed HeartTwin contract.
    """
    run_id = new_run_id(entity_id, {
        "observations": [item.model_dump(mode="json") for item in observations],
        "benchmark_samples": benchmark_samples,
        "learning_data": learning_data,
        "seed": seed,
    })
    steps: list[ServiceResult] = []
    state = WorkflowState(entity_id=entity_id, observations=observations)

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
    steps.append(atlas_result)

    learning_result = _call(
        registry,
        "learn.infer",
        entity_id,
        {
            "data": learning_data,
            "target_column": "target",
            "group_column": "group_id",
            "model": "logistic_regression",
            "task": "classification",
            "seed": seed,
        },
    )
    state.learning = _as_learning(learning_result.data)
    steps.append(learning_result)

    benchmark_result = _call(
        registry,
        "benchmark.resolve",
        entity_id,
        {
            "benchmark_id": "hearttwin-multimodal-initial",
            "version": "1.0",
            "policy": "subject_heldout",
            "seed": seed,
            "samples": benchmark_samples,
        },
    )
    initial_benchmark = _as_benchmark(benchmark_result.data)
    locked_benchmark = _benchmark_lock_to_learning_test(benchmark_samples, state.learning, seed=seed)
    # The second lock is the explicit typed transformation that binds the evaluator to
    # the actual model test groups rather than trusting two unrelated split algorithms.
    benchmark_result = benchmark_result.model_copy(
        update={"data": locked_benchmark.model_dump(mode="json")}
    )
    state.benchmark = locked_benchmark
    steps.append(benchmark_result)

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
    steps.append(simulation_result)

    agent_result = _call(
        registry,
        "agent.challenge",
        entity_id,
        {
            "observations": [
                {
                    "modality": "structural",
                    "values": {
                        "domain": "ischemic",
                        "severity": max(0.1, min(0.9, 1.0 - float(state.simulation.summary.get("cardiac_health_score", 0.5)))),
                        "count": 1,
                        "seed": seed,
                    },
                }
            ],
            "context": {"simulation": state.simulation.model_dump(mode="json")},
        },
    )
    state.agent = _as_agent(agent_result.data, entity_id)
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
            "population": state.agent.challenges,
            "intended_task": "defensive-phenotype-evaluation",
        },
    )
    bridge_data = bridge_result.data
    state.bridge = {
        "contract_version": "1.0",
        "message_type": "agent.challenge",
        "message_id": str(bridge_data.get("message_id", "unknown")),
        "status": str(bridge_data.get("status", "unknown")),
        "transport": str(bridge_data.get("transport", "unknown")),
        "content_sha256": bridge_data.get("content_sha256"),
    }
    steps.append(bridge_result)

    evaluation_payload = {
        "benchmark": state.benchmark.model_dump(mode="json"),
        "predictions": [item.model_dump(mode="json") for item in state.learning.predictions],
        "model_id": state.learning.model_id,
        "task_id": "binary-cardiac-state-detection",
    }
    evaluation_result = _call(registry, "evaluation.run", entity_id, evaluation_payload)
    state.evaluation = _as_evaluation(evaluation_result.data)
    steps.append(evaluation_result)

    trace_result = _call(
        registry,
        "trace.record",
        entity_id,
        {
            "context": {"workflow_run_id": run_id},
            "observations": [item.model_dump(mode="json") for item in observations],
            "workflow_state": state.model_dump(mode="json"),
            "results": [item.model_dump(mode="json") for item in steps],
            "hearttwin_run_id": run_id,
        },
    )
    state.trace = trace_result.data
    steps.append(trace_result)

    state.provenance.extend(item.provenance for item in steps if item.provenance is not None)
    return WorkflowRun(run_id=run_id, entity_id=entity_id, status="ok", state=state, steps=steps)
