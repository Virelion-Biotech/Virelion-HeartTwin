"""Patient-state, curve calibration and native handoff regressions; run in Colab."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from hearttwin.patient_ingest import import_observation
from hearttwin.patient_observations import normalize_observation, sample_prediction
from hearttwin.patient_personalization import (
    PatientPlanJob,
    execute_patient_plan,
    longitudinal_changes,
    validate_patient_plan,
)
from hearttwin.patient_recipes import circulation_recipe


def imported(tmp_path, *, role="context", curve=False, input_id="input-pressure"):
    raw = json.dumps(
        {"values": [80, 85, 82], "time_s": [0, 0.5, 1], "unit": "mmHg"}
        if curve
        else {"value": 80, "unit": "mmHg"}
    ).encode()
    path = tmp_path / "original.json"
    path.write_bytes(raw)
    source = {
        "file_id": "original",
        "uri": path.as_uri(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    spec = {
        "encounter_id": "visit1",
        "acquisition_id": "pressure1",
        "evidence_id": "ev1",
        "format": "json",
        "unit": "mmHg",
        "role": role,
        "modality": "pressure",
        "domain": "circulation",
        "variable": "pressure",
        "input_id": input_id,
    }
    case, normalized = import_observation(
        {"patient_id": "P1", "encounters": [{"encounter_id": "visit1"}]},
        source,
        spec,
        output_dir=tmp_path / "normalized",
    )
    return case, normalized


def plan(tmp_path):
    case, _ = imported(tmp_path)
    return {
        "case": case.model_dump(mode="json"),
        "case_revision": 1,
        "case_fingerprint": "a" * 64,
        "encounter_id": "visit1",
        "stages": [
            {
                "stage_id": "flow",
                "capability": "flow.simulate",
                "purpose": "test pressure handoff",
                "template": {
                    "subject_id": "P1",
                    "pressure": {"$input": "input-pressure", "unit": "mmHg"},
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"values":[true],"unit":"mmHg"}',
        b'{"values":[NaN],"unit":"mmHg"}',
        b'{"values":[1,2],"time_s":[0,0],"unit":"mmHg"}',
        b'{"values":[1,2],"labels":["A","A"],"unit":"mmHg"}',
        b'{"value":2,"unit":"Pa"}',
        b'{"value":2,"value":3,"unit":"mmHg"}',
    ],
)
def test_import_rejects_invalid_or_ambiguous_observations(raw):
    with pytest.raises(ValueError):
        normalize_observation(raw, format="json", unit="mmHg")


def test_csv_explicit_time_and_unit():
    result = normalize_observation(
        b"time_s,value\n0,80\n0.5,90\n", format="csv", unit="mmHg"
    )
    assert result == {"values": [80.0, 90.0], "time_s": [0.0, 0.5], "unit": "mmHg"}
    with pytest.raises(ValueError):
        normalize_observation(b"time_ms,value\n0,80\n1,90", format="csv", unit="mmHg")


def test_import_retains_original_and_normalized_hashes(tmp_path):
    case, normalized = imported(tmp_path, curve=True)
    assert len(case.acquisitions[0].source_files) == 2
    assert (
        normalized["metadata"]["derived_from_sha256"]
        == case.acquisitions[0].source_files[0].sha256
    )
    assert case.model_inputs[0].value["time_s"] == [0.0, 0.5, 1.0]
    assert case.model_inputs[0].status == "MEASURED"


def test_import_holdout_cannot_become_input(tmp_path):
    with pytest.raises(ValueError, match="Held-out"):
        imported(tmp_path, role="validation")


@pytest.mark.parametrize(
    "mutation",
    ["unit", "unknown", "identity", "future", "literal", "heldout", "missing_hash"],
)
def test_plan_fail_closed(tmp_path, mutation):
    payload = plan(tmp_path)
    if mutation == "unit":
        payload["stages"][0]["template"]["pressure"]["unit"] = "Pa"
    if mutation == "unknown":
        payload["case"]["model_inputs"][0].update(status="UNKNOWN", value=None)
    if mutation == "identity":
        payload["stages"][0]["template"]["subject_id"] = "P2"
    if mutation == "future":
        payload["stages"][0]["template"]["pressure"] = {"$result": "later.pressure"}
    if mutation == "literal":
        payload["stages"][0]["template"]["compliance"] = 1.5
    if mutation == "heldout":
        payload["case"]["evidence"][0].update(role="validation", withheld_from_fit=True)
    if mutation == "missing_hash":
        payload["case"]["acquisitions"][0]["source_files"][0]["sha256"] = None
    with pytest.raises(ValueError):
        validate_patient_plan(PatientPlanJob.model_validate(payload), verify=True)


def test_followup_requires_explicit_carry_forward(tmp_path):
    payload = plan(tmp_path)
    payload["case"]["encounters"].append(
        {"encounter_id": "visit2", "physiologic_state": {"rhythm": "changed"}}
    )
    payload["encounter_id"] = "visit2"
    with pytest.raises(ValueError, match="carry-forward"):
        validate_patient_plan(PatientPlanJob.model_validate(payload))
    payload["carry_forward"] = {
        "pressure1": "Explicit research comparison under baseline loading"
    }
    assert validate_patient_plan(PatientPlanJob.model_validate(payload))


def test_patient_native_handoff_is_durable_and_identity_bound(tmp_path):
    payload = plan(tmp_path)
    payload["stages"].append(
        {
            "stage_id": "therapy",
            "capability": "therapy.run",
            "purpose": "Research intervention comparison",
            "template": {"subject_id": "P1", "baseline": {"$result": "flow.pressure"}},
        }
    )
    calls = []

    def invoke(capability, request):
        calls.append((capability, request))
        return {
            "subject_id": "P1",
            "pressure": request.get("pressure", request.get("baseline")),
        }

    adapter = SimpleNamespace(available=lambda: True, invoke=invoke)
    registry = SimpleNamespace(capability=lambda _: adapter)
    report = execute_patient_plan(payload, registry, output_dir=tmp_path / "run")
    assert calls[1][1]["baseline"] == 80
    assert report["case_revision"] == 1
    assert (tmp_path / "run/flow-result.json").is_file()
    assert (
        report["personalization_manifest"][1]["bindings"][0]["upstream_stage"] == "flow"
    )


def test_longitudinal_state_change_never_reuses_fit(tmp_path):
    case, _ = imported(tmp_path)
    newer = case.model_dump(mode="json")
    newer["encounters"][0]["physiologic_state"] = {"medications": "changed"}
    result = longitudinal_changes(case, newer)
    assert result["requires_new_run"] and not result["prior_fit_reusable_automatically"]
    assert result["changed"]["encounters"] == ["visit1"]


def test_curve_sampling_and_field_correspondence():
    prediction = {
        "series_outputs": {"pressure": [80, 100, 80], "time_s": [0, 0.5, 1]},
        "units": {"pressure": "mmHg"},
    }
    spec = {
        "model_output": "series_outputs.pressure",
        "model_time": "series_outputs.time_s",
        "unit": "mmHg",
        "time_s": [0.25, 0.75],
    }
    assert sample_prediction(prediction, spec) == [90, 90]
    with pytest.raises(ValueError, match="extrapolation"):
        sample_prediction(prediction, {**spec, "time_s": [1.1]})
    with pytest.raises(ValueError, match="units"):
        sample_prediction(prediction, {**spec, "unit": "Pa"})
    field = {
        "values": [2.0, 3.0],
        "ids": ["node-B", "node-A"],
        "units": {"values": "1"},
    }
    assert sample_prediction(
        field,
        {
            "model_output": "values",
            "model_labels": "ids",
            "unit": "1",
            "labels": ["node-A"],
        },
    ) == [3.0]


def test_recipe_requires_all_inputs_and_exposes_assumptions(tmp_path):
    case, _ = imported(tmp_path)
    with pytest.raises(ValueError, match="proximal_resistance"):
        circulation_recipe(case, "visit1")


def test_real_patient_curve_inverse_loop(tmp_path):
    pytest.importorskip("cardiinfer")
    pytest.importorskip("cardiflow")
    from cardiflow.api import FlowAPI
    from hearttwin.config import load_registry
    from hearttwin.patient_domain import execute_patient_domain

    template = {
        "subject_id": "P1",
        "backend": "windkessel-3element-v1",
        "domain": {
            "domain_id": "0d",
            "region": "afterload",
            "anatomy_ref": {
                "artifact_id": "P1-domain",
                "kind": "reduced_order_domain",
                "uri": "memory://0d",
            },
        },
        "fluid": {"density": 1060, "dynamic_viscosity": 0.0035},
        "boundary_conditions": [
            {
                "boundary_id": "afterload",
                "kind": "windkessel",
                "region": "aorta",
                "parameters": {
                    "proximal_resistance": 0.08,
                    "distal_resistance": 1.1,
                    "compliance": 1.7,
                    "distal_pressure_mmHg": 4.0,
                },
            }
        ],
        "settings": {
            "inlet_flow": [0, 30, 80, 120, 70, 20, 0, 0, 0, 0, 0],
            "dt_s": 0.1,
            "initial_capacitor_pressure": 80,
        },
    }
    truth = FlowAPI().simulate(template)["series_outputs"]
    acquisitions, evidence, targets = [], [], []
    for name, role, delta in [("fit", "calibration", 0), ("hold", "validation", 0.01)]:
        path = tmp_path / (name + ".json")
        raw = json.dumps(
            {
                "values": [x + delta for x in truth["outlet_pressure"]],
                "time_s": truth["time_s"],
                "unit": "mmHg",
            }
        ).encode()
        path.write_bytes(raw)
        acquisitions.append(
            {
                "acquisition_id": name,
                "subject_id": "P1",
                "encounter_id": "visit1",
                "modality": "pressure",
                "source_files": [
                    {
                        "file_id": name,
                        "uri": path.as_uri(),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                ],
            }
        )
        evidence.append(
            {
                "evidence_id": name,
                "subject_id": "P1",
                "acquisition_id": name,
                "data_kind": "pressure",
                "role": role,
                "withheld_from_fit": role == "validation",
            }
        )
        targets.append(
            {
                "evidence_id": name,
                "source_file_id": name,
                "model_output": "series_outputs.outlet_pressure",
                "model_time": "series_outputs.time_s",
                "observation_kind": "curve",
                "unit": "mmHg",
                "noise_sd": 2.0,
                "max_abs_error": 10.0,
            }
        )
    payload = {
        "case": {
            "patient_id": "P1",
            "encounters": [{"encounter_id": "visit1"}],
            "acquisitions": acquisitions,
            "evidence": evidence,
        },
        "case_revision": 1,
        "case_fingerprint": "a" * 64,
        "encounter_id": "visit1",
        "domain": "circulation",
        "forward_template": template,
        "parameter_paths": {"r": "boundary_conditions.0.parameters.distal_resistance"},
        "parameter_bounds": {"r": [0.5, 2.0]},
        "parameter_units": {"r": "mmHg*s/mL"},
        "targets": targets,
        "sampler_settings": {"population_size": 6, "generations": 2},
    }
    report = execute_patient_domain(
        payload, load_registry(), output_dir=tmp_path / "curve-run"
    )
    assert report["heldout_acceptance_passed"]
    request = json.loads((tmp_path / "curve-run/inference-request.json").read_text())
    assert "hold" not in json.dumps(request)
    assert report["heldout_residuals"][0]["observation_kind"] == "curve"
    assert len(report["heldout_residuals"][0]["error"]) == 11


def test_http_visit_import_and_revision_bound_plan(tmp_path):
    import http.client
    import io
    import threading
    from hearttwin.web import Workbench, make_server

    token = "owner-" + "x" * 40
    app = Workbench(tmp_path / "workbench", token)
    app.patients.save(
        {"patient_id": "P1"}, expected_revision=0, actor="owner", reason="create"
    )
    raw = b'{"value":80,"unit":"mmHg"}'
    receipt = app.upload(io.BytesIO(raw), "pressure.json", len(raw))
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(suffix, payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/patients/P1/" + suffix,
            body=json.dumps(payload),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        code = response.status
        data = json.loads(response.read())
        connection.close()
        return code, data

    try:
        assert (
            post(
                "encounters",
                {"expected_revision": 1, "encounter": {"encounter_id": "visit1"}},
            )[0]
            == 201
        )
        assert (
            post(
                "encounters",
                {"expected_revision": 1, "encounter": {"encounter_id": "stale"}},
            )[0]
            == 409
        )
        spec = {
            "encounter_id": "visit1",
            "acquisition_id": "a1",
            "evidence_id": "e1",
            "format": "json",
            "unit": "mmHg",
            "role": "context",
            "modality": "pressure",
            "domain": "circulation",
            "variable": "pressure",
            "input_id": "pressure",
        }
        code, snapshot = post(
            "observations",
            {"expected_revision": 2, "upload_id": receipt["id"], "observation": spec},
        )
        assert code == 201 and snapshot["revision"] == 3
        request = {
            "patient_id": "P1",
            "case_revision": 3,
            "encounter_id": "visit1",
            "stages": [
                {
                    "stage_id": "flow",
                    "capability": "flow.simulate",
                    "purpose": "patient pressure handoff",
                    "template": {
                        "subject_id": "P1",
                        "pressure": {"$input": "pressure", "unit": "mmHg"},
                    },
                }
            ],
        }
        assert post("plan-preview", request)[0] == 200
        queued = app.submit(
            app.patient_request({"kind": "patient_plan", "payload": request}),
            "patient-plan-request",
        )
        binding = app.patients.runs("P1")[0]
        assert binding["run_id"] == queued["job_id"] and binding["revision"] == 3
        assert post("longitudinal", {"older": 1, "newer": 3})[1]["requires_new_run"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


def test_native_spatial_field_requires_pinned_mesh_correspondence(tmp_path):
    raw = json.dumps(
        {
            "subject_id": "P1",
            "anatomy_sha256": "b" * 64,
            "green_strain": [[[0.1, 0, 0], [0, 0.2, 0], [0, 0, 0.3]]],
        }
    ).encode()
    path = tmp_path / "fields.json"
    path.write_bytes(raw)
    prediction = {
        "subject_id": "P1",
        "outputs": [
            {
                "artifact_id": "fem",
                "kind": "spatial_mechanics_fields",
                "uri": path.as_uri(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    spec = {
        "model_output": "native-field",
        "field_quantity": "green_strain_xx",
        "field_anatomy_sha256": "b" * 64,
        "unit": "1",
        "labels": ["element:0"],
    }
    assert sample_prediction(prediction, spec) == [0.1]
    with pytest.raises(ValueError, match="correspondence"):
        sample_prediction(prediction, {**spec, "field_anatomy_sha256": "c" * 64})


@pytest.mark.parametrize("kappa", [1000., 1e7])
def test_real_fem_field_replay_keeps_native_settings_and_mesh_binding(tmp_path, kappa):
    pytest.importorskip("cardimech")
    pytest.importorskip("scipy")
    from cardimech.fem_examples import cube_mesh
    from hearttwin.config import load_registry
    from hearttwin.patient_domain import execute_patient_domain

    mesh = cube_mesh()
    mesh["subject_id"] = "P1"
    raw = json.dumps(mesh).encode()
    mesh_path = tmp_path / "mesh.json"
    mesh_path.write_bytes(raw)
    mesh_hash = hashlib.sha256(raw).hexdigest()
    anatomy = {"artifact_id": "mesh", "kind": "tetra_mechanics_mesh", "uri": mesh_path.as_uri(), "sha256": mesh_hash}
    acquisitions = [{"acquisition_id": "mesh-acq", "subject_id": "P1", "modality": "mri", "source_files": [{"file_id": "mesh", "uri": mesh_path.as_uri(), "sha256": mesh_hash}]}]
    evidence, targets = [], []
    for name, role, observed in [("fit", "calibration", .0202), ("hold", "validation", .0203)]:
        path = tmp_path / (name + "-field.json")
        data = json.dumps({"values": [observed], "labels": ["element:0"], "unit": "1"}).encode()
        path.write_bytes(data)
        acquisitions.append({"acquisition_id": name, "subject_id": "P1", "modality": "echo", "source_files": [{"file_id": name, "uri": path.as_uri(), "sha256": hashlib.sha256(data).hexdigest()}]})
        evidence.append({"evidence_id": name, "subject_id": "P1", "acquisition_id": name, "role": role, "data_kind": "strain", "withheld_from_fit": role == "validation"})
        targets.append({"evidence_id": name, "source_file_id": name, "observation_kind": "field", "field_quantity": "green_strain_xx", "field_anatomy_sha256": mesh_hash, "model_output": "native-field", "unit": "1", "noise_sd": .01, "max_abs_error": .001})
    boundaries = [{"boundary_id": str(i), "kind": "fixed", "region": "boundary", "unit": "m", "metadata": {"nodes": [i], "displacement": [.02*x[0], -.01*x[1], .03*x[2]]}} for i, x in enumerate(mesh["nodes"])]
    template = {"subject_id": "P1", "backend": "scipy-tetra-v1", "anatomy_ref": anatomy, "parameters": {"material_model": "neo_hookean", "active_model": "constant_fiber", "passive": {"mu": 1000., "kappa": kappa}, "units": {"mu": "Pa", "kappa": "Pa"}}, "boundary_conditions": boundaries, "settings": {"load_steps": 1}}
    actual = load_registry()
    inference = SimpleNamespace(available=lambda: True, invoke=lambda *_: {"subject_id": "P1", "diagnostics": {"best_parameters": {"mu": 1000.}}})
    registry = SimpleNamespace(capability=lambda name: inference if name == "infer.run" else actual.capability(name))
    payload = {"case": {"patient_id": "P1", "acquisitions": acquisitions, "evidence": evidence}, "case_revision": 1, "case_fingerprint": "a"*64, "domain": "mechanics", "forward_template": template, "parameter_paths": {"mu": "parameters.passive.mu"}, "parameter_bounds": {"mu": [500., 1500.]}, "parameter_units": {"mu": "Pa"}, "targets": targets}
    report = execute_patient_domain(payload, registry, output_dir=tmp_path / "fem-replay")
    assert report["prediction"]["provenance"]["formulation"] == ("mixed_mini" if kappa == 1e7 else "displacement_p1")
    assert report["heldout_acceptance_passed"]
    assert report["heldout_residuals"][0]["predicted"][0] == pytest.approx(.0202)
    # This fixture tests native FEM replay and observation correspondence, not material identifiability.


def test_real_circulation_recipe_consumes_case_inputs(tmp_path):
    pytest.importorskip("cardiflow")
    from hearttwin.config import load_registry

    raw = b'time_s,value\n0,0\n0.1,30\n0.2,80\n0.3,120\n0.4,70\n0.5,20\n0.6,0\n'
    path = tmp_path / "measured-flow.csv"
    path.write_bytes(raw)
    source = {"file_id": "flow-raw", "uri": path.as_uri(), "sha256": hashlib.sha256(raw).hexdigest()}
    spec = {"encounter_id": "visit1", "acquisition_id": "flow-acq", "evidence_id": "flow-evidence", "format": "csv", "unit": "mL/s", "role": "context", "modality": "echo", "domain": "circulation", "variable": "inlet_flow", "input_id": "measured-flow"}
    case, _ = import_observation({"patient_id": "P1", "encounters": [{"encounter_id": "visit1"}]}, source, spec, output_dir=tmp_path / "normalized")
    data = case.model_dump(mode="json")
    for variable, value, unit in [("proximal_resistance", .08, "mmHg*s/mL"), ("distal_resistance", 1.1, "mmHg*s/mL"), ("compliance", 1.7, "mL/mmHg"), ("distal_pressure_mmHg", 4., "mmHg"), ("initial_capacitor_pressure", 80., "mmHg")]:
        data["model_inputs"].append({"input_id": variable, "domain": "circulation", "variable": variable, "value": value, "unit": unit, "status": "ASSUMED", "assumption_reason": "Explicit synthetic fixture parameter; not patient measured"})
    payload = {**circulation_recipe(data, "visit1"), "case": data, "case_revision": 1, "case_fingerprint": "a"*64}
    report = execute_patient_plan(payload, load_registry(), output_dir=tmp_path / "native-plan")
    assert report["stages"]["circulation"]["qc"]["passed"]
    bindings = report["personalization_manifest"][0]["bindings"]
    assert any(x.get("input_id") == "measured-flow" and x["status"] == "MEASURED" for x in bindings)
    assert sum(x["status"] == "ASSUMED" for x in bindings) == 5
    assert report["stages"]["circulation"]["provenance"]["anatomy_consumed"] is False


@pytest.mark.parametrize("accepted", [True, False])
def test_composite_calibration_handoff_inherits_frozen_case(tmp_path, monkeypatch, accepted):
    import hearttwin.patient_domain as domain

    payload = plan(tmp_path)
    payload["case"]["evidence"][0].update(role="calibration")
    payload["stages"] = [{"stage_id": "fit", "capability": "patient.calibrate", "purpose": "Fit on frozen patient evidence", "template": {"subject_id": "P1", "domain": "circulation", "targets": [{"evidence_id": "ev1"}]}}]
    payload["stages"].append({"stage_id": "flow", "capability": "flow.simulate", "purpose": "Use fitted parameter without manual copying", "template": {"subject_id": "P1", "resistance": {"$result": "fit.inference.diagnostics.best_parameters.r"}}})
    calls = []
    def calibrated(request, registry, *, output_dir):
        assert request["case"] == payload["case"]
        assert request["case_revision"] == 1
        assert request["case_fingerprint"] == "a"*64
        assert request["encounter_id"] == "visit1"
        return {"patient_id": "P1", "heldout_acceptance_passed": accepted, "inference": {"diagnostics": {"best_parameters": {"r": 1.3}}}}
    monkeypatch.setattr(domain, "execute_patient_domain", calibrated)
    adapter = SimpleNamespace(available=lambda: True, invoke=lambda capability, request: calls.append(request) or {"subject_id": "P1"})
    registry = SimpleNamespace(capability=lambda name: adapter)
    if not accepted:
        with pytest.raises(ValueError, match="downstream"):
            execute_patient_plan(payload, registry, output_dir=tmp_path / "composite")
        assert calls == []
        assert (tmp_path / "composite/fit-result.json").is_file()
        return
    report = execute_patient_plan(payload, registry, output_dir=tmp_path / "composite")
    assert calls[0]["resistance"] == 1.3
    assert "fit" in report["stages"]
    payload["stages"][0]["template"]["case_revision"] = 99
    with pytest.raises(ValueError, match="inherit"):
        validate_patient_plan(PatientPlanJob.model_validate(payload))


def test_patient_plan_rejects_unbound_plain_source_reference():
    from hearttwin.patient_personalization import PatientPlanJob, resolve_patient_template

    job = PatientPlanJob(case={"patient_id": "P1", "encounters": [{"encounter_id": "v"}]}, case_revision=1,
                         case_fingerprint="0" * 64, encounter_id="v", stages=[{"stage_id": "s", "capability": "patient.images.segment", "purpose": "test", "template": {}}])
    with pytest.raises(ValueError, match="must use"):
        resolve_patient_template({"uri": "file:///unbound-image", "sha256": "0" * 64}, job)
