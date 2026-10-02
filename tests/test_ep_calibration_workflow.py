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
from hearttwin.ep_calibration import EPCalibrationWorkflowError


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



class _WrongSubjectAdapter:
    def available(self) -> bool:
        return True

    def invoke(self, capability: str, payload: dict) -> dict:
        assert capability == "electrical.prepare_calibration"
        return {
            "contract_version": "1.0",
            "schema_version": "electrotrace-ep-calibration-v1",
            "entity_id": "OTHER-SUBJECT",
            "observations": [],
        }


class _WrongSubjectRegistry:
    def capability(self, capability: str):
        if capability == "electrical.prepare_calibration":
            return _WrongSubjectAdapter()
        return None


def _activation_observation(path: Path, *, entity: str = "S1") -> Observation:
    return Observation(
        observation_id=f"{entity}-activation-source",
        modality="electrical",
        values={
            "input_path": str(path),
            "observation_kind": "activation_map",
            "coordinate_frame": "mesh-node-order",
            "units": "ms",
            "discrepancy": "rmse",
        },
        provenance=Provenance(
            source_service="test",
            source_repository="local",
            source_version="1",
            run_id=f"{entity}-source-run",
        ),
    )


def _ep_geometry(tmp_path: Path) -> Path:
    path = tmp_path / "geometry.json"
    path.write_text(
        __import__("json").dumps(
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
    return path


def test_prepare_ep_problem_rejects_cross_subject_handoff(tmp_path: Path) -> None:
    observed = tmp_path / "activation.json"
    observed.write_text('{"values_ms":[0,10,20,40]}\n', encoding="utf-8")

    with pytest.raises(EPCalibrationWorkflowError, match="does not match"):
        prepare_ep_inference_problem(
            _WrongSubjectRegistry(),
            entity_id="S1",
            electrical_observation=_activation_observation(observed),
            anatomy_ref={
                "artifact_id": "geometry",
                "kind": "ep_geometry",
                "uri": (tmp_path / "geometry.json").as_uri(),
            },
            priors=[
                {
                    "name": "fibre_speed",
                    "distribution": "uniform",
                    "bounds": [0.05, 0.15],
                }
            ],
            inference_backend="cardiep-abc-rejection-v1",
            ep_backend="numpy-eikonal-v1",
        )


def test_high_level_ep_calibration_runs_electrotrace_to_posterior(tmp_path: Path) -> None:
    observed = tmp_path / "activation.json"
    observed.write_text(
        '{"values_ms":[0.0,10.0,20.0,40.0]}\n',
        encoding="utf-8",
    )
    geometry = _ep_geometry(tmp_path)

    result = run_ep_calibration(
        load_registry(),
        entity_id="S1",
        electrical_observation=_activation_observation(observed),
        anatomy_ref={
            "artifact_id": "geometry",
            "kind": "ep_geometry",
            "uri": geometry.as_uri(),
        },
        priors=[
            {
                "name": "fibre_speed",
                "distribution": "uniform",
                "bounds": [0.05, 0.15],
                "unit": "cm/ms",
            }
        ],
        inference_backend="cardiep-abc-rejection-v1",
        ep_backend="numpy-eikonal-v1",
        ep_settings={"root_nodes": [0]},
        fixed_parameters={
            "sheet_speed": 0.05,
            "normal_speed": 0.025,
            "apd_ms": 280.0,
        },
        sampler_settings={
            "n_samples": 32,
            "acceptance_fraction": 0.125,
            "min_accept": 4,
            "output_dir": str(tmp_path / "posterior"),
        },
        seed=42,
    )

    assert result["measurement_handoff"]["entity_id"] == "S1"
    inference = result["inference_result"]
    assert inference["backend"] == "cardiep-abc-rejection-v1"
    assert inference["posterior_samples"]["sha256"]
    assert inference["convergence"]["converged"] is None
    assert len(result["problem_sha256"]) == 64
    assert len(result["result_sha256"]) == 64

    observation_artifact = result["measurement_handoff"]["observations"][0]["artifact"]
    likelihood_artifact = result["inference_request"]["likelihood"][0]["observation_ref"]
    assert likelihood_artifact["artifact_id"] == observation_artifact["artifact_id"]
    assert likelihood_artifact["sha256"] == observation_artifact["sha256"]

    posterior = {item["parameter"]: item for item in inference["posterior"]}
    assert abs(posterior["fibre_speed"]["median"] - 0.1) < 0.025
