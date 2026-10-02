from __future__ import annotations

import os
from collections import Counter

import pytest

from hearttwin import Observation, Provenance, load_registry, run_multimodal_workflow


CANARY = "HEARTTWIN-PIPE-CANARY-9f41c7"


def _observation(entity_id: str, modality: str, values: dict) -> Observation:
    return Observation(
        observation_id=f"obs-{entity_id}-{modality}",
        modality=modality,
        values=values,
        provenance=Provenance(
            source_service="stack-continuity-probe",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id=f"probe-{entity_id}-{modality}",
        ),
    )


def _rows(n: int = 20) -> list[dict]:
    return [
        {
            "sample_id": f"{CANARY}-S{i:03d}",
            "group_id": f"{CANARY}-G{i:03d}",
            "study_id": f"{CANARY}-STUDY",
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "gene_a": float(i) / n,
            "gene_b": float((i * 7) % n) / n,
        }
        for i in range(n)
    ]


def _require_full_stack(registry) -> None:
    missing = [
        name
        for name, adapter in registry.adapters.items()
        if not adapter.available()
    ]
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("full stack not installed: " + ", ".join(missing))
    assert not missing, "full stack unavailable: " + ", ".join(missing)


def test_every_advertised_capability_has_one_aligned_route() -> None:
    registry = load_registry()
    ownership: dict[str, list[str]] = {}

    for spec in registry.services():
        assert spec.name in registry.adapters
        for capability in spec.capabilities:
            ownership.setdefault(capability, []).append(spec.name)
            assert registry.capability(capability) is registry.adapters[spec.name]

    duplicates = {
        capability: owners
        for capability, owners in ownership.items()
        if len(owners) != 1
    }
    assert not duplicates, f"Capabilities have ambiguous routes: {duplicates}"

    advertised = sum(len(spec.capabilities) for spec in registry.services())
    assert advertised == len(ownership)


def test_same_canary_crosses_real_multimodal_pipe_without_misalignment(
    monkeypatch,
    tmp_path,
) -> None:
    registry = load_registry()
    _require_full_stack(registry)
    monkeypatch.setenv("CARDITRACE_ROOT", str(tmp_path / "carditrace"))

    calls: list[tuple[str, str, dict]] = []
    for name, adapter in registry.adapters.items():
        original = adapter.invoke

        def wrapped(capability, payload, *, _name=name, _original=original):
            calls.append((_name, capability, payload))
            return _original(capability, payload)

        monkeypatch.setattr(adapter, "invoke", wrapped)

    rows = _rows()
    sample_ids = {row["sample_id"] for row in rows}
    observations = [
        _observation(
            CANARY,
            "molecular",
            {
                "gene_a": 0.4,
                "gene_b": 0.6,
                "continuity_canary": CANARY,
            },
        ),
        _observation(
            CANARY,
            "structural",
            {
                "region": "left_ventricle",
                "zone": "IZ",
                "continuity_canary": CANARY,
            },
        ),
        _observation(
            CANARY,
            "clinical",
            {
                "condition": "continuity_probe",
                "continuity_canary": CANARY,
            },
        ),
    ]

    run = run_multimodal_workflow(
        registry,
        entity_id=CANARY,
        observations=observations,
        benchmark_samples=[
            {
                "sample_id": row["sample_id"],
                "group_id": row["group_id"],
                "study_id": row["study_id"],
                "label": row["label"],
                "region": "IZ" if row["target"] else "remote",
            }
            for row in rows
        ],
        learning_data=rows,
        feature_columns=["gene_a", "gene_b"],
        reference_labels={
            row["sample_id"]: row["target"]
            for row in rows
        },
        simulation={
            "preset": "mi",
            "n_cells": 12,
            "duration": 1.0,
            "dt": 0.25,
        },
        seed=314159,
    )

    assert run.status == "ok"
    assert run.entity_id == CANARY
    assert run.state.entity_id == CANARY
    assert run.state.cardiac_state is not None
    assert run.state.cardiac_state.entity_id == CANARY
    assert run.state.cardiac_state.state_fingerprint

    expected_path = {
        "atlas.context",
        "benchmark.resolve",
        "learn.infer",
        "simulation.run",
        "agent.challenge",
        "bridge.publish",
        "vex.observe",
        "evaluation.run",
        "trace.record",
    }
    observed = [capability for _, capability, _ in calls]
    assert expected_path <= set(observed)

    counts = Counter(observed)
    for capability in expected_path:
        assert counts[capability] == 1, (
            f"{capability} was called {counts[capability]} times"
        )

    for service, capability, payload in calls:
        if capability in expected_path:
            assert payload.get("entity_id") == CANARY, (
                f"{service}/{capability} received a misaligned entity_id: "
                f"{payload.get('entity_id')!r}"
            )

    assert run.state.atlas is not None
    assert run.state.atlas.context_id == f"{CANARY}-context"

    assert run.state.benchmark is not None
    assert set(run.state.benchmark.assignments) == sample_ids
    assert {sample.sample_id for sample in run.state.benchmark.samples} == sample_ids

    assert run.state.learning is not None
    prediction_ids = {
        prediction.sample_id
        for prediction in run.state.learning.predictions
    }
    expected_prediction_ids = {
        sample_id
        for sample_id, partition in run.state.benchmark.assignments.items()
        if partition == "test"
    }
    assert prediction_ids == expected_prediction_ids

    assert run.state.evaluation is not None
    assert run.state.evaluation.model_id == run.state.learning.model_id
    assert (
        run.state.evaluation.benchmark_id
        == run.state.benchmark.benchmark_id
    )
    assert (
        run.state.evaluation.benchmark_version
        == run.state.benchmark.version
    )

    assert run.state.agent is not None
    assert run.state.agent.entity_id == CANARY

    assert run.state.bridge is not None
    assert run.state.vex is not None
    assert run.state.bridge.message_type == "agent.challenge"
    assert run.state.bridge.consumer_result is not None
    assert (
        run.state.bridge.consumer_result.get("scenario_id")
        == run.state.vex.scenario_id
    )

    trace_payload = next(
        payload
        for _, capability, payload in calls
        if capability == "trace.record"
    )
    assert trace_payload["entity_id"] == CANARY
    assert trace_payload["hearttwin_run_id"] == run.run_id
    assert trace_payload["context"]["workflow_run_id"] == run.run_id

    traced_state = trace_payload["workflow_state"]
    traced_canonical = traced_state["cardiac_state"]
    assert traced_state["entity_id"] == CANARY
    assert traced_canonical["entity_id"] == CANARY
    assert (
        traced_canonical["state_fingerprint"]
        == trace_payload["canonical_state_fingerprint"]
    )
    assert traced_canonical["atlas_context"]["context_id"] == f"{CANARY}-context"
    assert traced_canonical["benchmarks"]
    assert traced_canonical["prediction_artifacts"]
    assert traced_canonical["simulation_artifacts"]
    assert traced_canonical["challenges"]
    assert traced_canonical["vex_observations"]
    assert traced_canonical["bridge_publications"]
    assert traced_canonical["evaluation_artifacts"]

    traced_observations = trace_payload["observations"]
    assert all(
        item["values"]["continuity_canary"] == CANARY
        for item in traced_observations
    )

    result_capabilities = {
        item["capability"]
        for item in trace_payload["results"]
    }
    assert expected_path - {"trace.record", "vex.observe"} <= result_capabilities


def test_disconnected_native_branches_resolve_without_route_aliasing() -> None:
    registry = load_registry()
    _require_full_stack(registry)

    branch_probes = {
        "anatomy.health": "CardiAnatomy",
        "ep.health": "CardiEP",
        "infer.health": "CardiInfer",
        "atlas.search": "CardiAtlas",
        "design.generate": "CardiStudio",
        "host.map": "DCCP",
    }
    payloads = {
        "anatomy.health": {"entity_id": CANARY},
        "ep.health": {"entity_id": CANARY},
        "infer.health": {"entity_id": CANARY},
        "atlas.search": {
            "entity_id": CANARY,
            "query": CANARY,
            "records": [],
        },
        "design.generate": {
            "entity_id": CANARY,
            "factors": {"condition": ["probe"]},
            "replicates": 1,
        },
        "host.map": {
            "entity_id": CANARY,
            "module_scores": {},
        },
    }

    for capability, expected_service in branch_probes.items():
        adapter = registry.capability(capability)
        assert adapter is not None
        assert adapter.spec.name == expected_service
        result = adapter.invoke(capability, payloads[capability])
        assert isinstance(result, dict)

    atlas = registry.capability("atlas.search")
    assert atlas is not None
    atlas_result = atlas.invoke(
        "atlas.search",
        {
            "entity_id": CANARY,
            "query": CANARY,
            "records": [],
        },
    )
    assert atlas_result["query"] == CANARY
