from __future__ import annotations

from hearttwin import Observation, Provenance, load_registry, run_multimodal_workflow


def _observation(entity_id: str, modality: str, values: dict) -> Observation:
    return Observation(
        observation_id=f"obs-{entity_id}-{modality}",
        modality=modality,
        values=values,
        provenance=Provenance(
            source_service="hearttwin-e2e-fixture",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id="fixture",
        ),
    )


def _rows(n: int = 20) -> list[dict]:
    return [
        {
            "sample_id": f"S{i:03d}",
            "group_id": f"G{i:03d}",
            "study_id": "E2E-001",
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "gene_a": float(i) / n,
            "gene_b": float((i * 7) % n) / n,
        }
        for i in range(n)
    ]


def test_native_multimodal_workflow_is_end_to_end() -> None:
    registry = load_registry()
    observations = [
        _observation("E2E-001", "molecular", {"gene_a": 0.4, "gene_b": 0.6}),
        _observation("E2E-001", "structural", {"region": "left_ventricle", "zone": "IZ"}),
        _observation("E2E-001", "clinical", {"condition": "research_fixture"}),
    ]
    rows = _rows()

    run = run_multimodal_workflow(
        registry,
        entity_id="E2E-001",
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
        simulation={"preset": "mi", "n_cells": 16, "duration": 1.0, "dt": 0.25},
        seed=42,
    )

    assert run.status == "ok"
    assert run.state.atlas is not None
    assert run.state.learning is not None
    assert run.state.benchmark is not None
    assert run.state.simulation is not None
    assert run.state.agent is not None
    assert run.state.bridge is not None
    assert run.state.evaluation is not None
    assert run.state.trace is not None

    expected = {
        "atlas.context",
        "learn.infer",
        "benchmark.resolve",
        "simulation.run",
        "agent.challenge",
        "bridge.publish",
        "evaluation.run",
        "trace.record",
    }
    assert {step.capability for step in run.steps} == expected
    assert all(step.status == "ok" for step in run.steps)
    assert run.state.evaluation.primary_metric == "macro_f1"
    assert run.state.evaluation.evaluation_fingerprint
    assert run.state.bridge.transport == "in-process"
    assert run.state.bridge.status in {"processed", "duplicate"}
