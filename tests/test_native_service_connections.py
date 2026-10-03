from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hearttwin import load_registry


NATIVE_SERVICES = {
    "CardiAnatomy",
    "CardiAtlas",
    "CardiBench",
    "CardiEval",
    "CardiFlow",
    "CardiLearn",
    "CardiSim",
    "CardiVex",
    "CardiStudio",
    "CardiTherapy",
    "DCCP",
}


def _require_native(registry) -> None:
    missing = sorted(name for name in NATIVE_SERVICES if not registry.adapters[name].available())
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("native component packages not installed: " + ", ".join(missing))
    assert not missing, "native component packages not installed: " + ", ".join(missing)


def test_all_endpoint_only_services_have_native_connections() -> None:
    registry = load_registry()
    specs = {spec.name: spec for spec in registry.services()}
    assert NATIVE_SERVICES <= set(specs)
    for name in sorted(NATIVE_SERVICES):
        assert specs[name].builtin is not None
    _require_native(registry)


def test_cardistudio_native_smoke() -> None:
    registry = load_registry()
    _require_native(registry)
    result = registry.capability("design.generate").invoke(
        "design.generate",
        {"entity_id": "smoke", "factors": {"condition": ["sham", "MI"]}, "replicates": 1},
    )
    assert result["n_runs"] == 2
    assert len(result["design"]) == 2


def test_cardibench_native_smoke() -> None:
    registry = load_registry()
    _require_native(registry)
    samples = [
        {
            "sample_id": f"S{i}",
            "group_id": f"G{i}",
            "study_id": "ST1",
            "label": "MI" if i % 2 else "sham",
        }
        for i in range(6)
    ]
    result = registry.capability("benchmark.resolve").invoke(
        "benchmark.resolve",
        {"entity_id": "smoke", "samples": samples, "seed": 1},
    )
    assert result["sample_count"] == 6
    assert set(result["assignments"]) == {item["sample_id"] for item in samples}


def test_cardibench_intelligence_control_plane_smoke() -> None:
    registry = load_registry()
    _require_native(registry)

    health = registry.capability("benchmark.health").invoke("benchmark.health", {})
    expected = {
        "benchmark.health",
        "benchmark.resolve",
        "benchmark.search",
        "benchmark.catalog",
        "benchmark.discover",
        "benchmark.admission.assess",
        "benchmark.result.record",
        "benchmark.results",
    }
    assert expected <= set(health["capabilities"])

    records = [
        {
            "record_id": "cbx-hearttwin-smoke",
            "canonical_name": "Myocardial infarction cardiac benchmark",
            "record_type": "benchmark",
            "aliases": ["MI cardiac benchmark"],
            "identifiers": {"doi": "10.1000/hearttwin-smoke"},
            "observation_ids": ["obs-hearttwin-smoke"],
            "sources": ["crossref"],
            "evidence_state": "verified",
            "metadata": {"fixture": True},
        }
    ]
    search = registry.capability("benchmark.search").invoke(
        "benchmark.search",
        {"query": "myocardial infarction benchmark", "records": records},
    )
    assert search["full_match_count"] == 1
    assert search["results"][0]["record"]["record_id"] == "cbx-hearttwin-smoke"

    catalog = registry.capability("benchmark.catalog").invoke(
        "benchmark.catalog",
        {"records": records, "record_type": "benchmark"},
    )
    assert catalog["count"] == 1
    assert catalog["records"][0]["evidence_state"] == "verified"


    admission_samples = [
        {"sample_id": "A1", "group_id": "G1", "study_id": "ST1", "label": "reference"},
        {"sample_id": "A2", "group_id": "G2", "study_id": "ST1", "label": "myocardial_injury"},
        {"sample_id": "A3", "group_id": "G3", "study_id": "ST1", "label": "reference"},
        {"sample_id": "A4", "group_id": "G4", "study_id": "ST1", "label": "myocardial_injury"},
        {"sample_id": "A5", "group_id": "G5", "study_id": "ST1", "label": "reference"},
        {"sample_id": "A6", "group_id": "G6", "study_id": "ST1", "label": "myocardial_injury"},
    ]
    admission = registry.capability("benchmark.admission.assess").invoke(
        "benchmark.admission.assess",
        {
            "samples": admission_samples,
            "benchmark_id": "hearttwin-admission-smoke",
            "test_values": ["G1", "G2"],
            "validation_values": ["G3", "G4"],
            "seed": 7,
        },
    )
    assert admission["status"] == "ready_for_review"
    assert admission["ready_for_review"] is True
    assert admission["materialization_preview"]["sample_count"] == 6

    blocked_samples = [dict(item) for item in admission_samples]
    blocked_samples[0]["group_id"] = ""
    blocked = registry.capability("benchmark.admission.assess").invoke(
        "benchmark.admission.assess",
        {"samples": blocked_samples, "benchmark_id": "blocked-smoke"},
    )
    assert blocked["status"] == "blocked"
    assert blocked["ready_for_review"] is False
    assert "group_id" in blocked["missing_by_field"]


def test_cardianatomy_native_smoke() -> None:
    registry = load_registry()
    _require_native(registry)
    health = registry.capability("anatomy.health").invoke("anatomy.health", {})
    assert health["service"] == "CardiAnatomy"
    presets = registry.capability("anatomy.presets").invoke("anatomy.presets", {})
    assert "cine_cmr_biventricular" in presets["presets"]

    phases = registry.capability("anatomy.cine.phases").invoke(
        "anatomy.cine.phases",
        {"volumes_ml": [120.0, 90.0, 65.0, 85.0, 110.0]},
    )
    assert phases["ed_phase"] == 0
    assert phases["es_phase"] == 2

    inverse = registry.capability("anatomy.transforms.invert").invoke(
        "anatomy.transforms.invert",
        {
            "matrix": [
                [1.0, 0.0, 0.0, 2.0],
                [0.0, 1.0, 0.0, -1.0],
                [0.0, 0.0, 1.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        },
    )
    assert inverse["matrix"][0][3] == -2.0

    correspondence = registry.capability(
        "anatomy.correspondence.compare"
    ).invoke(
        "anatomy.correspondence.compare",
        {
            "reference_points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "target_points": [[1, 0, 0], [2, 0, 0], [1, 1, 0]],
            "reference_cells": [[0, 1, 2]],
            "target_cells": [[0, 1, 2]],
        },
    )
    assert correspondence["connectivity_identical"] is True

    motion = registry.capability("anatomy.motion.summarize").invoke(
        "anatomy.motion.summarize",
        {
            "frames": [
                [[0, 0, 0], [1, 0, 0]],
                [[0.5, 0, 0], [1.5, 0, 0]],
                [[0, 0, 0], [1, 0, 0]],
            ],
            "cyclic": True,
        },
    )
    assert motion["cyclic_closure_error"]["max"] == 0.0

    overlap = registry.capability(
        "anatomy.validation.segmentation"
    ).invoke(
        "anatomy.validation.segmentation",
        {
            "reference_labels": [1, 1, 0, 0],
            "prediction_labels": [1, 0, 0, 0],
        },
    )
    assert overlap["metrics"]["1"]["dice"] > 0.66

    point_metrics = registry.capability(
        "anatomy.validation.points"
    ).invoke(
        "anatomy.validation.points",
        {
            "reference_points": [[0, 0, 0], [1, 0, 0]],
            "prediction_points": [[1, 0, 0], [2, 0, 0]],
            "units": "mm",
            "block_size": 1,
        },
    )
    assert point_metrics["hausdorff"] == 1.0



def test_dccp_host_map_native_result_is_json_boundary_safe() -> None:
    registry = load_registry()
    _require_native(registry)
    result = registry.capability("host.map").invoke(
        "host.map",
        {
            "entity_id": "json-boundary-smoke",
            "module_scores": {
                "inflammatory": 0.8,
                "contractile_functional": 0.6,
                "metabolic_mitochondrial": 0.4,
            },
        },
    )
    encoded = json.dumps(result, allow_nan=False)
    assert '"continuous"' in encoded
    assert isinstance(result["axes"]["ordinal"], dict)


def test_cardiflow_and_carditherapy_reference_backends(tmp_path: Path) -> None:
    registry = load_registry()
    _require_native(registry)

    flow_health = registry.capability("flow.health").invoke("flow.health", {})
    assert flow_health["service"] == "CardiFlow"
    assert "windkessel-3element-v1" in flow_health["backends"]

    flow_result = registry.capability("flow.simulate").invoke(
        "flow.simulate",
        {
            "subject_id": "smoke",
            "domain": {
                "domain_id": "systemic",
                "anatomy_ref": {
                    "artifact_id": "anatomy",
                    "kind": "reduced_order_domain",
                    "uri": "memory://systemic",
                },
                "region": "systemic_circulation",
            },
            "backend": "windkessel-3element-v1",
            "fluid": {"density": 1060.0, "dynamic_viscosity": 0.0035},
            "boundary_conditions": [
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
            "settings": {"dt_s": 0.01, "inlet_flow": [2.0, 2.0, 2.0]},
        },
    )
    assert flow_result["validation_status"] == "software_checked"
    assert flow_result["qc"]["passed"] is True

    with pytest.raises(Exception, match="backend unavailable"):
        registry.capability("flow.simulate").invoke(
            "flow.simulate",
            {
                "subject_id": "smoke",
                "domain": {
                    "domain_id": "lv",
                    "anatomy_ref": {
                        "artifact_id": "anatomy",
                        "kind": "surface_mesh",
                        "uri": "file:///lv.vtp",
                    },
                    "region": "left_ventricle",
                },
                "backend": "missing",
                "fluid": {"density": 1060.0, "dynamic_viscosity": 0.0035},
                "boundary_conditions": [
                    {
                        "boundary_id": "wall",
                        "kind": "wall",
                        "region": "endocardium",
                    }
                ],
            },
        )

    surface = tmp_path / "therapy-surface.json"
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

    therapy_health = registry.capability("therapy.health").invoke("therapy.health", {})
    assert therapy_health["service"] == "CardiTherapy"
    assert "cardiep-pacing-v1" in therapy_health["backends"]

    therapy_result = registry.capability("therapy.run").invoke(
        "therapy.run",
        {
            "subject_id": "smoke",
            "backend": "cardiep-pacing-v1",
            "twin_state_ref": {
                "artifact_id": "twin",
                "kind": "cardiac_state",
                "uri": "memory://state",
            },
            "baseline_refs": [
                {
                    "artifact_id": "ep-surface",
                    "kind": "surface_mesh",
                    "uri": surface.resolve().as_uri(),
                    "metadata": {"coordinate_frame": "smoke"},
                }
            ],
            "plan": {
                "plan_id": "P1",
                "arms": [
                    {
                        "arm_id": "control",
                        "label": "Baseline",
                        "is_comparator": True,
                    },
                    {
                        "arm_id": "paced",
                        "label": "Alternative pacing root",
                        "interventions": [
                            {
                                "intervention_id": "pace-1",
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
            "settings": {
                "ep_backend": "surface-eikonal-v1",
                "ep_parameters": {"isotropic_speed_cm_per_ms": 0.1},
                "ep_settings": {
                    "root_node": 0,
                    "output_dir": str(tmp_path / "therapy-ep"),
                },
            },
        },
    )
    assert therapy_result["validation_status"] == "software_checked"
    assert therapy_result["backend"] == "cardiep-pacing-v1"
    assert {item["arm_id"] for item in therapy_result["outcomes"]} == {
        "control",
        "paced",
    }
    assert all(item["unit"] == "ms" for item in therapy_result["outcomes"])

    with pytest.raises(Exception, match="backend unavailable"):
        registry.capability("therapy.run").invoke(
            "therapy.run",
            {
                "subject_id": "smoke",
                "backend": "missing",
                "twin_state_ref": {
                    "artifact_id": "twin",
                    "kind": "cardiac_state",
                    "uri": "file:///state.json",
                },
                "plan": {
                    "plan_id": "P2",
                    "arms": [
                        {
                            "arm_id": "control",
                            "label": "Control",
                            "is_comparator": True,
                        }
                    ],
                    "endpoints": ["activation_span_ms"],
                },
            },
        )

