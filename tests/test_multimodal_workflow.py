from __future__ import annotations

import shutil

import numpy as np
import pytest

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
    assert run.state.vex is not None
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
    assert run.state.bridge.consumer_result is not None


def _write_ecg(path) -> None:
    import pandas as pd

    fs = 250.0
    t = np.arange(0.0, 5.0, 1.0 / fs)
    # Smooth deterministic test signal; ElectroTrace is exercising its import/QC path here.
    signal = np.sin(2 * np.pi * 1.2 * t) + 0.05 * np.sin(2 * np.pi * 8.0 * t)
    pd.DataFrame({"time": t, "lead_I": signal}).to_csv(path, index=False)


def _write_video(path) -> None:
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        30,
        (96, 96),
        isColor=False,
    )
    if not writer.isOpened():
        pytest.skip("OpenCV MP4 writer unavailable on this runner")
    try:
        for frame_idx in range(90):
            frame = np.zeros((96, 96), dtype=np.uint8)
            phase = frame_idx / 30.0 * 2 * np.pi * 1.25
            radius = int(18 + 6 * (0.5 + 0.5 * np.sin(phase)))
            cv2.circle(frame, (48, 48), radius, 220, -1)
            writer.write(frame)
    finally:
        writer.release()


def test_real_specialist_modalities_feed_typed_workflow_state(tmp_path) -> None:
    registry = load_registry()
    if shutil.which("electrotrace-hearttwin") is None or shutil.which("myotrace-hearttwin") is None:
        pytest.skip("specialist HeartTwin commands are not installed")

    ecg = tmp_path / "fixture_ecg.csv"
    video = tmp_path / "fixture_video.mp4"
    _write_ecg(ecg)
    _write_video(video)

    rows = _rows()
    observations = [
        _observation("E2E-SPECIALIST", "molecular", {"gene_a": 0.4, "gene_b": 0.6}),
        _observation("E2E-SPECIALIST", "electrical", {"input_path": str(ecg), "time_col": "time"}),
        _observation("E2E-SPECIALIST", "mechanical", {"input_path": str(video), "fps": 30}),
    ]

    run = run_multimodal_workflow(
        registry,
        entity_id="E2E-SPECIALIST",
        observations=observations,
        benchmark_samples=[
            {
                "sample_id": row["sample_id"],
                "group_id": row["group_id"],
                "study_id": row["study_id"],
                "label": row["label"],
            }
            for row in rows
        ],
        learning_data=rows,
        simulation={"preset": "mi", "n_cells": 8, "duration": 1.0, "dt": 0.25},
        seed=7,
    )

    assert {item.modality for item in run.state.modality_analyses} == {"electrical", "mechanical"}
    assert {item.capability for item in run.state.modality_analyses} == {
        "electrical.analyze",
        "mechanical.analyze",
    }
    assert all(item.content_sha256 for item in run.state.modality_analyses)
    assert run.state.evaluation is not None
    assert run.state.trace is not None
