from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from hearttwin import load_registry, run_mechanistic_twin_workflow


pytestmark = pytest.mark.skipif(
    os.getenv("HEARTTWIN_CURRENT_MAIN") != "1",
    reason="mechanistic pipe requires current component main branches",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_one_artifact_chain_crosses_ep_infer_mechanics_flow_therapy_trace(tmp_path: Path):
    pytest.importorskip("cardianatomy")
    pytest.importorskip("cardiep")
    pytest.importorskip("cardiinfer")
    pytest.importorskip("cardimech")
    pytest.importorskip("cardiflow")
    pytest.importorskip("carditherapy")

    surface = tmp_path / "surface.json"
    surface.write_text(
        json.dumps(
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
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_sha = _sha(surface)

    from cardianatomy.models import AnatomyBundle
    from cardianatomy.pipeline import bundle_fingerprint

    common = {
        "uri": surface.resolve().as_uri(),
        "sha256": source_sha,
        "subject_id": "PIPE-001",
        "study_id": "PIPE-STUDY",
        "acquisition_id": "PIPE-ACQ",
        "producer": "hearttwin.integration.fixture",
        "metadata": {"synthetic_integration_fixture": True},
    }
    bundle = AnatomyBundle(
        subject_id="PIPE-001",
        study_id="PIPE-STUDY",
        acquisition_id="PIPE-ACQ",
        artifacts=[
            {"artifact_id": "surface", "kind": "surface_mesh", **common},
            {"artifact_id": "volume", "kind": "volume_mesh", **common},
            {"artifact_id": "coordinates", "kind": "coordinate_field", **common},
            {"artifact_id": "fibres", "kind": "fiber_field", **common},
        ],
        qc={"passed": True, "checks": {"integration_fixture": True}},
        provenance={"scope": "software integration fixture"},
    )
    bundle.bundle_fingerprint = bundle_fingerprint(bundle)

    observation = tmp_path / "edv.json"
    observation.write_text(json.dumps({"values": [125.0]}) + "\n", encoding="utf-8")

    run = run_mechanistic_twin_workflow(
        load_registry(),
        entity_id="PIPE-001",
        anatomy_bundle=bundle.model_dump(mode="json"),
        ep_backend="surface-eikonal-v1",
        ep_parameters={"isotropic_speed_cm_per_ms": 0.1},
        ep_parameter_units={"isotropic_speed_cm_per_ms": "cm/ms"},
        ep_settings={"root_node": 0},
        mechanics_backend="numpy-lumped-v1",
        mechanics_parameters={
            "passive": {"v0_ml": 10.0, "a_mmHg": 0.08, "b": 0.055},
            "active": {"emax_mmHg_per_ml": 2.1},
            "units": {
                "passive.a_mmHg": "mmHg",
                "active.emax_mmHg_per_ml": "mmHg/mL",
            },
            "source": "prior",
        },
        mechanics_calibration={
            "observations": [
                {
                    "observation_id": "edv",
                    "kind": "end_diastolic_volume",
                    "artifact": {
                        "artifact_id": "edv-observation",
                        "kind": "scalar",
                        "uri": observation.resolve().as_uri(),
                        "sha256": _sha(observation),
                    },
                    "unit": "mL",
                }
            ],
            "parameter_bounds": {
                "passive.a_mmHg": [0.06, 0.10],
                "active.emax_mmHg_per_ml": [1.8, 2.4],
            },
            "circulation": {
                "enabled": True,
                "model": "windkessel_3e",
                "parameters": {},
            },
            "settings": {
                "discrepancy": "rmse",
                "inference_backend": "native-abc-smc-v1",
                "sampler_settings": {
                    "n_particles": 8,
                    "n_generations": 1,
                    "initial_oversample": 2,
                },
                "seed": 20261004,
            },
        },
        mechanics_settings={"cycles": 4, "dt_s": 0.002},
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
        therapy_plan={
            "plan_id": "pace",
            "arms": [
                {"arm_id": "control", "label": "Baseline", "is_comparator": True},
                {
                    "arm_id": "paced",
                    "label": "Alternative root",
                    "interventions": [
                        {
                            "intervention_id": "pace-2",
                            "kind": "pacing",
                            "target": "surface vertex 2",
                            "parameters": {"root_node": 2},
                            "model_service": "CardiEP",
                            "model_capability": "ep.simulate",
                        }
                    ],
                },
            ],
            "endpoints": ["activation_span_ms"],
        },
        workdir=tmp_path / "run",
    )

    state = run.state.cardiac_state
    assert run.status == "ok"
    assert state is not None
    assert len(state.ep_artifacts) == 1
    assert len(state.posterior_artifacts) == 1
    assert len(state.mechanics_artifacts) == 1
    assert len(state.flow_artifacts) == 1
    assert len(state.therapy_artifacts) == 1
    assert state.validation_gates[-1].passed is True
    assert state.trace_records
    assert (
        state.ep_artifacts[0].anatomy_bundle_fingerprint
        == bundle.bundle_fingerprint
        == state.mechanics_artifacts[0].anatomy_bundle_fingerprint
    )
    activation = next(
        item for item in run.state.ep.outputs if item["kind"] == "activation_map"
    )
    mechanics = next(
        item for item in run.state.mechanics.outputs if item["kind"] == "mechanics_timeseries"
    )
    assert state.mechanics_artifacts[0].activation_artifact_id == activation["artifact_id"]
    assert state.mechanics_artifacts[0].activation_sha256 == activation["sha256"]
    assert run.state.flow.provenance["mechanics_artifact_id"] == mechanics["artifact_id"]
    assert run.state.flow.provenance["mechanics_sha256"] == mechanics["sha256"]
    assert run.state.flow.provenance["coupling_mode"] == "mechanics_aortic_flow"
    assert run.state.therapy.provenance["posterior_sha256"] == (
        state.posterior_artifacts[0].posterior_samples["sha256"]
    )
    json.dumps(state.model_dump(mode="json"), allow_nan=False)
