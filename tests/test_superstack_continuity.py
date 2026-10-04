from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from hearttwin import Observation, Provenance, load_registry, run_superstack_workflow


CANARY = "HEARTTWIN-SUPERSTACK-CANARY"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _obs(modality: str, values: dict, suffix: str) -> Observation:
    return Observation(
        observation_id=f"{CANARY}-{suffix}",
        modality=modality,
        values=values,
        subject_id=CANARY,
        timepoint_id="baseline",
        provenance=Provenance(
            source_service="superstack-canary",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id=f"source-{suffix}",
        ),
    )


def _rows(n: int = 20) -> list[dict]:
    return [
        {
            "sample_id": f"SS-S{i:03d}",
            "group_id": f"SS-G{i:03d}",
            "study_id": "SS-STUDY",
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "gene_a": float(i) / n,
            "gene_b": float((i * 7) % n) / n,
        }
        for i in range(n)
    ]


def _require_full_stack(registry) -> None:
    missing = [name for name, adapter in registry.adapters.items() if not adapter.available()]
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("full stack not installed: " + ", ".join(missing))
    assert not missing, missing


def test_one_subject_crosses_every_repo_and_is_sealed_once(tmp_path: Path) -> None:
    registry = load_registry()
    _require_full_stack(registry)

    import pandas as pd

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

    fs = 100.0
    time = np.arange(0.0, 6.0, 1.0 / fs)
    ecg = tmp_path / "ecg.csv"
    pd.DataFrame(
        {
            "time": time,
            "lead_I": np.sin(2 * np.pi * 1.0 * time),
        }
    ).to_csv(ecg, index=False)

    cv2 = pytest.importorskip("cv2")
    video = tmp_path / "motion.avi"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"MJPG"),
        20.0,
        (64, 64),
        isColor=False,
    )
    assert writer.isOpened()
    try:
        for frame_index in range(100):
            frame = np.zeros((64, 64), dtype=np.uint8)
            phase = frame_index / 20.0 * 2.0 * np.pi
            radius = int(12 + 3 * np.sin(phase))
            cv2.circle(frame, (32, 32), radius, 220, -1)
            writer.write(frame)
    finally:
        writer.release()

    image = tmp_path / "cells.png"
    pixels = np.zeros((96, 96), dtype=np.uint8)
    cv2.circle(pixels, (32, 48), 12, 180, -1)
    cv2.circle(pixels, (64, 48), 10, 220, -1)
    assert cv2.imwrite(str(image), pixels)

    from virelion_cardioscore.io.synthetic import load_synthetic_dataset

    safety = tmp_path / "mea.csv"
    load_synthetic_dataset(
        n_compounds=1,
        n_concentrations=4,
        seed=2718,
    ).features.to_csv(safety, index=False)

    edv = tmp_path / "edv.json"
    edv.write_text(json.dumps({"values": [125.0]}) + "\n", encoding="utf-8")

    from cardianatomy.models import AnatomyBundle
    from cardianatomy.pipeline import bundle_fingerprint

    common = {
        "uri": geometry.resolve().as_uri(),
        "sha256": _sha(geometry),
        "subject_id": CANARY,
        "study_id": "SS-STUDY",
        "acquisition_id": "SS-ACQ",
        "producer": "hearttwin.superstack.canary",
        "metadata": {"synthetic_integration_fixture": True},
    }
    bundle = AnatomyBundle(
        subject_id=CANARY,
        study_id="SS-STUDY",
        acquisition_id="SS-ACQ",
        artifacts=[
            {"artifact_id": "geometry-surface", "kind": "surface_mesh", **common},
            {"artifact_id": "geometry-volume", "kind": "volume_mesh", **common},
            {"artifact_id": "geometry-coordinates", "kind": "coordinate_field", **common},
            {"artifact_id": "geometry-fibres", "kind": "fiber_field", **common},
        ],
        qc={"passed": True, "checks": {"superstack_fixture": True}},
        provenance={"scope": "software integration fixture"},
    )
    bundle.bundle_fingerprint = bundle_fingerprint(bundle)

    observations = [
        _obs("molecular", {"gene_a": 0.4, "gene_b": 0.6}, "molecular"),
        _obs("structural", {"region": "left_ventricle", "zone": "IZ"}, "structural"),
        _obs("clinical", {"condition": "superstack_canary"}, "clinical"),
        _obs("electrical", {"input_path": str(ecg), "time_col": "time"}, "ecg"),
        _obs(
            "electrical",
            {
                "input_path": str(activation),
                "observation_kind": "activation_map",
                "coordinate_frame": "ep_geometry",
                "units": "ms",
                "discrepancy": "rmse",
                "specialist_analysis": False,
            },
            "ep-calibration",
        ),
        _obs(
            "mechanical",
            {"input_path": str(video), "fps": 20, "allow_qc_fail": True},
            "motion",
        ),
        _obs(
            "imaging",
            {
                "input_path": str(image),
                "cell_method": "threshold",
                "adaptive_qc": False,
            },
            "imaging",
        ),
        _obs("safety", {"input_path": str(safety)}, "safety"),
    ]
    rows = _rows()

    run = run_superstack_workflow(
        registry,
        entity_id=CANARY,
        observations=observations,
        multimodal={
            "benchmark_samples": [
                {
                    "sample_id": row["sample_id"],
                    "group_id": row["group_id"],
                    "study_id": row["study_id"],
                    "label": row["label"],
                    "region": "IZ" if row["target"] else "remote",
                }
                for row in rows
            ],
            "learning_data": rows,
            "feature_columns": ["gene_a", "gene_b"],
            "reference_labels": {
                row["sample_id"]: row["target"]
                for row in rows
            },
            "benchmark_validation_values": [
                rows[-4]["group_id"],
                rows[-3]["group_id"],
            ],
            "benchmark_test_values": [
                rows[-2]["group_id"],
                rows[-1]["group_id"],
            ],
            "simulation": {
                "preset": "mi",
                "n_cells": 12,
                "duration": 1.0,
                "dt": 0.25,
            },
            "alignment_policy": {
                "require_subject_id": True,
                "require_timepoint_id": True,
                "require_single_timepoint": True,
            },
            "seed": 314159,
        },
        cardistudio={
            "factors": {"condition": ["sham", "MI"]},
            "replicates": 1,
            "seed": 314159,
            "benchmark_factor": "condition",
        },
        dccp={
            "module_scores": {"structural_injury": 0.35},
            "challenge_axis": "structural_injury",
        },
        ep_calibration={
            "observation_id": f"{CANARY}-ep-calibration",
            "anatomy_ref": {
                "artifact_id": "geometry-surface",
                "kind": "surface_mesh",
                "uri": geometry.resolve().as_uri(),
                "sha256": _sha(geometry),
            },
            "priors": [
                {
                    "name": "fibre_speed",
                    "distribution": "uniform",
                    "bounds": [0.05, 0.15],
                    "unit": "cm/ms",
                }
            ],
            "inference_backend": "native-abc-smc-v1",
            "ep_backend": "numpy-eikonal-v1",
            "ep_settings": {"root_nodes": [0]},
            "fixed_parameters": {
                "sheet_speed": 0.05,
                "normal_speed": 0.025,
                "apd_ms": 280.0,
            },
            "sampler_settings": {
                "n_particles": 8,
                "n_generations": 1,
                "initial_oversample": 2,
                "output_dir": str(tmp_path / "ep-inference"),
            },
            "seed": 19,
            "parameter_map": {"fibre_speed": "fibre_speed"},
        },
        mechanistic={
            "anatomy_bundle": bundle.model_dump(mode="json"),
            "ep_backend": "numpy-eikonal-v1",
            "ep_parameters": {
                "fibre_speed": 0.1,
                "sheet_speed": 0.05,
                "normal_speed": 0.025,
                "apd_ms": 280.0,
            },
            "ep_parameter_units": {
                "fibre_speed": "cm/ms",
                "sheet_speed": "cm/ms",
                "normal_speed": "cm/ms",
                "apd_ms": "ms",
            },
            "ep_settings": {"root_nodes": [0]},
            "mechanics_backend": "numpy-lumped-v1",
            "mechanics_parameters": {
                "passive": {"v0_ml": 10.0, "a_mmHg": 0.08, "b": 0.055},
                "active": {"emax_mmHg_per_ml": 2.1},
                "units": {
                    "passive.a_mmHg": "mmHg",
                    "active.emax_mmHg_per_ml": "mmHg/mL",
                },
                "source": "prior",
            },
            "mechanics_calibration": {
                "observations": [
                    {
                        "observation_id": "edv",
                        "kind": "end_diastolic_volume",
                        "artifact": {
                            "artifact_id": "edv-observation",
                            "kind": "scalar",
                            "uri": edv.resolve().as_uri(),
                            "sha256": _sha(edv),
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
            "mechanics_settings": {"cycles": 4, "dt_s": 0.001},
            "flow_backend": "windkessel-3element-v1",
            "flow_fluid": {"density": 1060.0, "dynamic_viscosity": 0.0035},
            "flow_boundary_conditions": [
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
            "therapy_backend": "cardiep-pacing-v1",
            "therapy_plan": {
                "plan_id": "superstack-pace",
                "arms": [
                    {"arm_id": "control", "label": "Baseline", "is_comparator": True},
                    {
                        "arm_id": "paced",
                        "label": "Alternative root",
                        "interventions": [
                            {
                                "intervention_id": "pace-1",
                                "kind": "pacing",
                                "target": "root node 1",
                                "parameters": {"root_nodes": [1]},
                                "model_service": "CardiEP",
                                "model_capability": "ep.simulate",
                            }
                        ],
                    },
                ],
                "endpoints": ["activation_span_ms"],
            },
            "evaluation_reference_outcomes": {
                "control:activation_span_ms": 15.0,
                "paced:activation_span_ms": 15.0,
            },
            "workdir": tmp_path / "mechanistic",
        },
    )

    assert run.status == "ok"
    state = run.state.cardiac_state
    assert state is not None
    assert state.entity_id == CANARY
    assert state.state_fingerprint
    assert {item.modality for item in state.modality_analyses} == {
        "electrical",
        "mechanical",
        "imaging",
        "safety",
    }
    assert state.anatomy_bundles
    assert state.ep_artifacts
    assert len(state.posterior_artifacts) >= 2  # EP + mechanics posteriors
    assert state.mechanics_artifacts
    assert state.flow_artifacts
    assert state.therapy_artifacts
    assert state.prediction_artifacts
    assert state.simulation_artifacts
    assert state.challenges
    assert state.vex_observations
    assert state.bridge_publications
    assert len(state.evaluation_artifacts) >= 2
    assert state.trace_records

    variables = {item.variable: item.value for item in state.derived_values}
    assert variables["superstack.cardistudio_n_runs"] == 2
    assert variables["superstack.dccp.structural_injury"] == pytest.approx(0.35)
    assert variables["superstack.myotrace_cycle_length_s"] > 0
    assert variables["superstack.opticell_gate"] == "passed"
    assert variables["superstack.cardioscore_gate"]["status"] == "passed"
    assert math.isfinite(float(variables["superstack.ep_calibrated.fibre_speed"]))

    expected_services = {
        "CardiAnatomy",
        "CardiAtlas",
        "CardiBench",
        "CardiEval",
        "CardiFlow",
        "CardiLearn",
        "CardiEP",
        "CardiMech",
        "CardiInfer",
        "CardiSim",
        "CardiTherapy",
        "CardiTrace",
        "CardiBridge",
        "CardiAgent",
        "CardiVex",
        "CardiStudio",
        "DCCP",
        "ElectroTrace",
        "MyoTrace",
        "OptiCell",
        "CardioScore",
    }
    assert set(state.biological_context["superstack"]["service_contributions"]) == expected_services
    assert run.state.trace["canonical_state_verified"] is True
    assert run.state.trace["canonical_state_fingerprint"]
