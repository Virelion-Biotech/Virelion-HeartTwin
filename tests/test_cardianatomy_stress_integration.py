from __future__ import annotations

import os

import pytest

from hearttwin import load_registry


def _registry():
    registry = load_registry()
    adapter = registry.adapters["CardiAnatomy"]
    if not adapter.available():
        if os.getenv("HEARTTWIN_REQUIRE_NATIVE") == "1":
            pytest.fail("CardiAnatomy native package is required but unavailable")
        pytest.skip("CardiAnatomy native package not installed")
    return registry


def _invoke(registry, capability: str, payload: dict):
    adapter = registry.capability(capability)
    assert adapter is not None
    return adapter.invoke(capability, payload)


@pytest.mark.parametrize(
    ("capability", "payload", "message"),
    [
        (
            "anatomy.transforms.invert",
            {
                "matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            },
            "singular",
        ),
        (
            "anatomy.registration.rigid",
            {
                "source_points": [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                "target_points": [[1, 1, 1], [2, 1, 1], [3, 1, 1]],
            },
            "non-collinear",
        ),
        (
            "anatomy.correspondence.compare",
            {
                "reference_points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "target_points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "reference_cells": [[0, 1.5, 2]],
                "target_cells": [[0, 1, 2]],
            },
            "integer indices",
        ),
        (
            "anatomy.manifest.audit",
            {
                "tools": [{"tool_id": "augmenta"}],
                "allow_restricted": "false",
            },
            "allow_restricted",
        ),
        (
            "anatomy.validation.points",
            {
                "reference_points": [[0, 0, 0]],
                "prediction_points": [[0, 0, 0]],
                "block_size": 1000000,
            },
            "block_size",
        ),
        (
            "anatomy.motion.summarize",
            {
                "frames": [[[0, 0, 0]], [[1, 0, 0]]],
                "cyclic": "false",
            },
            "cyclic",
        ),
    ],
)
def test_cardianatomy_bad_payloads_fail_closed_through_registry(
    capability: str,
    payload: dict,
    message: str,
) -> None:
    registry = _registry()
    with pytest.raises(ValueError, match=message):
        _invoke(registry, capability, payload)

    health = _invoke(registry, "anatomy.health", {})
    assert health["service"] == "CardiAnatomy"
    assert health["status"] == "ok"


def test_cardianatomy_quadratic_point_work_is_bounded_through_registry() -> None:
    registry = _registry()
    points = [[float(index), 0.0, 0.0] for index in range(100)]
    with pytest.raises(ValueError, match="pair-evaluation limit"):
        _invoke(
            registry,
            "anatomy.validation.points",
            {
                "reference_points": points,
                "prediction_points": points,
                "max_pair_evaluations": 1000,
            },
        )


def test_cardianatomy_repeated_native_calls_are_stateless() -> None:
    registry = _registry()
    payload = {"volumes_ml": [120.0, 90.0, 60.0, 85.0, 110.0]}
    baseline = _invoke(registry, "anatomy.cine.phases", payload)

    for _ in range(100):
        assert _invoke(registry, "anatomy.cine.phases", payload) == baseline


def test_cardianatomy_rejects_nonfinite_segmentation_through_registry() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="finite"):
        _invoke(
            registry,
            "anatomy.validation.segmentation",
            {
                "reference_labels": [0.0, float("nan")],
                "prediction_labels": [0.0, 1.0],
            },
        )

    healthy = _invoke(registry, "anatomy.health", {})
    assert healthy["status"] == "ok"
