"""Manufactured BSP contracts and independent reciprocal/forward FEM checks."""
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy

import numpy as np
import pytest
from test_patient_native_preparation import image_request, read

from hearttwin.patient_bsp import evaluate_bsp_holdout
from hearttwin.patient_ecg_eval import ECGEvaluationSpec
from hearttwin.patient_geometry import reconstruct_labels, write_artifact
from hearttwin.patient_spatial_ep import simulate_spatial_ep
from hearttwin.patient_volume_conductor import (
    generate_leadfield,
    observation_matrix,
    tetra_operators,
)

pytestmark = pytest.mark.skipif(importlib.util.find_spec("scipy") is None, reason="patient-native extra required")


def bsp_request(tmp_path, count=128):
    mesh_ref = reconstruct_labels(image_request(tmp_path, (5, 5, 5)))["mesh"]
    mesh = read(mesh_ref)
    nodes = np.asarray(mesh["nodes"])
    lo, hi = nodes.min(axis=0), nodes.max(axis=0)
    boundary = np.flatnonzero(np.any(np.isclose(nodes, lo) | np.isclose(nodes, hi), axis=1))
    electrodes = {f"B{i:03d}": int(boundary[i]) for i in range(count)}
    electrodes["CMS"] = int(boundary[count])
    names = [f"B{i:03d}-CMS" for i in range(count)]
    declaration = {"channel_names": names, "reference": "recorded-CMS",
                   "weights": [{f"B{i:03d}": 1., "CMS": -1.} for i in range(count)], "unit": "V"}
    electrode_ref = write_artifact(tmp_path, "bsp-electrodes", {
        "subject_id": "P1", "mesh_sha256": mesh_ref["sha256"],
        "coordinate_frame": "MODEL", "reviewer": "synthetic-fixture",
        "node_ids": electrodes, "observations": declaration})
    total = np.array([[.4, .03, .01], [.03, .5, .02], [.01, .02, .6]])
    return {"subject_id": "P1", "mesh": mesh_ref, "electrodes": electrode_ref,
            "coordinate_frame": "MODEL", "myocardial_element_ids": list(range(len(mesh["tetrahedra"]))),
            "total_conductivity_S_per_m": np.tile(total, (len(mesh["tetrahedra"]), 1, 1)).tolist(),
            "intracellular_conductivity_S_per_m": np.tile(np.eye(3)*.1, (len(mesh["tetrahedra"]), 1, 1)).tolist(),
            "gauge_node": 0, "output_dir": str(tmp_path)}


def test_128_channel_reciprocity_matches_independent_forward_and_cli(tmp_path):
    p = bsp_request(tmp_path)
    mesh = read(p["mesh"])
    record = read(p["electrodes"])
    lf = read(generate_leadfield(p)["leadfield"])
    assert lf["schema"] == "hearttwin-reciprocal-p1-v2"
    assert lf["measurement_reference"] == "recorded-CMS"
    k, _, _ = tetra_operators(mesh, p["total_conductivity_S_per_m"])
    ki, _, _ = tetra_operators(mesh, p["intracellular_conductivity_S_per_m"])
    vm = np.random.default_rng(104).normal(size=len(mesh["nodes"])) * .01
    phi = np.zeros(len(vm))
    phi[1:] = np.linalg.solve(k.toarray()[1:, 1:], (-ki @ vm)[1:])
    expected = np.array([phi[record["node_ids"][f"B{i:03d}"]] - phi[record["node_ids"]["CMS"]]
                         for i in range(128)])
    op = np.asarray(lf["operator_V_per_V"])
    np.testing.assert_allclose(op @ vm, expected, atol=1e-13)
    np.testing.assert_allclose(op @ np.ones(len(vm)), 0, atol=1e-13)
    p["gauge_node"] = len(vm)-1
    np.testing.assert_allclose(read(generate_leadfield(p)["leadfield"])["operator_V_per_V"], op, atol=1e-12)
    path = tmp_path/"command.json"
    path.write_text(json.dumps(p))
    command = subprocess.run([sys.executable, "-m", "hearttwin.patient_preparation",
                              "patient.leadfield.generate", str(path)], text=True, capture_output=True, check=True)
    np.testing.assert_allclose(read(json.loads(command.stdout)["leadfield"])["operator_V_per_V"], op, atol=1e-12)


def test_explicit_common_average_reference_and_channel_permutation(tmp_path):
    p = bsp_request(tmp_path, 4)
    original = np.asarray(read(generate_leadfield(p)["leadfield"])["operator_V_per_V"])
    r = read(p["electrodes"])
    r["observations"]["reference"] = "mean-of-B000-through-B003"
    r["observations"]["weights"] = [{f"B{j:03d}": float(i == j)-.25 for j in range(4)} for i in range(4)]
    r["observations"]["channel_names"].reverse()
    r["observations"]["weights"].reverse()
    p["electrodes"] = write_artifact(tmp_path, "car-electrodes", r)
    op = np.asarray(read(generate_leadfield(p)["leadfield"])["operator_V_per_V"])
    np.testing.assert_allclose(op, (original-original.mean(axis=0))[::-1], atol=1e-13)


@pytest.mark.parametrize("mutation", ["unbalanced", "unknown", "zero", "bool", "duplicate", "unit", "reference"])
def test_invalid_observation_conventions_fail_closed(tmp_path, mutation):
    p = bsp_request(tmp_path, 4)
    r = read(p["electrodes"])
    d = r["observations"]
    if mutation == "unbalanced":
        d["weights"][0]["CMS"] = -.5
    elif mutation == "unknown":
        d["weights"][0] = {"not-recorded": 1., "CMS": -1.}
    elif mutation == "zero":
        d["weights"][0] = {"CMS": 0.}
    elif mutation == "bool":
        d["weights"][0]["CMS"] = True
    elif mutation == "duplicate":
        d["channel_names"][1] = d["channel_names"][0]
    elif mutation == "unit":
        d["unit"] = "mV"
    else:
        d["reference"] = ""
    with pytest.raises(ValueError):
        observation_matrix(r, len(read(p["mesh"])["nodes"]))


def bsp_prediction(tmp_path, count=4):
    p = bsp_request(tmp_path, count)
    lf = generate_leadfield(p)["leadfield"]
    mesh = read(p["mesh"])
    n = len(mesh["nodes"])
    ep = {"subject_id": "P1", "mesh": p["mesh"], "coordinate_frame": "MODEL",
          "output_dir": str(tmp_path), "leadfield": lf, "leadfield_node_ids": list(range(n)),
          "initial_u": [0.]*n, "initial_w": [0.]*n,
          "diffusion_tensor_m2_per_s": np.tile(np.eye(3)*.0001, (len(mesh["tetrahedra"]),1,1)).tolist(),
          "parameters": {"a": .1,"b": 1.,"tau_u_s": .01,"tau_w_s": .1,"rest_V": -.08,"amplitude_V": .1},
          "dt_s": .0005,"duration_s": .01,
          "stimuli": [{"node_ids": [0],"start_s": 0.,"end_s": .0025,"amplitude": 2.}]}
    predicted = simulate_spatial_ep(ep)["spatial_ep"]
    values = read(predicted)
    assert "bsp_V" in values and "ecg_V" not in values
    observation = {"schema": "hearttwin-bsp-observation-v1", "subject_id": "P1",
                   "coordinate_frame": "MODEL", "measurement_reference": "recorded-CMS",
                   "lead_names": values["lead_names"], "time_s": values["time_s"],
                   "values_V": values["bsp_V"], "signal_unit": "V", "withheld_from_fit": True,
                   "evidence_id": "manufactured-no-patient-data"}
    return predicted, observation


@pytest.mark.parametrize("count", [4, 128])
def test_native_bsp_propagation_and_explicit_evaluation(tmp_path, count):
    pred, obs = bsp_prediction(tmp_path, count)
    spec = ECGEvaluationSpec(expected_leads=obs["lead_names"], require_standard_12_lead=False)
    report = evaluate_bsp_holdout(pred, write_artifact(tmp_path, "manufactured-bsp", obs), spec=spec)
    assert report.mean_nrmse == 0
    assert report.n_samples_compared == len(obs["time_s"])
    assert report.evidence_id == "manufactured-no-patient-data"
    from hearttwin.config import load_registry
    measured = write_artifact(tmp_path, "registry-bsp", obs)
    adapter = load_registry().capability("patient.bsp.evaluate")
    result = adapter.invoke("patient.bsp.evaluate", {
        "prediction": pred, "observation": measured, "protocol": spec.model_dump(mode="json")})
    assert result["mean_nrmse"] == 0


@pytest.mark.parametrize("mutation", ["subject", "reference", "frame", "time", "holdout", "channel", "unit", "hash"])
def test_bsp_evaluation_rejects_mismatched_contract(tmp_path, mutation):
    pred, obs = bsp_prediction(tmp_path)
    names = deepcopy(obs["lead_names"])
    if mutation == "subject":
        obs["subject_id"] = "different"
    elif mutation == "reference":
        obs["measurement_reference"] = "WCT"
    elif mutation == "frame":
        obs["coordinate_frame"] = "OTHER"
    elif mutation == "time":
        obs["time_s"] = (np.asarray(obs["time_s"])*2).tolist()
    elif mutation == "holdout":
        obs["withheld_from_fit"] = False
    elif mutation == "channel":
        obs["lead_names"].reverse()
    elif mutation == "unit":
        obs["signal_unit"] = "mV"
    measured = write_artifact(tmp_path, "bad-bsp", obs)
    if mutation == "hash":
        measured["sha256"] = "0"*64
    spec = ECGEvaluationSpec(expected_leads=names, require_standard_12_lead=False)
    with pytest.raises(ValueError):
        evaluate_bsp_holdout(pred, measured, spec=spec)
