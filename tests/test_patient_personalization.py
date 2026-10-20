"""Patient-state, curve calibration and native handoff regressions; run in Colab."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from hearttwin.patient_ingest import import_observation
from hearttwin.patient_observations import normalize_observation, sample_prediction
from hearttwin.patient_personalization import (
    PatientPlanJob, execute_patient_plan, longitudinal_changes, validate_patient_plan,
)
from hearttwin.patient_recipes import circulation_recipe


def imported(tmp_path, *, role="context", curve=False, input_id="input-pressure"):
    raw = json.dumps({"values": [80, 85, 82], "time_s": [0, .5, 1], "unit": "mmHg"}
                     if curve else {"value": 80, "unit": "mmHg"}).encode()
    path = tmp_path / "original.json"
    path.write_bytes(raw)
    source = {"file_id": "original", "uri": path.as_uri(), "sha256": hashlib.sha256(raw).hexdigest()}
    spec = {"encounter_id": "visit1", "acquisition_id": "pressure1", "evidence_id": "ev1",
            "format": "json", "unit": "mmHg", "role": role, "modality": "pressure",
            "domain": "circulation", "variable": "pressure", "input_id": input_id}
    case, normalized = import_observation({"patient_id": "P1", "encounters": [{"encounter_id": "visit1"}]},
                          source, spec, output_dir=tmp_path / "normalized")
    return case, normalized


def plan(tmp_path):
    case, _ = imported(tmp_path)
    return {"case": case.model_dump(mode="json"), "case_revision": 1, "case_fingerprint": "a"*64,
            "encounter_id": "visit1", "stages": [{"stage_id": "flow", "capability": "flow.simulate",
             "purpose": "test pressure handoff", "template": {"subject_id": "P1", "pressure": {"$input": "input-pressure", "unit": "mmHg"}}}]}


@pytest.mark.parametrize("raw", [b'{"values":[true],"unit":"mmHg"}', b'{"values":[NaN],"unit":"mmHg"}',
    b'{"values":[1,2],"time_s":[0,0],"unit":"mmHg"}', b'{"values":[1,2],"labels":["A","A"],"unit":"mmHg"}',
    b'{"value":2,"unit":"Pa"}', b'{"value":2,"value":3,"unit":"mmHg"}'])
def test_import_rejects_invalid_or_ambiguous_observations(raw):
    with pytest.raises(ValueError):
        normalize_observation(raw, format="json", unit="mmHg")


def test_csv_explicit_time_and_unit():
    result = normalize_observation(b'time_s,value\n0,80\n0.5,90\n', format="csv", unit="mmHg")
    assert result == {"values": [80., 90.], "time_s": [0., .5], "unit": "mmHg"}
    with pytest.raises(ValueError):
        normalize_observation(b'time_ms,value\n0,80\n1,90', format="csv", unit="mmHg")


def test_import_retains_original_and_normalized_hashes(tmp_path):
    case, normalized = imported(tmp_path, curve=True)
    assert len(case.acquisitions[0].source_files) == 2
    assert normalized["metadata"]["derived_from_sha256"] == case.acquisitions[0].source_files[0].sha256
    assert case.model_inputs[0].value["time_s"] == [0., .5, 1.]
    assert case.model_inputs[0].status == "MEASURED"


def test_import_holdout_cannot_become_input(tmp_path):
    with pytest.raises(ValueError, match="Held-out"):
        imported(tmp_path, role="validation")


@pytest.mark.parametrize("mutation", ["unit", "unknown", "identity", "future", "literal", "heldout", "missing_hash"])
def test_plan_fail_closed(tmp_path, mutation):
    payload = plan(tmp_path)
    if mutation == "unit": payload["stages"][0]["template"]["pressure"]["unit"] = "Pa"
    if mutation == "unknown": payload["case"]["model_inputs"][0].update(status="UNKNOWN", value=None)
    if mutation == "identity": payload["stages"][0]["template"]["subject_id"] = "P2"
    if mutation == "future": payload["stages"][0]["template"]["pressure"] = {"$result": "later.pressure"}
    if mutation == "literal": payload["stages"][0]["template"]["compliance"] = 1.5
    if mutation == "heldout": payload["case"]["evidence"][0].update(role="validation", withheld_from_fit=True)
    if mutation == "missing_hash": payload["case"]["acquisitions"][0]["source_files"][0]["sha256"] = None
    with pytest.raises(ValueError): validate_patient_plan(PatientPlanJob.model_validate(payload), verify=True)


def test_followup_requires_explicit_carry_forward(tmp_path):
    payload = plan(tmp_path)
    payload["case"]["encounters"].append({"encounter_id": "visit2", "physiologic_state": {"rhythm": "changed"}})
    payload["encounter_id"] = "visit2"
    with pytest.raises(ValueError, match="carry-forward"):
        validate_patient_plan(PatientPlanJob.model_validate(payload))
    payload["carry_forward"] = {"pressure1": "Explicit research comparison under baseline loading"}
    assert validate_patient_plan(PatientPlanJob.model_validate(payload))


def test_patient_native_handoff_is_durable_and_identity_bound(tmp_path):
    payload = plan(tmp_path)
    payload["stages"].append({"stage_id": "therapy", "capability": "therapy.run", "purpose": "Research intervention comparison",
         "template": {"subject_id": "P1", "baseline": {"$result": "flow.pressure"}}})
    calls = []
    def invoke(capability, request):
        calls.append((capability, request))
        return {"subject_id": "P1", "pressure": request.get("pressure", request.get("baseline"))}
    adapter = SimpleNamespace(available=lambda: True, invoke=invoke)
    registry = SimpleNamespace(capability=lambda _: adapter)
    report = execute_patient_plan(payload, registry, output_dir=tmp_path / "run")
    assert calls[1][1]["baseline"] == 80
    assert report["case_revision"] == 1
    assert (tmp_path / "run/flow-result.json").is_file()
    assert report["personalization_manifest"][1]["bindings"][0]["upstream_stage"] == "flow"


def test_longitudinal_state_change_never_reuses_fit(tmp_path):
    case, _ = imported(tmp_path)
    newer = case.model_dump(mode="json")
    newer["encounters"][0]["physiologic_state"] = {"medications": "changed"}
    result = longitudinal_changes(case, newer)
    assert result["requires_new_run"] and not result["prior_fit_reusable_automatically"]
    assert result["changed"]["encounters"] == ["visit1"]


def test_curve_sampling_and_field_correspondence():
    prediction = {"series_outputs": {"pressure": [80, 100, 80], "time_s": [0, .5, 1]}, "units": {"pressure": "mmHg"}}
    spec = {"model_output": "series_outputs.pressure", "model_time": "series_outputs.time_s", "unit": "mmHg", "time_s": [.25,.75]}
    assert sample_prediction(prediction, spec) == [90, 90]
    with pytest.raises(ValueError, match="extrapolation"):
        sample_prediction(prediction, {**spec, "time_s": [1.1]})
    with pytest.raises(ValueError, match="units"):
        sample_prediction(prediction, {**spec, "unit": "Pa"})
    field = {"values": [2.,3.], "ids": ["node-B", "node-A"], "units": {"values": "1"}}
    assert sample_prediction(field, {"model_output": "values", "model_labels": "ids", "unit": "1", "labels": ["node-A"]}) == [3.]


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
    template = {"subject_id":"P1", "backend":"windkessel-3element-v1",
        "domain":{"domain_id":"0d","region":"afterload","anatomy_ref":{"artifact_id":"P1-domain","kind":"reduced_order_domain","uri":"memory://0d"}},
        "fluid":{"density":1060,"dynamic_viscosity":.0035},
        "boundary_conditions":[{"boundary_id":"afterload","kind":"windkessel","region":"aorta",
             "parameters":{"proximal_resistance":.08,"distal_resistance":1.1,"compliance":1.7,"distal_pressure_mmHg":4.}}],
        "settings":{"inlet_flow":[0,30,80,120,70,20,0,0,0,0,0],"dt_s":.1,"initial_capacitor_pressure":80}}
    truth = FlowAPI().simulate(template)["series_outputs"]
    acquisitions, evidence, targets = [], [], []
    for name, role, delta in [("fit", "calibration", 0), ("hold", "validation", .01)]:
        path=tmp_path/(name+".json")
        raw=json.dumps({"values":[x+delta for x in truth["outlet_pressure"]], "time_s":truth["time_s"],"unit":"mmHg"}).encode()
        path.write_bytes(raw)
        acquisitions.append({"acquisition_id":name,"subject_id":"P1","encounter_id":"visit1","modality":"pressure",
             "source_files":[{"file_id":name,"uri":path.as_uri(),"sha256":hashlib.sha256(raw).hexdigest()}]})
        evidence.append({"evidence_id":name,"subject_id":"P1","acquisition_id":name,"data_kind":"pressure","role":role,"withheld_from_fit":role=="validation"})
        targets.append({"evidence_id":name,"source_file_id":name,"model_output":"series_outputs.outlet_pressure",
           "model_time":"series_outputs.time_s","observation_kind":"curve","unit":"mmHg","noise_sd":2.,"max_abs_error":10.})
    payload={"case":{"patient_id":"P1","encounters":[{"encounter_id":"visit1"}],"acquisitions":acquisitions,"evidence":evidence},
        "case_revision":1,"case_fingerprint":"a"*64,"encounter_id":"visit1","domain":"circulation","forward_template":template,
        "parameter_paths":{"r":"boundary_conditions.0.parameters.distal_resistance"},"parameter_bounds":{"r":[.5,2.]},
        "parameter_units":{"r":"mmHg*s/mL"},"targets":targets,"sampler_settings":{"population_size":6,"generations":2}}
    report=execute_patient_domain(payload,load_registry(),output_dir=tmp_path/"curve-run")
    assert report["heldout_acceptance_passed"]
    request=json.loads((tmp_path/"curve-run/inference-request.json").read_text())
    assert "hold" not in json.dumps(request)
    assert report["heldout_residuals"][0]["observation_kind"] == "curve"
    assert len(report["heldout_residuals"][0]["error"]) == 11


def test_http_visit_import_and_revision_bound_plan(tmp_path):
    import http.client
    import io
    import threading
    from hearttwin.web import Workbench, make_server
    token = "owner-" + "x"*40
    app = Workbench(tmp_path / "workbench", token)
    app.patients.save({"patient_id":"P1"}, expected_revision=0, actor="owner", reason="create")
    raw=b'{"value":80,"unit":"mmHg"}'
    receipt=app.upload(io.BytesIO(raw),"pressure.json",len(raw))
    server=make_server(app,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def post(suffix, payload):
        connection=http.client.HTTPConnection("127.0.0.1",server.server_port)
        connection.request("POST","/api/patients/P1/"+suffix,body=json.dumps(payload),
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        response=connection.getresponse();code=response.status;data=json.loads(response.read());connection.close()
        return code,data
    try:
        assert post("encounters",{"expected_revision":1,"encounter":{"encounter_id":"visit1"}})[0] == 201
        assert post("encounters",{"expected_revision":1,"encounter":{"encounter_id":"stale"}})[0] == 409
        spec={"encounter_id":"visit1","acquisition_id":"a1","evidence_id":"e1","format":"json","unit":"mmHg",
             "role":"context","modality":"pressure","domain":"circulation","variable":"pressure","input_id":"pressure"}
        code,snapshot=post("observations",{"expected_revision":2,"upload_id":receipt["id"],"observation":spec})
        assert code == 201 and snapshot["revision"] == 3
        request={"patient_id":"P1","case_revision":3,"encounter_id":"visit1","stages":[{"stage_id":"flow",
            "capability":"flow.simulate","purpose":"patient pressure handoff","template":{"subject_id":"P1",
            "pressure":{"$input":"pressure","unit":"mmHg"}}}]}
        assert post("plan-preview",request)[0] == 200
        queued=app.submit(app.patient_request({"kind":"patient_plan","payload":request}),"patient-plan-request")
        binding=app.patients.runs("P1")[0]
        assert binding["run_id"] == queued["job_id"] and binding["revision"] == 3
        assert post("longitudinal",{"older":1,"newer":3})[1]["requires_new_run"]
    finally:
        server.shutdown();server.server_close();thread.join();app.close()


def test_native_spatial_field_requires_pinned_mesh_correspondence(tmp_path):
    raw=json.dumps({"subject_id":"P1","anatomy_sha256":"b"*64,"green_strain":[[[.1,0,0],[0,.2,0],[0,0,.3]]]}).encode()
    path=tmp_path/"fields.json";path.write_bytes(raw)
    prediction={"subject_id":"P1","outputs":[{"artifact_id":"fem","kind":"spatial_mechanics_fields","uri":path.as_uri(),"sha256":hashlib.sha256(raw).hexdigest()}]}
    spec={"model_output":"native-field","field_quantity":"green_strain_xx","field_anatomy_sha256":"b"*64,"unit":"1","labels":["element:0"]}
    assert sample_prediction(prediction,spec) == [.1]
    with pytest.raises(ValueError,match="correspondence"):
        sample_prediction(prediction,{**spec,"field_anatomy_sha256":"c"*64})
