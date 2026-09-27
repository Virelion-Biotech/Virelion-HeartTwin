from __future__ import annotations

import os

import pytest

from hearttwin import load_registry


NATIVE_SERVICES = {
    "CardiAtlas",
    "CardiBench",
    "CardiEval",
    "CardiLearn",
    "CardiSim",
    "CardiVex",
    "CardiStudio",
    "DCCP",
}


def _require_native(registry) -> None:
    missing = sorted(name for name in NATIVE_SERVICES if not registry.adapters[name].available())
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("native component packages not installed: " + ", ".join(missing))
    assert not missing, "native component packages not installed: " + ", ".join(missing)


def test_all_endpoint_only_services_have_native_connections() -> None:
    registry = load_registry()
    specs = {spec.name: spec for spec in registry.services()}
    assert NATIVE_SERVICES <= set(specs)
    for name in sorted(NATIVE_SERVICES):
        assert specs[name].builtin is not None
    _require_native(registry)


def test_cardistudio_native_smoke() -> None:
    registry = load_registry()
    _require_native(registry)
    result = registry.capability("design.generate").invoke(
        "design.generate",
        {"entity_id": "smoke", "factors": {"condition": ["sham", "MI"]}, "replicates": 1},
    )
    assert result["n_runs"] == 2
    assert len(result["design"]) == 2


def test_cardibench_native_smoke() -> None:
    registry = load_registry()
    _require_native(registry)
    samples = [
        {
            "sample_id": f"S{i}",
            "group_id": f"G{i}",
            "study_id": "ST1",
            "label": "MI" if i % 2 else "sham",
        }
        for i in range(6)
    ]
    result = registry.capability("benchmark.resolve").invoke(
        "benchmark.resolve",
        {"entity_id": "smoke", "samples": samples, "seed": 1},
    )
    assert result["sample_count"] == 6
    assert set(result["assignments"]) == {item["sample_id"] for item in samples}


def test_cardieval_rejects_missing_holdout_ground_truth() -> None:
    registry = load_registry()
    _require_native(registry)
    adapter = registry.capability("evaluation.run")
    assert adapter is not None
    benchmark = {
        "benchmark_id": "ground-truth-gate",
        "version": "1.0",
        "policy": "subject_heldout",
        "seed": 1,
        "assignments": {"S1": "test"},
        "sample_count": 1,
        "group_count": 1,
        "metadata_sha256": "a" * 64,
        "samples": [
            {"sample_id": "S1", "group_id": "G1", "study_id": "ST1", "label": "MI"}
        ],
    }
    with pytest.raises(ValueError, match="independent ground truth"):
        adapter.invoke(
            "evaluation.run",
            {
                "benchmark": benchmark,
                "predictions": [{"sample_id": "S1", "y_true": None, "y_pred": 1, "score": 0.9}],
                "reference_labels": {},
                "model_id": "fixture",
            },
        )


def test_cardilearn_excludes_identifiers_and_outcome_labels_from_features() -> None:
    registry = load_registry()
    _require_native(registry)
    adapter = registry.capability("learn.infer")
    rows = [
        {
            "sample_id": f"S{i}",
            "study_id": "ST1",
            "group_id": f"G{i}",
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "x1": float(i),
            "x2": float(i % 3),
        }
        for i in range(20)
    ]
    result = adapter.invoke(
        "learn.infer",
        {
            "data": rows,
            "target_column": "target",
            "group_column": "group_id",
            "task": "classification",
            "model": "logistic_regression",
            "seed": 7,
        },
    )
    assert result["feature_columns"] == ["x1", "x2"]
    assert not {"sample_id", "study_id", "group_id", "label", "target"} & set(result["feature_columns"])


def test_cardieval_rejects_prediction_label_that_disagrees_with_reference() -> None:
    registry = load_registry()
    _require_native(registry)
    adapter = registry.capability("evaluation.run")
    benchmark = {
        "benchmark_id": "ground-truth-mismatch",
        "version": "1.0",
        "policy": "subject_heldout",
        "seed": 1,
        "assignments": {"S1": "test"},
        "label_counts": {"test": {"MI": 1}},
        "sample_count": 1,
        "group_count": 1,
        "metadata_sha256": "a" * 64,
        "samples": [
            {"sample_id": "S1", "group_id": "G1", "study_id": "ST1", "label": "MI"}
        ],
    }
    with pytest.raises(ValueError, match="disagrees with controlled reference"):
        adapter.invoke(
            "evaluation.run",
            {
                "benchmark": benchmark,
                "predictions": [{"sample_id": "S1", "y_true": 0, "y_pred": 1, "score": 0.9}],
                "reference_labels": {"S1": 1},
                "model_id": "fixture",
            },
        )
