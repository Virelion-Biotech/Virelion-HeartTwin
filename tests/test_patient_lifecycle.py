"""Persistent patient lineage, access boundaries and domain execution regressions."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from types import SimpleNamespace

import pytest

from hearttwin.patient_access import PatientAccess
from hearttwin.patient_store import PatientCaseStore, RevisionConflict
from hearttwin.patient_model import PatientCase
from hearttwin.patient_domain import (
    PatientDomainJob,
    execute_patient_domain,
    validate_domain_job,
)
from hearttwin.web import Workbench
from hearttwin.web_job import execute


def saved(tmp_path):
    store = PatientCaseStore(tmp_path / "cases.db")
    first = store.save(
        {"patient_id": "P1"}, expected_revision=0, actor="owner", reason="create"
    )
    return store, first


def test_revisions_survive_restart_and_conflicts(tmp_path):
    store, first = saved(tmp_path)
    case = deepcopy(first["case"])
    case["encounters"].append({"encounter_id": "visit2"})
    second = store.save(case, expected_revision=1, actor="owner", reason="follow-up")
    assert second["parent_fingerprint"] == first["fingerprint"]
    reopened = PatientCaseStore(store.path)
    assert reopened.get("P1", 1) == first
    assert reopened.get("P1")["revision"] == 2
    assert reopened.compare("P1", 1, 2)["changes"]["encounters"]["added"] == ["visit2"]
    with pytest.raises(RevisionConflict):
        reopened.save(case, expected_revision=1, actor="owner", reason="stale")


def test_concurrent_revision_has_one_winner(tmp_path):
    store, first = saved(tmp_path)

    def update(i):
        case = deepcopy(first["case"])
        case["metadata"]["writer"] = i
        try:
            return store.save(case, expected_revision=1, actor="owner", reason=str(i))[
                "revision"
            ]
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(update, [1, 2]))
    assert sorted(map(str, values)) == ["2", "conflict"]


def test_revision_and_event_tampering_rejected(tmp_path):
    store, first = saved(tmp_path)
    store.event(
        "P1",
        1,
        kind="review",
        actor="reviewer",
        detail={"decision": "approved", "rationale": "checked source segmentation"},
    )
    assert store.events("P1")[0]["case_fingerprint"] == first["fingerprint"]
    with store.connect() as db:
        db.execute(
            "UPDATE patient_events SET envelope=replace(envelope,'approved','rejected')"
        )
    with pytest.raises(ValueError, match="integrity"):
        store.events("P1")
    with store.connect() as db:
        db.execute(
            "UPDATE patient_revisions SET envelope=replace(envelope,'create','altered')"
        )
    with pytest.raises(ValueError, match="integrity"):
        store.get("P1")


def test_review_is_revision_specific_archive_reversible(tmp_path):
    store, first = saved(tmp_path)
    store.event(
        "P1",
        1,
        kind="review",
        actor="reviewer",
        detail={"decision": "approved", "rationale": "reviewed"},
    )
    store.save(first["case"], expected_revision=1, actor="owner", reason="new version")
    assert store.readiness("P1")["reviews"] == []
    store.event("P1", 2, kind="archive", actor="owner", detail={})
    assert store.list_cases()[0]["archived"] is True
    store.event("P1", 2, kind="restore", actor="owner", detail={})
    assert store.list_cases()[0]["archived"] is False
    assert store.get("P1", 1) == first


def test_run_binding_cannot_be_reassigned(tmp_path):
    store, first = saved(tmp_path)
    request = {"kind": "patient_ep", "payload": {"case_revision": 1}}
    a = store.bind_run("run1", "P1", 1, request)
    assert store.bind_run("run1", "P1", 1, request) == a
    store.save(first["case"], expected_revision=1, actor="owner", reason="update")
    with pytest.raises(RevisionConflict):
        store.bind_run("run1", "P1", 2, request)
    assert store.runs("P1")[0]["revision"] == 1


def test_credentials_scoped_and_review_separated():
    secret = "reader-only-test-token-123456789012345"
    access = PatientAccess(
        "owner-" + "x" * 40,
        [
            {
                "token_sha256": hashlib.sha256(secret.encode()).hexdigest(),
                "actor": "R1",
                "role": "reviewer",
                "patients": ["P1"],
            }
        ],
    )
    reviewer = access.authenticate("Bearer " + secret)
    reviewer.require("read", "P1")
    reviewer.require("review", "P1")
    for action, patient in [("write", "P1"), ("execute", "P1"), ("read", "P2")]:
        with pytest.raises(PermissionError):
            reviewer.require(action, patient)
    assert access.authenticate("Bearer wrong") is None


def domain_payload(tmp_path):
    acquisitions, evidence = [], []
    for name, role, value in [
        ("fit", "calibration", 80.0),
        ("hold", "validation", 81.0),
    ]:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"value": value, "unit": "mmHg"}))
        acquisitions.append(
            {
                "acquisition_id": name,
                "subject_id": "P1",
                "modality": "pressure",
                "source_files": [
                    {
                        "file_id": name,
                        "uri": path.as_uri(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
        evidence.append(
            {
                "evidence_id": name,
                "subject_id": "P1",
                "role": role,
                "acquisition_id": name,
                "data_kind": "pressure",
                "withheld_from_fit": role == "validation",
            }
        )
    return {
        "case": {
            "patient_id": "P1",
            "acquisitions": acquisitions,
            "evidence": evidence,
        },
        "case_revision": 1,
        "case_fingerprint": "a" * 64,
        "domain": "circulation",
        "forward_template": {
            "subject_id": "P1",
            "backend": "windkessel-3element-v1",
            "boundary_conditions": [{"parameters": {"distal_resistance": 1.0}}],
        },
        "parameter_paths": {"r": "boundary_conditions.0.parameters.distal_resistance"},
        "parameter_bounds": {"r": [0.5, 2.0]},
        "parameter_units": {"r": "mmHg*s/mL"},
        "targets": [
            {
                "evidence_id": name,
                "source_file_id": name,
                "model_output": "scalar_outputs.mean_outlet_pressure",
                "unit": "mmHg",
                "noise_sd": 2.0,
                "max_abs_error": 5.0,
            }
            for name in ("fit", "hold")
        ],
    }


def test_domain_holdouts_absent_from_inference_and_prediction_precedes_read(tmp_path):
    payload = domain_payload(tmp_path)
    hold = tmp_path / "hold.json"
    hold_bytes = hold.read_bytes()
    calls = []

    class Adapter:
        def available(self):
            return True

        def invoke(self, capability, request):
            calls.append((capability, deepcopy(request)))
            if capability == "infer.run":
                encoded = json.dumps(request)
                assert str(hold) not in encoded and "hold.json" not in encoded
                assert len(request["likelihood"]) == 1
                return {
                    "subject_id": "P1",
                    "diagnostics": {"best_parameters": {"r": 1.2}},
                }
            # The holdout is deliberately unavailable until prediction is produced.
            hold.write_bytes(hold_bytes)
            return {
                "subject_id": "P1",
                "scalar_outputs": {"mean_outlet_pressure": 82.0},
                "units": {"mean_outlet_pressure": "mmHg"},
                "qc": {"passed": True},
            }

    hold.unlink()
    registry = SimpleNamespace(capability=lambda _: Adapter())
    output = execute_patient_domain(payload, registry, output_dir=tmp_path / "run")
    assert [x[0] for x in calls] == ["infer.run", "flow.simulate"]
    assert output["heldout_residuals"][0]["error"] == 1.0
    assert output["heldout_acceptance_passed"] is True
    assert (tmp_path / "run/prediction.json").exists()


@pytest.mark.parametrize(
    "mutation",
    ["same_hash", "same_acquisition", "embedded_holdout", "parameter_backend"],
)
def test_domain_rejects_leakage_and_unsafe_parameter_paths(tmp_path, mutation):
    payload = domain_payload(tmp_path)
    if mutation == "same_hash":
        payload["case"]["acquisitions"][1]["source_files"][0]["sha256"] = payload[
            "case"
        ]["acquisitions"][0]["source_files"][0]["sha256"]
    if mutation == "same_acquisition":
        payload["case"]["evidence"][1]["acquisition_id"] = "fit"
    if mutation == "embedded_holdout":
        payload["forward_template"]["observations"] = [{"observed": 81}]
    if mutation == "parameter_backend":
        payload["parameter_paths"]["r"] = "backend"
    with pytest.raises(ValueError):
        validate_domain_job(PatientDomainJob.model_validate(payload))


def test_domain_hash_change_and_failed_qc_rejected(tmp_path):
    payload = domain_payload(tmp_path)
    (tmp_path / "fit.json").write_text('{"value":900,"unit":"mmHg"}')
    with pytest.raises(ValueError, match="SHA-256"):
        execute_patient_domain(payload, SimpleNamespace(), output_dir=tmp_path / "run")


def test_worker_dispatch_uses_patient_scientific_boundary(monkeypatch):
    import hearttwin.patient_execution as module

    calls = []
    monkeypatch.setattr(
        module,
        "execute_patient_ep",
        lambda payload, registry: (
            calls.append(payload) or {"status": "boundary_called"}
        ),
    )
    assert execute(
        {"kind": "patient_ep", "payload": {"fixture": True}}, registry=object()
    ) == {"status": "boundary_called"}
    assert calls == [{"fixture": True}]


def test_patient_server_populates_revision_cannot_be_spoofed(tmp_path):
    app = Workbench(tmp_path, "owner-" + "x" * 40)
    snapshot = app.patients.save(
        {"patient_id": "P1"}, expected_revision=0, actor="owner", reason="create"
    )
    request = {
        "kind": "patient_ep",
        "payload": {"patient_id": "P1", "case_revision": 1},
    }
    expanded = app.patient_request(request)
    assert expanded["payload"]["case_fingerprint"] == snapshot["fingerprint"]
    assert expanded["payload"]["case"]["patient_id"] == "P1"
    request["payload"]["case"] = {"patient_id": "P2"}
    with pytest.raises(ValueError):
        app.patient_request(request)


def test_http_scoped_review_cannot_read_other_case_or_raw_jobs(tmp_path):
    import http.client
    import threading
    from hearttwin.web import make_server

    token = "reviewer-" + "r" * 40
    app = Workbench(
        tmp_path,
        "owner-" + "x" * 40,
        patient_credentials=[
            {
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "actor": "R1",
                "role": "reviewer",
                "patients": ["P1"],
            }
        ],
    )
    for patient in ("P1", "P2"):
        app.patients.save(
            {"patient_id": patient}, expected_revision=0, actor="owner", reason="create"
        )
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def send(method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        raw = None if body is None else json.dumps(body)
        connection.request(
            method,
            path,
            body=raw,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        status = response.status
        data = json.loads(response.read())
        connection.close()
        return status, data

    try:
        assert send("GET", "/api/patients")[1]["patients"][0]["patient_id"] == "P1"
        assert len(send("GET", "/api/patients")[1]["patients"]) == 1
        assert send("GET", "/api/patients/P2")[0] == 403
        assert send("GET", "/api/jobs")[0] == 403
        assert (
            send(
                "POST",
                "/api/patients/P1",
                {"case": {"patient_id": "P1"}, "expected_revision": 1, "reason": "bad"},
            )[0]
            == 403
        )
        assert (
            send(
                "POST",
                "/api/patients/P1/review",
                {
                    "revision": 1,
                    "detail": {
                        "decision": "approved",
                        "rationale": "independent review",
                    },
                },
            )[0]
            == 201
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


def test_duplicate_ecg_content_cannot_hide_behind_new_acquisition_ids(tmp_path):
    from hearttwin.patient_integrity import verify_patient_sources

    payload = domain_payload(tmp_path)
    first = payload["case"]["acquisitions"][0]["source_files"][0]
    payload["case"]["acquisitions"][1]["source_files"][0] = dict(
        first, file_id="renamed"
    )
    case = PatientCase.model_validate(payload["case"])
    with pytest.raises(ValueError, match="Identical source"):
        verify_patient_sources(case, ["fit"], ["hold"])


def test_real_circulation_inference_loop(tmp_path):
    """Optional synthetic regression with real services and command forward evaluations."""
    pytest.importorskip("cardiinfer")
    pytest.importorskip("cardiflow")
    from hearttwin.config import load_registry
    from cardiflow.api import FlowAPI

    payload = domain_payload(tmp_path)
    template = {
        "subject_id": "P1",
        "domain": {
            "domain_id": "0d",
            "anatomy_ref": {
                "artifact_id": "P1-domain",
                "kind": "reduced_order_domain",
                "uri": "memory://0d",
            },
            "region": "afterload",
        },
        "backend": "windkessel-3element-v1",
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
    truth = FlowAPI().simulate(template)["scalar_outputs"]["mean_outlet_pressure"]
    # Independent acquisition IDs, with small declared synthetic measurement difference.
    for name, value in [("fit", truth), ("hold", truth + 0.01)]:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"value": value, "unit": "mmHg"}))
        source = next(
            a for a in payload["case"]["acquisitions"] if a["acquisition_id"] == name
        )["source_files"][0]
        source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload["forward_template"] = template
    payload["sampler_settings"] = {"population_size": 6, "generations": 2}
    for target in payload["targets"]:
        target["max_abs_error"] = 10
    output = execute_patient_domain(
        payload, load_registry(), output_dir=tmp_path / "real-domain"
    )
    assert output["heldout_acceptance_passed"] is True
    assert output["inference"]["backend"] == "native-map-de-v1"
    assert output["clinical_qualification"] == "not_established"


def test_runtime_manifest_records_installed_electrotrace():
    pytest.importorskip("electrotrace")
    from importlib.metadata import version
    from hearttwin.patient_execution import runtime_manifest

    manifest = runtime_manifest()["packages"]["electrotrace"]
    assert manifest["installed"] is True
    assert manifest["version"] == version("electrotrace")
    assert "__init__.py" in manifest["source_files"]
    assert len(manifest["source_fingerprint"]) == 64
