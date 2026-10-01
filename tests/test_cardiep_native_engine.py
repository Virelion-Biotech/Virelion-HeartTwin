import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("cardiep")

from hearttwin import load_registry


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
