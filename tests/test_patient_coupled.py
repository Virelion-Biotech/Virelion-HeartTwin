"""Manufactured four-cavity monolithic coupling and analytical Jacobian checks."""

import importlib.util
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import pytest

from hearttwin.patient_coupled import CHAMBERS, coupled_system, load_coupling, simulate_coupled
from hearttwin.patient_geometry import reconstruct_labels, write_artifact

pytestmark = pytest.mark.skipif(importlib.util.find_spec("cardimech") is None or importlib.util.find_spec("scipy") is None,
                                reason="Pinned CardiMech and scipy required")


def read(ref):
    return json.loads(Path(unquote(urlparse(ref["uri"]).path)).read_bytes())


def coupled_request(tmp_path):
    image = np.ones((9, 3, 3), dtype=int)
    for x in [1, 3, 5, 7]:
        image[x, 1, 1] = 2
    source = write_artifact(tmp_path, "four-cavity-labels", {"labels": image.tolist(), "voxel_corner_affine_m": np.diag([0.01, 0.01, 0.01, 1.]).tolist()})
    p = {"subject_id": "P1", "source": source, "format": "json-labels-v1", "coordinate_frame": "MODEL", "tissue_labels": [1], "cavity_labels": [2],
         "review": {"source_sha256": source["sha256"], "reviewer": "synthetic", "rationale": "Manufactured four-cavity solid"}, "output_dir": str(tmp_path)}
    mesh = read(reconstruct_labels(p)["mesh"])
    ne = len(mesh["tetrahedra"])
    mesh.update(fiber=[[1., 0., 0.]] * ne, sheet=[[0., 1., 0.]] * ne)
    mesh_ref = write_artifact(tmp_path, "fiber-mesh", mesh)
    nodes = np.asarray(mesh["nodes"])
    faces = {name: [] for name in CHAMBERS}
    centres = np.array([0.015, 0.035, 0.055, 0.075])
    for face in mesh["cavity_faces"]:
        index = np.argmin(np.abs(centres - nodes[face].mean(axis=0)[0]))
        faces[CHAMBERS[index]].append(face)
    cavity_ref = write_artifact(tmp_path, "cavities", {"subject_id": "P1", "mesh_sha256": mesh_ref["sha256"], "coordinate_frame": "MODEL", "reviewer": "synthetic", "faces": faces})
    times = [0., 0.005, 0.01]
    voltage = write_artifact(tmp_path, "voltage", {"subject_id": "P1", "mesh": mesh_ref, "coordinate_frame": "MODEL", "node_ids": list(range(len(nodes))),
                        "time_s": times, "voltage_V": [[v] * len(nodes) for v in [-0.08, 0.02, 0.02]]})
    return {"subject_id": "P1", "mesh": mesh_ref, "cavities": cavity_ref, "voltage": voltage, "coordinate_frame": "MODEL",
            "time_s": times, "passive_Pa": {"mu": 1000., "kappa": 5000.},
            "fixed_boundaries": [{"node_ids": np.flatnonzero(nodes[:, 0] == 0).tolist(), "components": [0, 1, 2], "displacement_m": [0., 0., 0.]}],
            "activation": {"voltage_threshold_V": -0.03, "voltage_slope_V": 0.005, "rise_tau_s": 0.01, "decay_tau_s": 0.03, "maximum_tension_Pa": [5.] * ne, "initial_activation": [0.] * ne},
            "vessels": {name: {"compliance_mL_per_mmHg": 0.01, "unstressed_volume_mL": 2., "external_pressure_mmHg": 0.} for name in ["SA", "SV", "PA", "PV"]},
            "initial_volumes_mL": [1., 1., 2., 2., 1., 1., 2., 2.], "initial_pressure_guess_mmHg": [0.] * 4,
            "chamber_external_pressure_mmHg": [0.] * 4, "edge_resistance_mmHg_s_per_mL": [1.] * 8,
            "solver": {"force_tolerance_N": 1e-8, "volume_tolerance_mL": 1e-6, "max_iterations": 30},
            "reference_state_rationale": "Manufactured stress-free solid with four one-mL cavities, not patient anatomy", "output_dir": str(tmp_path)}


def test_monolithic_jacobian_matches_directional_finite_difference(tmp_path):
    p = coupled_request(tmp_path)
    mesh, groups, fixed, free, prescribed = load_coupling(p)
    rng = np.random.default_rng(8)
    x = np.concatenate([rng.normal(size=len(free)) * 1e-6, [0.2, 0.3, 0.1, 0.15], p["initial_volumes_mL"]])
    direction = np.concatenate([rng.normal(size=len(free)) * 1e-4, rng.normal(size=4) * 0.1, rng.normal(size=8) * 0.05])
    kwargs = dict(mesh=mesh, groups=groups, free=free, fixed=fixed, prescribed=prescribed,
                  tension=np.ones(len(mesh.elements)) * 1.5, passive=p["passive_Pa"], external=np.zeros(4), vessels=p["vessels"],
                  resistance=np.ones(8), previous_volume=np.array(p["initial_volumes_mL"]), dt=0.005, scales=(1e-8, 1e-6))
    _, jacobian, _ = coupled_system(x, **kwargs)
    eps = 1e-6
    plus, _, _ = coupled_system(x + eps * direction, **kwargs)
    minus, _, _ = coupled_system(x - eps * direction, **kwargs)
    expected = jacobian @ direction
    actual = (plus - minus) / (2 * eps)
    assert np.linalg.norm(actual - expected) / np.linalg.norm(expected) < 1e-5


def test_coupled_activation_changes_spatial_wall_and_preserves_circuit_volume(tmp_path):
    p = coupled_request(tmp_path)
    result = read(simulate_coupled(p)["coupled"])
    states = result["states"]
    assert max(np.abs(np.asarray(states[-1]["displacement_m"])).ravel()) > 1e-8
    assert max(states[-1]["active_tension_Pa"]) > 1
    np.testing.assert_allclose([sum(s["volumes_mL"]) for s in states], 12., atol=1e-8)
    assert all(s["scaled_residual"] <= 1 for s in states)
    assert all(min(s["element_J"]) > 0 for s in states)
    assert all(s["volume_mismatch_mL"] <= 1e-6 for s in states)
    assert all(s["force_residual_N"] <= 1e-8 for s in states)
    assert np.all(np.asarray([s["flows_mL_per_s"] for s in states])[:, [0, 1, 4, 5]] >= 0)


@pytest.mark.parametrize("change", ["voltage-patient", "frame", "cavity-hash", "fibres"])
def test_coupling_rejects_mismatched_sources(tmp_path, change):
    p = coupled_request(tmp_path)
    if change in {"voltage-patient", "frame"}:
        v = read(p["voltage"])
        v["subject_id" if change == "voltage-patient" else "coordinate_frame"] = "wrong"
        p["voltage"] = write_artifact(tmp_path, "wrong-voltage", v)
    elif change == "cavity-hash":
        p["cavities"]["sha256"] = "0" * 64
    else:
        m = read(p["mesh"])
        m.pop("fiber")
        p["mesh"] = write_artifact(tmp_path, "missing-fibres", m)
    with pytest.raises(ValueError):
        simulate_coupled(p)
