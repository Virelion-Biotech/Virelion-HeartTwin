from __future__ import annotations

import os
from pathlib import Path

import pytest

from hearttwin import load_registry


def _require_command(registry, service: str) -> None:
    adapter = registry.adapters[service]
    if not adapter.available():
        if os.getenv("HEARTTWIN_REQUIRE_NATIVE") == "1":
            pytest.fail(f"{service} HeartTwin adapter is unavailable")
        pytest.skip(f"{service} HeartTwin adapter is not installed")


def test_opticell_command_adapter_runs_real_image_qc(tmp_path: Path) -> None:
    registry = load_registry()
    _require_command(registry, "OptiCell")

    import numpy as np
    import tifffile

    y, x = np.mgrid[:64, :64]
    image = (
        40
        + 140 * np.exp(-((x - 20) ** 2 + (y - 20) ** 2) / 100)
        + 100 * np.exp(-((x - 45) ** 2 + (y - 42) ** 2) / 80)
    ).clip(0, 255).astype("uint8")
    path = tmp_path / "cells.tif"
    tifffile.imwrite(path, image)

    result = registry.adapters["OptiCell"].invoke(
        "imaging.qc",
        {
            "entity_id": "opticell-smoke",
            "observations": [
                {
                    "modality": "imaging",
                    "values": {
                        "input_path": str(path),
                        "cell_method": "threshold",
                        "adaptive_qc": False,
                    },
                }
            ],
        },
    )
    assert result["entity_id"] == "opticell-smoke"
    assert result["n_rows"] == 1
    assert result["cell_method"] == "threshold"
    assert result["results"]


def test_cardioscore_command_adapter_runs_synthetic_mea(tmp_path: Path) -> None:
    registry = load_registry()
    _require_command(registry, "CardioScore")

    from virelion_cardioscore.io.synthetic import generate_synthetic_mea

    path = tmp_path / "mea.csv"
    generate_synthetic_mea(
        n_compounds=2,
        n_concentrations=3,
        n_wells_per_conc=3,
        seed=17,
    ).features.to_csv(path, index=False)

    result = registry.adapters["CardioScore"].invoke(
        "safety.score",
        {
            "entity_id": "cardioscore-smoke",
            "observations": [
                {
                    "modality": "safety",
                    "values": {"input_path": str(path)},
                }
            ],
        },
    )
    assert result["entity_id"] == "cardioscore-smoke"
    assert isinstance(result["scores"], list)
    assert result["summary"]


def test_cardiagent_command_adapter_generates_real_challenge() -> None:
    registry = load_registry()
    _require_command(registry, "CardiAgent")

    result = registry.adapters["CardiAgent"].invoke(
        "agent.challenge",
        {
            "entity_id": "cardiagent-smoke",
            "observations": [
                {
                    "modality": "structural",
                    "values": {
                        "domain": "ischemic",
                        "severity": 0.5,
                        "difficulty": 0.6,
                        "seed": 19,
                        "count": 2,
                    },
                }
            ],
        },
    )
    assert result["entity_id"] == "cardiagent-smoke"
    assert len(result["challenges"]) == 2
    assert all(challenge for challenge in result["challenges"])


def test_carditrace_command_adapter_records_hash_chained_run(
    tmp_path: Path, monkeypatch
) -> None:
    registry = load_registry()
    _require_command(registry, "CardiTrace")
    monkeypatch.setenv("CARDITRACE_ROOT", str(tmp_path / "trace"))

    result = registry.adapters["CardiTrace"].invoke(
        "trace.record",
        {
            "entity_id": "carditrace-smoke",
            "context": {"purpose": "integration-test"},
            "observations": [],
        },
    )
    assert result["status"] == "succeeded"
    assert result["execution_fingerprint"]
    assert result["input_artifact_id"]
    assert Path(result["trace_root"]).exists()
