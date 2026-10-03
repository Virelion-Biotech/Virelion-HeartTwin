from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from hearttwin import CardiacStateStore, Provenance, ServiceResult, load_registry


def _require_native(registry, names: set[str]) -> None:
    missing = sorted(
        name for name in names if not registry.adapters[name].available()
    )
    if missing and os.getenv("HEARTTWIN_REQUIRE_NATIVE") != "1":
        pytest.skip("native component packages not installed: " + ", ".join(missing))
    assert not missing


def test_native_flow_and_pacing_results_survive_canonical_state_pipe(
    tmp_path: Path,
) -> None:
    registry = load_registry()
    _require_native(registry, {"CardiFlow", "CardiEP", "CardiTherapy"})

    flow = registry.capability("flow.simulate").invoke(
        "flow.simulate",
        {
            "subject_id": "pipe-smoke",
            "domain": {
                "domain_id": "systemic",
                "anatomy_ref": {
                    "artifact_id": "flow-domain",
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
    therapy = registry.capability("therapy.run").invoke(
        "therapy.run",
        {
            "subject_id": "pipe-smoke",
            "backend": "cardiep-pacing-v1",
            "twin_state_ref": {
                "artifact_id": "state",
                "kind": "cardiac_state",
                "uri": "memory://state",
            },
            "baseline_refs": [
                {
                    "artifact_id": "surface",
                    "kind": "surface_mesh",
                    "uri": surface.resolve().as_uri(),
                    "metadata": {"coordinate_frame": "pipe-smoke"},
                }
            ],
            "plan": {
                "plan_id": "pace-smoke",
                "arms": [
                    {
                        "arm_id": "control",
                        "label": "Baseline",
                        "is_comparator": True,
                    },
                    {
                        "arm_id": "paced",
                        "label": "Paced",
                        "interventions": [
                            {
                                "intervention_id": "pace-root-2",
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
                    "output_dir": str(tmp_path / "ep"),
                },
            },
        },
    )

    store = CardiacStateStore.new("pipe-smoke")
    store.reduce_service_result(
        ServiceResult(
            service="CardiFlow",
            capability="flow.simulate",
            status="ok",
            data=flow,
            provenance=Provenance(
                source_service="CardiFlow",
                run_id="flow-pipe-smoke",
            ),
        )
    )
    store.reduce_service_result(
        ServiceResult(
            service="CardiTherapy",
            capability="therapy.run",
            status="ok",
            data=therapy,
            provenance=Provenance(
                source_service="CardiTherapy",
                run_id="therapy-pipe-smoke",
                parent_run_ids=["flow-pipe-smoke"],
            ),
        )
    )

    snapshot = store.snapshot()
    assert len(snapshot.flow_artifacts) == 1
    assert snapshot.flow_artifacts[0].backend == "windkessel-3element-v1"
    assert snapshot.flow_artifacts[0].validation_status == "software_checked"
    assert len(snapshot.therapy_artifacts) == 1
    assert snapshot.therapy_artifacts[0].backend == "cardiep-pacing-v1"
    assert snapshot.therapy_artifacts[0].validation_status == "software_checked"
    assert snapshot.therapy_artifacts[0].outcomes

    payload = snapshot.model_dump(mode="json")
    json.dumps(payload, allow_nan=False)
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "cardiac-state-1.2.0.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.validate(payload, schema)
