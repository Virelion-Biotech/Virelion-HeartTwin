from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pytest

pytest.importorskip("electrotrace")
pytest.importorskip("cardiep")
pytest.importorskip("cardiinfer")

from hearttwin import Observation, Provenance, load_registry, prepare_ep_inference_problem


def test_ecg_becomes_cardiinfer_likelihood_input(tmp_path: Path) -> None:
    fs = 100.0
    time = np.arange(1000, dtype=float) / fs
    peaks = np.asarray([100, 300, 500, 700, 900], dtype=int)
    lead_i = np.zeros_like(time)
    lead_ii = np.zeros_like(time)
    for peak in peaks:
        x = np.arange(len(time)) - peak
        lead_i += np.exp(-0.5 * (x / 2.0) ** 2)
        lead_ii += 0.7 * np.exp(-0.5 * ((x - 1.0) / 2.5) ** 2)

    source = tmp_path / "ecg.csv"
    rows = ["time,I,II"]
    rows.extend(
        f"{t:.6f},{lead_i[i]:.12g},{lead_ii[i]:.12g}"
        for i, t in enumerate(time)
    )
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    observation = Observation(
        observation_id="measured-ecg",
        modality="electrical",
        values={
            "input_path": str(source),
            "observation_kind": "ecg",
            "r_indices": peaks.tolist(),
            "pre_s": 0.10,
            "post_s": 0.15,
            "units": "mV",
            "calibration_output_dir": str(tmp_path / "calibration"),
        },
        provenance=Provenance(
            source_service="test",
            run_id="measurement-run",
        ),
    )

    problem = prepare_ep_inference_problem(
        load_registry(),
        entity_id="S1",
        electrical_observation=observation,
        anatomy_ref={
            "artifact_id": "mesh-1",
            "kind": "anatomy_bundle",
            "uri": (tmp_path / "anatomy.json").resolve().as_uri(),
        },
        priors=[
            {
                "name": "fibre_speed",
                "distribution": "uniform",
                "bounds": [0.02, 0.15],
                "unit": "cm/ms",
            }
        ],
        inference_backend="test-inference-backend",
        ep_backend="test-ep-backend",
        seed=42,
    )

    handoff_observation = problem["measurement_handoff"]["observations"][0]
    request = problem["inference_request"]
    likelihood = request["likelihood"][0]

    assert handoff_observation["kind"] == "ecg"
    assert request["model_service"] == "CardiEP"
    assert request["model_capability"] == "ep.simulate"
    assert likelihood["observation_ref"]["artifact_id"] == handoff_observation["artifact"]["artifact_id"]
    assert likelihood["discrepancy"] == "correlation"

    artifact_path = Path(urlparse(handoff_observation["artifact"]["uri"]).path)
    assert artifact_path.is_file()
