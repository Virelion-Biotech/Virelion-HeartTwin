import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("cardiep")
pytest.importorskip("cardiinfer")

from hearttwin import (
    EPCalibrationWorkflowError,
    Observation,
    Provenance,
    load_registry,
    run_ep_calibration,
)


def _geometry(tmp_path: Path) -> Path:
    path = tmp_path / "hearttwin_ep_geometry.json"
    path.write_text(
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
                "ventricular_coordinates": {"tm": [0.0, 0.2, 0.6, 1.0]},
                "electrodes": {
                    "RA": [-2.0, 0.0, 0.0],
                    "LA": [2.0, 0.0, 0.0],
                    "LL": [0.0, -2.0, 0.0],
                    "V1": [0.2, 2.0, 0.0],
                    "V2": [0.5, 2.0, 0.0],
                    "V3": [0.8, 2.0, 0.0],
                    "V4": [1.1, 2.0, 0.0],
                    "V5": [1.4, 2.0, 0.0],
                    "V6": [1.7, 2.0, 0.0],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_hearttwin_routes_full_cardiep_engine(tmp_path: Path) -> None:
    registry = load_registry()

    backends = registry.capability("ep.backends")
    assert backends is not None
    backend_payload = backends.invoke("ep.backends", {})
    status = {item["name"]: item for item in backend_payload["backends"]}
    assert status["numpy-eikonal-v1"]["available"] is True

    reference = registry.capability("ep.validate.reference")
    assert reference is not None
    reference_payload = reference.invoke("ep.validate.reference", {})
    assert reference_payload["passed"] is True

    geometry = _geometry(tmp_path)
    simulator = registry.capability("ep.simulate")
    assert simulator is not None
    result = simulator.invoke(
        "ep.simulate",
        {
            "subject_id": "HT-EP-1",
            "anatomy_ref": {
                "artifact_id": "ht-ep-geometry",
                "kind": "ep_geometry",
                "uri": geometry.as_uri(),
            },
            "backend": "numpy-eikonal-v1",
            "parameters": {
                "values": {
                    "fibre_speed": 0.1,
                    "sheet_speed": 0.05,
                    "normal_speed": 0.025,
                    "apd_min_ms": 250.0,
                    "apd_max_ms": 300.0,
                    "apd_gradient_tm": 1.0,
                },
                "source": "fixed",
            },
            "settings": {
                "output_dir": str(tmp_path / "ep-output"),
                "ecg_sample_rate_hz": 250.0,
            },
        },
    )

    assert result["backend"] == "numpy-eikonal-v1"
    assert result["subject_id"] == "HT-EP-1"
    assert result["validation_status"] == "software_checked"

    kinds = {item["kind"] for item in result["outputs"]}
    assert {"activation_map", "repolarization_map", "pseudo_ecg", "ep_summary"} <= kinds
    for artifact in result["outputs"]:
        assert len(artifact["sha256"]) == 64
        assert Path(urlparse(artifact["uri"]).path).is_file()


def test_cardiep_ecosystem_is_discoverable_through_hearttwin() -> None:
    adapter = load_registry().capability("ep.ecosystem")
    assert adapter is not None
    payload = adapter.invoke("ep.ecosystem", {})
    projects = {item["project"] for item in payload["external_projects"]}
    assert {"Cardiac-Digital-Twin", "fenicsx-beat", "MonoAlg3D_C", "openCARP"} <= projects


def test_hearttwin_runs_cardiep_abc_inverse_loop(tmp_path: Path) -> None:
    registry = load_registry()
    geometry = _geometry(tmp_path)

    observed = tmp_path / "observed_activation.json"
    observed.write_text(
        json.dumps({"values_ms": [0.0, 10.0, 20.0, 40.0]}) + "\n",
        encoding="utf-8",
    )
    observation = {
        "observation_id": "lat",
        "kind": "activation_map",
        "artifact": {
            "artifact_id": "observed-lat",
            "kind": "activation_map",
            "uri": observed.as_uri(),
        },
        "units": "ms",
    }
    anatomy_ref = {
        "artifact_id": "ht-ep-geometry",
        "kind": "ep_geometry",
        "uri": geometry.as_uri(),
    }
    model_context = {
        "ep_backend": "numpy-eikonal-v1",
        "anatomy_ref": anatomy_ref,
        "ep_observations": [observation],
        "ep_settings": {"root_nodes": [0]},
        "fixed_parameters": {
            "sheet_speed": 0.05,
            "normal_speed": 0.025,
            "apd_ms": 280.0,
        },
    }

    backends = registry.capability("infer.backends")
    assert backends is not None
    backend_payload = backends.invoke("infer.backends", {})
    backend_status = {item["name"]: item for item in backend_payload["backends"]}
    assert backend_status["cardiep-abc-rejection-v1"]["available"] is True

    inference = registry.capability("infer.run")
    assert inference is not None
    result = inference.invoke(
        "infer.run",
        {
            "subject_id": "HT-EP-ABC-1",
            "model_service": "CardiEP",
            "model_capability": "ep.simulate",
            "backend": "cardiep-abc-rejection-v1",
            "priors": [
                {
                    "name": "fibre_speed",
                    "distribution": "uniform",
                    "bounds": [0.05, 0.15],
                    "unit": "cm/ms",
                }
            ],
            "likelihood": [
                {
                    "term_id": "lat:activation",
                    "observation_ref": observation["artifact"],
                    "model_output": "activation_map",
                    "discrepancy": "rmse",
                    "weight": 1.0,
                    "metadata": {"observation_id": "lat"},
                }
            ],
            "model_context": model_context,
            "sampler_settings": {
                "n_samples": 32,
                "acceptance_fraction": 0.125,
                "min_accept": 4,
                "output_dir": str(tmp_path / "posterior"),
            },
            "seed": 42,
        },
    )

    assert result["backend"] == "cardiep-abc-rejection-v1"
    assert result["validation_status"] == "software_checked"
    assert result["convergence"]["converged"] is None
    assert result["convergence"]["effective_sample_size_min"] is None
    assert result["diagnostics"]["accepted_particle_count_is_ess"] is False
    assert result["diagnostics"]["n_accepted"] == 4
    assert result["diagnostics"]["best_objective"] < 1.0
    posterior = {item["parameter"]: item for item in result["posterior"]}
    assert abs(posterior["fibre_speed"]["median"] - 0.1) < 0.02
    posterior_artifact = result["posterior_samples"]
    assert posterior_artifact is not None
    assert Path(urlparse(posterior_artifact["uri"]).path).is_file()

    propagate = registry.capability("infer.propagate")
    assert propagate is not None
    propagated = propagate.invoke(
        "infer.propagate",
        {
            "subject_id": "HT-EP-ABC-1",
            "backend": "cardiep-abc-rejection-v1",
            "model_service": "CardiEP",
            "model_capability": "ep.simulate",
            "posterior_samples": posterior_artifact,
            "outputs": ["activation_span_ms", "apd_mean_ms"],
            "model_context": model_context,
            "settings": {
                "max_samples": 4,
                "output_dir": str(tmp_path / "propagation"),
            },
        },
    )
    assert propagated["backend"] == "cardiep-abc-rejection-v1"
    assert propagated["diagnostics"]["n_samples"] == 4
    assert set(propagated["output_summaries"]) == {
        "activation_span_ms",
        "apd_mean_ms",
    }
    assert Path(urlparse(propagated["samples"][0]["uri"]).path).is_file()



@pytest.mark.skipif(
    shutil.which("electrotrace-hearttwin") is None,
    reason="ElectroTrace HeartTwin adapter is not installed",
)
def test_high_level_ep_calibration_runs_electrotrace_to_posterior(tmp_path: Path) -> None:
    registry = load_registry()
    geometry = _geometry(tmp_path)
    geometry_sha = hashlib.sha256(geometry.read_bytes()).hexdigest()

    observed = tmp_path / "measured_activation.json"
    observed.write_text(
        json.dumps({"values_ms": [0.0, 10.0, 20.0, 40.0]}) + "\n",
        encoding="utf-8",
    )
    electrical = Observation(
        observation_id="clinical-map",
        modality="electrical",
        values={
            "input_path": str(observed),
            "observation_kind": "activation_map",
            "coordinate_frame": "cardiac_mesh",
            "units": "ms",
            "discrepancy": "rmse",
            "weight": 1.0,
        },
        provenance=Provenance(
            source_service="fixture",
            run_id="fixture-run",
        ),
    )

    result = run_ep_calibration(
        registry,
        entity_id="HT-HIGHLEVEL-1",
        electrical_observation=electrical,
        anatomy_ref={
            "artifact_id": "highlevel-geometry",
            "kind": "ep_geometry",
            "uri": geometry.as_uri(),
            "sha256": geometry_sha,
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
            "output_dir": str(tmp_path / "highlevel-posterior"),
        },
        seed=42,
    )

    inference = result["inference_result"]
    assert inference["subject_id"] == "HT-HIGHLEVEL-1"
    assert inference["backend"] == "cardiep-abc-rejection-v1"
    assert inference["diagnostics"]["best_objective"] < 1.0
    assert inference["convergence"]["converged"] is None
    assert result["measurement_handoff"]["observations"][0]["kind"] == "activation_map"
    assert len(result["problem_sha256"]) == 64
    assert len(result["result_sha256"]) == 64
    posterior = inference["posterior_samples"]
    assert posterior is not None
    assert Path(urlparse(posterior["uri"]).path).is_file()


@pytest.mark.skipif(
    shutil.which("electrotrace-hearttwin") is None,
    reason="ElectroTrace HeartTwin adapter is not installed",
)
def test_high_level_ep_calibration_wraps_electrotrace_failures(tmp_path: Path) -> None:
    bad_map = tmp_path / "map.json"
    bad_map.write_text("{}\n", encoding="utf-8")
    electrical = Observation(
        observation_id="bad-map",
        modality="electrical",
        values={
            "input_path": str(bad_map),
            "observation_kind": "activation_map",
            "units": "ms",
        },
        provenance=Provenance(source_service="fixture", run_id="fixture-run"),
    )

    with pytest.raises(
        EPCalibrationWorkflowError,
        match="ElectroTrace EP calibration preparation failed",
    ):
        run_ep_calibration(
            load_registry(),
            entity_id="HT-BAD",
            electrical_observation=electrical,
            anatomy_ref={
                "artifact_id": "unused",
                "kind": "ep_geometry",
                "uri": bad_map.as_uri(),
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
