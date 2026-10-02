import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pytest

pytest.importorskip("electrotrace")
pytest.importorskip("cardiep")
pytest.importorskip("cardiinfer")

from hearttwin import (
    Observation,
    Provenance,
    load_registry,
    prepare_ep_inference_problem,
    run_ep_calibration,
)


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



def test_generic_abc_smc_runs_native_cardiep_through_hearttwin(tmp_path: Path) -> None:
    geometry = tmp_path / "geometry.json"
    geometry.write_text(
        json.dumps(
            {
                "units": "cm",
                "node_xyz": [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                "tetrahedra": [[0, 1, 2, 3]],
                "fibre": [[1.0, 0.0, 0.0]] * 4,
                "sheet": [[0.0, 1.0, 0.0]] * 4,
                "normal": [[0.0, 0.0, 1.0]] * 4,
                "root_nodes": [0],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    activation = tmp_path / "activation.json"
    activation.write_text(
        json.dumps({"values_ms": [0.0, 10.0, 20.0, 40.0]}) + "\n",
        encoding="utf-8",
    )
    observation = Observation(
        observation_id="measured-activation",
        modality="electrical",
        values={
            "input_path": str(activation),
            "observation_kind": "activation_map",
            "coordinate_frame": "ep_geometry",
            "units": "ms",
            "discrepancy": "rmse",
        },
        provenance=Provenance(
            source_service="test",
            run_id="activation-map-run",
        ),
    )

    run = run_ep_calibration(
        load_registry(),
        entity_id="S-generic-abc",
        electrical_observation=observation,
        anatomy_ref={
            "artifact_id": "geometry",
            "kind": "ep_geometry",
            "uri": geometry.resolve().as_uri(),
        },
        priors=[
            {
                "name": "fibre_speed",
                "distribution": "uniform",
                "bounds": [0.05, 0.15],
                "unit": "cm/ms",
            }
        ],
        inference_backend="native-abc-smc-v1",
        ep_backend="numpy-eikonal-v1",
        ep_settings={"root_nodes": [0]},
        fixed_parameters={
            "sheet_speed": 0.05,
            "normal_speed": 0.025,
            "apd_ms": 280.0,
        },
        sampler_settings={
            "n_particles": 8,
            "n_generations": 1,
            "initial_oversample": 2,
            "output_dir": str(tmp_path / "posterior"),
        },
        seed=19,
    )

    result = run["inference_result"]
    assert result["contract_version"] == "1.1"
    assert result["backend"] == "native-abc-smc-v1"
    assert result["model_service"] == "CardiEP"
    assert result["model_capability"] == "ep.simulate"
    assert result["provenance"]["forward_transport"] == "cardiep-native-v1"
    assert result["posterior_samples"] is not None
    assert len(result["posterior_samples"]["sha256"]) == 64
    assert result["diagnostics"]["n_forward_evaluations"] == 16
