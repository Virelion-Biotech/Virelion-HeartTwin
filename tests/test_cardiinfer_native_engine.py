from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from hearttwin import load_registry


def _require_cardiinfer(registry) -> None:
    adapter = registry.adapters["CardiInfer"]
    if not adapter.available() and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("CardiInfer native package not installed")
    assert adapter.available()


def test_cardiinfer_generic_backend_surface_is_registered() -> None:
    registry = load_registry()
    _require_cardiinfer(registry)

    backend_adapter = registry.capability("infer.backends")
    assert backend_adapter is not None
    backend_report = backend_adapter.invoke("infer.backends", {})
    names = {item["name"] for item in backend_report["backends"]}
    assert {
        "native-abc-smc-v1",
        "native-metropolis-v1",
        "native-map-de-v1",
    } <= names

    ecosystem_adapter = registry.capability("infer.ecosystem")
    assert ecosystem_adapter is not None
    ecosystem = ecosystem_adapter.invoke("infer.ecosystem", {})
    integration_names = {item["name"] for item in ecosystem["integrations"]}
    assert {"arviz", "dynesty", "pints", "pyabc", "pypesto", "salib", "sbi"} <= integration_names


def test_hearttwin_runs_generic_cardiinfer_command_forward_model(tmp_path: Path) -> None:
    registry = load_registry()
    _require_cardiinfer(registry)

    forward = tmp_path / "toy_forward.py"
    forward.write_text(
        "import json, os\n"
        "payload = json.loads(os.environ['HEARTTWIN_PAYLOAD'])\n"
        "x = float(payload['parameters']['x'])\n"
        "print(json.dumps({'outputs': {'y': [x]}}))\n",
        encoding="utf-8",
    )

    infer = registry.capability("infer.run")
    assert infer is not None
    result = infer.invoke(
        "infer.run",
        {
            "subject_id": "hearttwin-generic-infer-smoke",
            "model_service": "ToyForward",
            "model_capability": "toy.simulate",
            "backend": "native-abc-smc-v1",
            "priors": [
                {
                    "name": "x",
                    "distribution": "uniform",
                    "bounds": [0.0, 1.0],
                }
            ],
            "likelihood": [
                {
                    "term_id": "y",
                    "observation_ref": {
                        "artifact_id": "synthetic-y",
                        "kind": "synthetic",
                        "uri": "file:///unused",
                    },
                    "model_output": "outputs.y",
                    "discrepancy": "rmse",
                    "metadata": {"observed": [0.25]},
                }
            ],
            "model_context": {
                "forward_model": {
                    "mode": "command",
                    "command": [sys.executable, str(forward)],
                    "timeout_s": 10,
                }
            },
            "sampler_settings": {
                "n_particles": 8,
                "n_generations": 1,
                "initial_oversample": 2,
                "output_dir": str(tmp_path / "inference"),
            },
            "seed": 17,
        },
    )

    assert result["contract_version"] == "1.1"
    assert result["backend"] == "native-abc-smc-v1"
    assert result["model_service"] == "ToyForward"
    assert result["validation_status"] == "software_checked"
    assert len(result["posterior"]) == 1
    artifact = result["posterior_samples"]
    assert artifact is not None
    assert len(artifact["sha256"]) == 64
    assert Path(urlparse(artifact["uri"]).path).is_file()

    payload = json.loads(Path(urlparse(artifact["uri"]).path).read_text(encoding="utf-8"))
    assert len(payload["samples"]) == 8
    assert abs(sum(float(item["weight"]) for item in payload["samples"]) - 1.0) < 1e-9
