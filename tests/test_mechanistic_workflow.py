from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import jsonschema
import pytest

from hearttwin import load_registry, run_mechanistic_twin_workflow


def _require_full_stack(registry) -> None:
    required = {
        "CardiAnatomy",
        "CardiEP",
        "CardiInfer",
        "CardiMech",
        "CardiFlow",
        "CardiTherapy",
        "CardiEval",
        "CardiTrace",
    }
    missing = sorted(
        name for name in required if not registry.adapters[name].available()
    )
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("mechanistic stack not installed: " + ", ".join(missing))
    assert not missing


def _write_json(path: Path, payload: dict) -> tuple[str, str]:
    path.write_text(
        json.dumps(payload, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path.resolve().as_uri(), hashlib.sha256(path.read_bytes()).hexdigest()


def test_one_canary_crosses_the_complete_mechanistic_pipe(tmp_path: Path) -> None:
    registry = load_registry()
    _require_full_stack(registry)

    subject = "HEARTTWIN-MECHANISTIC-CANARY"

    surface_uri, surface_sha = _write_json(
        tmp_path / "surface.json",
        {
            "schema_version": "cardiep-surface-v1",
            "coordinate_unit": "cm",
            "vertices": [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            "triangles": [[0, 1, 2], [0, 2, 3]],
        },
    )
    volume_uri, volume_sha = _write_json(
        tmp_path / "volume.json",
        {"schema_version": "hearttwin-canary-volume-v1", "subject_id": subject},
    )
    coordinates_uri, coordinates_sha = _write_json(
        tmp_path / "coordinates.json",
        {"schema_version": "hearttwin-canary-coordinates-v1", "subject_id": subject},
    )
    fibers_uri, fibers_sha = _write_json(
        tmp_path / "fibers.json",
        {"schema_version": "hearttwin-canary-fibers-v1", "subject_id": subject},
    )
    observation_uri, observation_sha = _write_json(
        tmp_path / "edv.json",
        {"values": [125.0]},
    )

    anatomy_bundle = {
        "contract_version": "2.0.0",
        "subject_id": subject,
        "study_id": "HT-CANARY-STUDY",
        "acquisition_id": "HT-CANARY-ACQ",
        "artifacts": [
            {
                "artifact_id": "canary-surface",
                "kind": "surface_mesh",
                "uri": surface_uri,
                "sha256": surface_sha,
                "frame_id": "canary-frame",
                "subject_id": subject,
                "study_id": "HT-CANARY-STUDY",
                "acquisition_id": "HT-CANARY-ACQ",
            },
            {
                "artifact_id": "canary-volume",
                "kind": "volume_mesh",
                "uri": volume_uri,
                "sha256": volume_sha,
                "frame_id": "canary-frame",
                "subject_id": subject,
                "study_id": "HT-CANARY-STUDY",
                "acquisition_id": "HT-CANARY-ACQ",
            },
            {
                "artifact_id": "canary-coordinates",
                "kind": "coordinate_field",
                "uri": coordinates_uri,
                "sha256": coordinates_sha,
                "frame_id": "canary-frame",
                "subject_id": subject,
                "study_id": "HT-CANARY-STUDY",
                "acquisition_id": "HT-CANARY-ACQ",
            },
            {
                "artifact_id": "canary-fibers",
                "kind": "fiber_field",
                "uri": fibers_uri,
                "sha256": fibers_sha,
                "frame_id": "canary-frame",
                "subject_id": subject,
                "study_id": "HT-CANARY-STUDY",
                "acquisition_id": "HT-CANARY-ACQ",
            },
        ],
        "frames": [
            {
                "frame_id": "canary-frame",
                "convention": "MODEL",
                "units": "cm",
            }
        ],
        "qc": {
            "passed": True,
            "checks": {"integration_canary": True},
            "metrics": {},
            "warnings": [],
            "errors": [],
        },
        "provenance": {"fixture": "HeartTwin mechanistic continuity canary"},
    }

    mechanics_parameters = {
        "passive": {"v0_ml": 10.0, "a_mmHg": 0.08, "b": 0.055},
        "active": {"emax_mmHg_per_ml": 2.1},
        "units": {
            "passive.a_mmHg": "mmHg",
            "active.emax_mmHg_per_ml": "mmHg/mL",
        },
        "source": "prior",
    }
    mechanics_calibration = {
        "observations": [
            {
                "observation_id": "canary-edv",
                "kind": "end_diastolic_volume",
                "artifact": {
                    "artifact_id": "canary-edv-observation",
                    "kind": "scalar",
                    "uri": observation_uri,
                    "sha256": observation_sha,
                },
                "unit": "mL",
            }
        ],
        "parameter_bounds": {
            "passive.a_mmHg": [0.04, 0.12],
            "active.emax_mmHg_per_ml": [1.5, 2.8],
        },
        "settings": {
            "discrepancy": "rmse",
            "inference_backend": "native-abc-smc-v1",
            "sampler_settings": {
                "n_particles": 8,
                "n_generations": 1,
                "initial_oversample": 1,
            },
            "seed": 20261004,
        },
    }

    therapy_plan = {
        "plan_id": "canary-pacing",
        "arms": [
            {
                "arm_id": "control",
                "label": "Baseline root 0",
                "is_comparator": True,
            },
            {
                "arm_id": "paced",
                "label": "Paced root 1",
                "interventions": [
                    {
                        "intervention_id": "pace-root-1",
                        "kind": "pacing",
                        "target": "surface vertex 1",
                        "parameters": {"root_node": 1},
                        "model_service": "CardiEP",
                        "model_capability": "ep.simulate",
                    }
                ],
            },
        ],
        "endpoints": ["activation_span_ms"],
    }

    run = run_mechanistic_twin_workflow(
        registry,
        entity_id=subject,
        anatomy_bundle=anatomy_bundle,
        ep_backend="surface-eikonal-v1",
        ep_parameters={"isotropic_speed_cm_per_ms": 0.1},
        ep_parameter_units={"isotropic_speed_cm_per_ms": "cm/ms"},
        ep_settings={"root_node": 0},
        mechanics_backend="numpy-lumped-v1",
        mechanics_parameters=mechanics_parameters,
        mechanics_calibration=mechanics_calibration,
        mechanics_settings={
            "cycles": 4,
            "dt_s": 0.002,
            "cycle_length_s": 0.8,
        },
        flow_backend="windkessel-3element-v1",
        flow_fluid={"density": 1060.0, "dynamic_viscosity": 0.0035},
        flow_boundary_conditions=[
            {
                "boundary_id": "afterload",
                "kind": "windkessel",
                "region": "aorta",
                "parameters": {
                    "proximal_resistance": 1.0,
                    "distal_resistance": 4.0,
                    "compliance": 0.5,
                },
            }
        ],
        therapy_backend="cardiep-pacing-v1",
        therapy_plan=therapy_plan,
        evaluation_reference_outcomes={
            "control:activation_span_ms": math.sqrt(2.0) / 0.1,
            "paced:activation_span_ms": 2.0 / 0.1,
        },
        workdir=tmp_path / "run",
    )

    assert run.status == "ok"
    capabilities = [step.capability for step in run.steps]
    for required in (
        "ep.simulate",
        "mechanics.prepare_calibration",
        "infer.run",
        "mechanics.simulate",
        "flow.simulate",
        "therapy.run",
        "evaluation.run",
        "trace.record",
    ):
        assert required in capabilities
    assert capabilities.count("anatomy.validate") == 3

    state = run.state.cardiac_state
    assert state is not None
    assert state.entity_id == subject
    assert state.state_phase == "evaluated"
    assert len(state.anatomy_bundles) == 1
    assert len(state.ep_artifacts) == 1
    assert len(state.posterior_artifacts) == 1
    assert len(state.mechanics_artifacts) == 1
    assert len(state.flow_artifacts) == 1
    assert len(state.therapy_artifacts) == 1
    assert len(state.evaluation_artifacts) == 1
    assert len(state.validation_gates) >= 2
    assert state.trace_records

    assert run.state.ep is not None
    activation = next(
        item for item in run.state.ep.outputs if item["kind"] == "activation_map"
    )
    ep_artifact = state.ep_artifacts[0]
    mechanics_artifact = state.mechanics_artifacts[0]
    flow_artifact = state.flow_artifacts[0]
    therapy_artifact = state.therapy_artifacts[0]

    assert ep_artifact.anatomy_bundle_fingerprint
    assert (
        state.anatomy_bundles[0].bundle_fingerprint
        == ep_artifact.anatomy_bundle_fingerprint
        == mechanics_artifact.anatomy_bundle_fingerprint
        == flow_artifact.anatomy_bundle_fingerprint
    )
    assert mechanics_artifact.activation_artifact_id == activation["artifact_id"]
    assert mechanics_artifact.activation_sha256 == activation["sha256"]

    assert run.state.mechanics is not None
    mechanics_timeseries = next(
        item
        for item in run.state.mechanics.outputs
        if item["kind"] == "mechanics_timeseries"
    )
    assert run.state.flow is not None
    assert run.state.flow.provenance["coupling_mode"] == "mechanics_aortic_flow"
    assert flow_artifact.coupling_mode == "mechanics_aortic_flow"
    assert flow_artifact.mechanics_artifact_id == mechanics_timeseries["artifact_id"]
    assert flow_artifact.mechanics_sha256 == mechanics_timeseries["sha256"]
    assert (
        flow_artifact.anatomy_bundle_fingerprint
        == ep_artifact.anatomy_bundle_fingerprint
    )

    assert run.state.inference is not None
    assert therapy_artifact.twin_state_artifact_id == f"{subject}-cardiac-state"
    assert therapy_artifact.twin_state_sha256
    assert therapy_artifact.twin_state_fingerprint
    assert therapy_artifact.posterior_artifact_id is not None
    assert (
        therapy_artifact.posterior_sha256
        == run.state.inference.posterior_samples["sha256"]
    )

    assert run.state.evaluation is not None
    assert run.state.evaluation.ground_truth_source == "benchmark_manifest"
    assert run.state.evaluation.primary_metric == "rmse"
    assert run.state.evaluation.primary_value == pytest.approx(0.0, abs=1e-10)

    prepared_step = next(
        step
        for step in run.steps
        if step.capability == "mechanics.prepare_calibration"
    )
    prepared_settings = prepared_step.data["model_context"]["cardimech_request"]["settings"]
    assert prepared_settings["cycles"] == 4
    assert prepared_settings["dt_s"] == pytest.approx(0.002)
    assert prepared_settings["cycle_length_s"] == pytest.approx(0.8)
    assert run.state.mechanics.provenance["settings"]["cycles"] == 4
    assert run.state.mechanics.provenance["settings"]["dt_s"] == pytest.approx(0.002)
    assert run.state.mechanics.provenance["settings"]["cycle_length_s"] == pytest.approx(0.8)

    payload = state.model_dump(mode="json")
    json.dumps(payload, allow_nan=False)
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "cardiac-state-1.3.0.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(payload, schema)
