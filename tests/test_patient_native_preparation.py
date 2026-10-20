"""Manufactured numerical solutions and strict synthetic hospital-source boundaries."""

import json
import importlib.util
from copy import deepcopy
from urllib.parse import unquote, urlparse

import numpy as np
import pytest

from hearttwin.patient_geometry import reconstruct_labels, write_artifact
from hearttwin.patient_hospital import import_fhir_bundle
from hearttwin.patient_spatial_ep import simulate_spatial_ep
from hearttwin.patient_volume_conductor import generate_leadfield, lead_matrix, tetra_operators

pytestmark = pytest.mark.skipif(importlib.util.find_spec("scipy") is None, reason="patient-native extra required")


def read(ref):
    from pathlib import Path
    return json.loads(Path(unquote(urlparse(ref["uri"]).path)).read_bytes())


def image_request(tmp_path, shape=(2, 2, 2)):
    affine = np.diag([0.001, 0.002, 0.003, 1.0])
    affine[:3, 3] = [0.1, -0.2, 0.05]
    source = write_artifact(tmp_path, "labels", {"labels": np.ones(shape, dtype=int).tolist(), "voxel_corner_affine_m": affine.tolist()})
    source["file_id"] = "reviewed-image"
    return {"subject_id": "P1", "source": source, "format": "json-labels-v1", "coordinate_frame": "MODEL",
            "tissue_labels": [1], "cavity_labels": [2], "output_dir": str(tmp_path),
            "review": {"source_sha256": source["sha256"], "reviewer": "synthetic-reviewer", "rationale": "Manufactured label image"}}


def conductor(tmp_path):
    request = image_request(tmp_path)
    mesh = reconstruct_labels(request)["mesh"]
    m = read(mesh)
    # Node indices come from the generated topology, never hardcoded positions.
    coords = np.asarray(m["nodes"])
    boundary = [i for i, p in enumerate(coords) if any(np.isclose(p[j], coords[:, j].min()) or np.isclose(p[j], coords[:, j].max()) for j in range(3))]
    names = ["RA", "LA", "LL", "V1", "V2", "V3", "V4", "V5", "V6"]
    electrodes = write_artifact(tmp_path, "electrodes", {"subject_id": "P1", "mesh_sha256": mesh["sha256"],
        "coordinate_frame": "MODEL", "reviewer": "synthetic-reviewer", "node_ids": dict(zip(names, boundary[:9]))})
    return {"subject_id": "P1", "mesh": mesh, "electrodes": electrodes, "coordinate_frame": "MODEL",
            "myocardial_element_ids": list(range(len(m["tetrahedra"]))),
            "total_conductivity_S_per_m": np.tile(np.eye(3) * 0.4, (len(m["tetrahedra"]), 1, 1)).tolist(),
            "intracellular_conductivity_S_per_m": np.tile(np.eye(3) * 0.1, (len(m["tetrahedra"]), 1, 1)).tolist(),
            "gauge_node": 0, "output_dir": str(tmp_path)}


def test_reconstruction_physical_volume_and_conforming_faces(tmp_path):
    result = reconstruct_labels(image_request(tmp_path))
    mesh, manifest = read(result["mesh"]), read(result["manifest"])
    assert len(mesh["nodes"]) == 27
    assert len(mesh["tetrahedra"]) == 48
    assert len(manifest["exterior_faces"]) == 48
    assert manifest["volume_m3"] == pytest.approx(8 * 0.001 * 0.002 * 0.003)
    k, mass, (_, _, gradients, _) = tetra_operators(mesh, np.tile(np.eye(3), (48, 1, 1)))
    # Linear interpolation exactly reproduces an affine field gradient.
    expected = np.array([2., -3., 4.])
    values = np.asarray(mesh["nodes"]) @ expected + 0.2
    actual = np.einsum("ea,eai->ei", values[np.asarray(mesh["tetrahedra"])], gradients)
    np.testing.assert_allclose(actual, np.tile(expected, (48, 1)), atol=1e-12)
    np.testing.assert_allclose(k @ np.ones(27), 0, atol=1e-15)
    assert mass.sum() == pytest.approx(manifest["volume_m3"])


def test_review_and_source_tampering_rejected(tmp_path):
    p = image_request(tmp_path)
    p["review"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Reviewed"):
        reconstruct_labels(p)
    p = image_request(tmp_path)
    p["source"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA"):
        reconstruct_labels(p)


def test_reconstructed_cavity_is_closed_and_oriented_into_wall(tmp_path):
    from collections import Counter

    p = image_request(tmp_path, (3, 3, 3))
    image = read(p["source"])
    image["labels"][1][1][1] = 2
    p["source"] = {"file_id": "reviewed-image", **write_artifact(tmp_path, "shell-labels", image)}
    p["review"]["source_sha256"] = p["source"]["sha256"]
    mesh = read(reconstruct_labels(p)["mesh"])
    faces, nodes = np.asarray(mesh["cavity_faces"]), np.asarray(mesh["nodes"])
    assert len(faces) == 12
    edges = Counter((int(a), int(b)) for f in faces for a, b in zip(f, np.roll(f, -1)))
    assert all(count == 1 and edges[b, a] == 1 for (a, b), count in edges.items())
    x = nodes[faces]
    volume = np.einsum("ei,ei->e", x[:, 0], np.cross(x[:, 1], x[:, 2])).sum() / 6
    assert volume == pytest.approx(0.001 * 0.002 * 0.003)


def test_reciprocity_matches_independent_forward_solve_and_gauge(tmp_path):
    p = conductor(tmp_path)
    result = read(generate_leadfield(p)["leadfield"])
    mesh = read(p["mesh"])
    total, _, _ = tetra_operators(mesh, p["total_conductivity_S_per_m"])
    ki, _, _ = tetra_operators(mesh, p["intracellular_conductivity_S_per_m"])
    vm = np.random.default_rng(29).normal(size=len(mesh["nodes"])) * 0.01
    rhs = -ki @ vm
    phi = np.zeros(len(vm))
    phi[1:] = np.linalg.solve(total.toarray()[1:, 1:], rhs[1:])
    c = lead_matrix(read(p["electrodes"])["node_ids"], len(vm))
    op = np.asarray(result["operator_V_per_V"])
    np.testing.assert_allclose(op @ vm, c @ phi, atol=1e-14)
    np.testing.assert_allclose(op @ np.ones(len(vm)), 0, atol=1e-13)
    np.testing.assert_allclose(op[2], op[1] - op[0], atol=1e-13)
    p["gauge_node"] = len(vm) - 1
    np.testing.assert_allclose(read(generate_leadfield(p)["leadfield"])["operator_V_per_V"], op, atol=1e-12)


@pytest.mark.parametrize("change", ["patient", "tensor", "electrode"])
def test_leadfield_invalid_identity_or_materials_rejected(tmp_path, change):
    p = conductor(tmp_path)
    if change == "patient":
        p["subject_id"] = "P2"
    elif change == "tensor":
        p["total_conductivity_S_per_m"][0][0][0] = -0.1
    else:
        p["electrodes"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        generate_leadfield(p)


def ep_request(tmp_path):
    p = conductor(tmp_path)
    lf = generate_leadfield(p)["leadfield"]
    mesh = read(p["mesh"])
    n = len(mesh["nodes"])
    return {"subject_id": "P1", "mesh": p["mesh"], "coordinate_frame": "MODEL", "output_dir": str(tmp_path),
            "leadfield": lf, "leadfield_node_ids": list(range(n)),
            "initial_u": [0.] * n, "initial_w": [0.] * n,
            "diffusion_tensor_m2_per_s": np.tile(np.eye(3) * 0.0001, (len(mesh["tetrahedra"]), 1, 1)).tolist(),
            "parameters": {"a": 0.1, "b": 1., "tau_u_s": 0.01, "tau_w_s": 0.1, "rest_V": -0.08, "amplitude_V": 0.1},
            "dt_s": 0.0005, "duration_s": 0.02, "stimuli": []}


def test_spatial_rest_equilibrium_and_stimulus_response(tmp_path):
    p = ep_request(tmp_path)
    result = read(simulate_spatial_ep(p)["spatial_ep"])
    np.testing.assert_allclose(result["voltage_V"], -0.08, atol=1e-14)
    np.testing.assert_allclose(result["ecg_V"], 0, atol=1e-14)
    p["stimuli"] = [{"node_ids": [0], "start_s": 0., "end_s": 0.005, "amplitude": 2.}]
    driven = read(simulate_spatial_ep(p)["spatial_ep"])
    assert np.max(np.asarray(driven["voltage_V"])[1:] + 0.08) > 0.001
    assert np.max(np.abs(driven["ecg_V"])) > 1e-7
    p["leadfield_node_ids"][0], p["leadfield_node_ids"][1] = 1, 0
    with pytest.raises(ValueError, match="correspondence"):
        simulate_spatial_ep(p)


def hospital_fixture(tmp_path):
    case = {"patient_id": "P1", "encounters": [{"encounter_id": "visit"}]}
    resource = {"resourceType": "Observation", "id": "pressure", "status": "final",
                "subject": {"reference": "Patient/hospital-p1"}, "encounter": {"reference": "Encounter/visit1"},
                "code": {"coding": [{"system": "http://loinc.org", "code": "synthetic-pressure"}]},
                "valueQuantity": {"value": 80., "system": "http://unitsofmeasure.org", "code": "mm[Hg]"}}
    bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": resource}]}
    mapping = {"patient_reference": "Patient/hospital-p1", "encounters": {"Encounter/visit1": "visit"},
               "observations": [{"observation_id": "pressure", "acquisition_id": "pressure1", "evidence_id": "pressure-e1",
                   "variable": "outlet_pressure", "unit": "mm[Hg]", "role": "context", "modality": "pressure",
                   "domain": "circulation", "input_id": "pressure-input", "code_system": "http://loinc.org", "code": "synthetic-pressure"}]}
    source = {"file_id": "hospital", **write_artifact(tmp_path, "bundle", bundle)}
    return case, source, mapping, bundle


def test_hospital_import_encounter_provenance_and_idempotency(tmp_path):
    case, source, mapping, _ = hospital_fixture(tmp_path)
    imported, report = import_fhir_bundle(case, source, mapping, output_dir=tmp_path)
    assert report["imported"] == ["pressure1"]
    assert imported.model_inputs[0].value == 80
    assert imported.acquisitions[0].encounter_id == "visit"
    again, report = import_fhir_bundle(imported, source, mapping, output_dir=tmp_path)
    assert report["unchanged"] == ["pressure1"]
    assert again == imported
    changed = deepcopy(mapping)
    changed["observations"][0]["variable"] = "other"
    with pytest.raises(ValueError, match="different content"):
        import_fhir_bundle(imported, source, changed, output_dir=tmp_path)


@pytest.mark.parametrize("change", ["patient", "units", "inequality", "holdout", "status", "pagination"])
def test_hospital_fail_closed(tmp_path, change):
    case, source, mapping, bundle = hospital_fixture(tmp_path)
    resource = bundle["entry"][0]["resource"]
    if change == "patient":
        resource["subject"]["reference"] = "Patient/other"
    elif change == "units":
        resource["valueQuantity"]["code"] = "kPa"
    elif change == "inequality":
        resource["valueQuantity"]["comparator"] = "<"
    elif change == "holdout":
        mapping["observations"][0]["role"] = "validation"
    elif change == "status":
        resource["status"] = "preliminary"
    else:
        bundle["link"] = [{"relation": "next", "url": "https://hospital.invalid/next"}]
    source = {"file_id": "hospital", **write_artifact(tmp_path, "modified", bundle)}
    with pytest.raises(ValueError):
        import_fhir_bundle(case, source, mapping, output_dir=tmp_path)


def test_fhir_client_collects_pages_without_leaking_token(tmp_path, monkeypatch):
    import io
    import urllib.request
    from hearttwin.patient_hospital import fetch_fhir_bundle

    requests = []
    pages = [
        {"resourceType": "Bundle", "type": "searchset", "entry": [], "link": [{"relation": "next", "url": "?page=2"}]},
        {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": {"resourceType": "Observation", "id": "p2"}}]},
    ]

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps(pages[len(requests) - 1]).encode())

    monkeypatch.setenv("SYNTHETIC_FHIR_TOKEN", "not-a-real-credential")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    result = fetch_fhir_bundle("https://hospital.invalid/fhir/Observation?page=1", output_dir=tmp_path, token_env="SYNTHETIC_FHIR_TOKEN")
    assert len(requests) == 2 and requests[1].full_url.endswith("?page=2")
    assert requests[0].get_header("Authorization") == "Bearer not-a-real-credential"
    assert "not-a-real-credential" not in json.dumps(result)
    assert read(result)["entry"][0]["resource"]["id"] == "p2"


@pytest.mark.parametrize("next_url", ["https://other.invalid/fhir/Observation", "https://hospital.invalid/elsewhere", "https://hospital.invalid/fhir/Observation"])
def test_fhir_client_refuses_cross_origin_resource_and_loop(tmp_path, monkeypatch, next_url):
    import io
    import urllib.request
    from hearttwin.patient_hospital import fetch_fhir_bundle

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps({"resourceType": "Bundle", "type": "searchset", "link": [{"relation": "next", "url": next_url}]}).encode())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(ValueError, match="pagination"):
        fetch_fhir_bundle("https://hospital.invalid/fhir/Observation", output_dir=tmp_path)
    assert len(calls) == 1


def test_native_reconstruction_runs_through_frozen_patient_plan(tmp_path):
    from hearttwin.config import load_registry
    from hearttwin.patient_personalization import execute_patient_plan
    from hearttwin.patient_store import PatientCaseStore

    p = image_request(tmp_path)
    snapshot = PatientCaseStore(tmp_path / "cases.db").save({
        "patient_id": "P1", "encounters": [{"encounter_id": "visit"}],
        "acquisitions": [{"acquisition_id": "image", "subject_id": "P1", "encounter_id": "visit", "modality": "mri", "source_files": [p["source"]]}],
    }, expected_revision=0, actor="owner", reason="Synthetic image")
    p["source"] = {"$source": {"acquisition_id": "image", "file_id": "reviewed-image", "kind": "reviewed_label_image"}}
    request = {"case": snapshot["case"], "case_revision": snapshot["revision"], "case_fingerprint": snapshot["fingerprint"], "encounter_id": "visit",
               "stages": [{"stage_id": "mesh", "capability": "patient.geometry.reconstruct", "purpose": "Reviewed synthetic geometry",
                           "template": p, "literal_rationale": {"tissue_labels.0": "Synthetic wall label", "cavity_labels.0": "Synthetic cavity label"}}]}
    result = execute_patient_plan(request, load_registry(), output_dir=tmp_path / "plan")
    assert len(read(result["stages"]["mesh"]["mesh"])["tetrahedra"]) == 48
    assert result["case_fingerprint"] == snapshot["fingerprint"]
    assert result["personalization_manifest"][0]["bindings"][0]["source_sha256"] == snapshot["case"]["acquisitions"][0]["source_files"][0]["sha256"]


def test_nifti_centres_are_converted_to_physical_voxel_corners(tmp_path):
    nib = pytest.importorskip("nibabel")
    import hashlib

    p = image_request(tmp_path, (1, 1, 1))
    image = nib.Nifti1Image(np.ones((1, 1, 1), dtype=np.int16), np.diag([2., 3., 4., 1.]))
    image.header.set_xyzt_units("mm")
    raw = image.to_bytes()
    path = tmp_path / "labels.nii"
    path.write_bytes(raw)
    p.update(format="nifti-labels", coordinate_frame="NIFTI_RAS", source={"file_id": "nifti", "uri": path.as_uri(), "sha256": hashlib.sha256(raw).hexdigest()})
    p["review"]["source_sha256"] = p["source"]["sha256"]
    mesh = read(reconstruct_labels(p)["mesh"])
    np.testing.assert_allclose(np.asarray(mesh["nodes"]).min(axis=0), [-0.001, -0.0015, -0.002])
    np.testing.assert_allclose(np.asarray(mesh["nodes"]).max(axis=0), [0.001, 0.0015, 0.002])


def circulation_request(tmp_path):
    from hearttwin.patient_closed_loop import COMPARTMENTS

    compartments = {}
    for name in COMPARTMENTS:
        p = {"unstressed_volume_mL": 0., "external_pressure_mmHg": 0.}
        if name in {"LA", "LV", "RA", "RV"}:
            p.update(emin_mmHg_per_mL=1., emax_mmHg_per_mL=1., activation_start_s=0., activation_duration_s=0.3)
        else:
            p["compliance_mL_per_mmHg"] = 1.
        compartments[name] = p
    return {"subject_id": "P1", "compartments": compartments, "cycle_length_s": 1.,
            "initial_volumes_mL": [80.] * 8, "edge_resistance_mmHg_s_per_mL": [1.] * 8,
            "time_s": np.linspace(0., 1., 101).tolist(), "solver": {"rtol": 1e-8, "atol_mL": 1e-9, "max_step_s": 0.01}, "output_dir": str(tmp_path)}


def test_closed_loop_manufactured_equilibrium_and_conservation(tmp_path):
    from hearttwin.patient_closed_loop import simulate_closed_loop

    p = circulation_request(tmp_path)
    equilibrium = read(simulate_closed_loop(p)["circulation"])
    np.testing.assert_allclose(equilibrium["volumes_mL"], 80.)
    np.testing.assert_allclose(equilibrium["flows_mL_per_s"], 0.)
    p["compartments"]["LV"]["emax_mmHg_per_mL"] = 2.
    driven = read(simulate_closed_loop(p)["circulation"])
    np.testing.assert_allclose(np.asarray(driven["volumes_mL"]).sum(axis=1), 640., atol=1e-8)
    q = np.asarray(driven["flows_mL_per_s"])
    assert q[:, 1].max() > 1
    assert np.all(q[:, [0, 1, 4, 5]] >= 0)
    assert driven["maximum_volume_drift_mL"] < 1e-8
    p["edge_resistance_mmHg_s_per_mL"][0] = 0
    with pytest.raises(ValueError, match="resistances"):
        simulate_closed_loop(p)
