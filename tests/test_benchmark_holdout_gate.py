from __future__ import annotations

import pytest

from hearttwin.contracts import BenchmarkResolutionPayload
from hearttwin.workflow import WorkflowError, _require_classification_split_coverage


def _benchmark(label_counts: dict[str, dict[str, int]]) -> BenchmarkResolutionPayload:
    return BenchmarkResolutionPayload(
        benchmark_id="coverage-gate",
        version="1.0",
        policy="subject_heldout",
        seed=1,
        assignments={"s1": "train", "s2": "test"},
        label_counts=label_counts,
        sample_count=2,
        group_count=2,
        metadata_sha256="a" * 64,
        samples=[
            {"sample_id": "s1", "group_id": "g1", "study_id": "st", "label": "sham"},
            {"sample_id": "s2", "group_id": "g2", "study_id": "st", "label": "MI"},
        ],
    )


def test_classification_split_coverage_rejects_single_label_holdout() -> None:
    benchmark = _benchmark({"train": {"sham": 5, "MI": 5}, "test": {"MI": 2}})
    with pytest.raises(WorkflowError, match="at least two represented labels"):
        _require_classification_split_coverage(benchmark, "test")


def test_classification_split_coverage_accepts_two_labels() -> None:
    benchmark = _benchmark({"train": {"sham": 5, "MI": 5}, "test": {"sham": 2, "MI": 2}})
    _require_classification_split_coverage(benchmark, "train")
    _require_classification_split_coverage(benchmark, "test")
