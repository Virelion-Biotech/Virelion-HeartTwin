"""HTTP contracts and real subprocess execution for the owner workbench."""

import hashlib
import http.client
import io
import json
import threading
import time
from contextlib import contextmanager
from importlib.resources import files

import pytest

from hearttwin.config import load_registry
from hearttwin.web import Workbench, make_server, strict_json
from hearttwin.web_job import resolve_steps

TOKEN = "test-only-workbench-key-" + "x" * 32
NATIVE = {
    "kind": "capability",
    "capability": "simulation.cardiac_twin",
    "label": "software fixture",
    "payload": {
        "geometry": {
            "node_xyz": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "tetrahedra": [[0, 1, 2, 3]],
        },
        "root_nodes": [0],
        "with_ecg": False,
    },
}


@contextmanager
def gateway(tmp_path, worker=False):
    app = Workbench(tmp_path, TOKEN, origins=["https://workbench.example"], timeout=15)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if worker:
        app.start()
    try:
        yield app, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join()


def request(port, method, path, data=None, auth=True, headers=None):
    headers = dict(headers or {})
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = "Bearer " + TOKEN
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def completed(app, identifier):
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        job = app.job(identifier)
        if job["status"] in {"ok", "error", "cancelled"}:
            return job
        time.sleep(0.1)
    pytest.fail("Worker did not finish within the test deadline")


def test_http_auth_origin_host_and_static(tmp_path):
    with gateway(tmp_path) as (_, port):
        assert request(port, "GET", "/api/catalog", auth=False)[0] == 401
        assert (
            request(
                port, "GET", "/api/catalog", headers={"Origin": "https://evil.example"}
            )[0]
            == 403
        )
        assert (
            request(port, "GET", "/api/catalog", headers={"Host": "evil.example"})[0]
            == 403
        )
        status, headers, body = request(
            port, "GET", "/api/catalog", headers={"Origin": "https://workbench.example"}
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "https://workbench.example"
        catalog = json.loads(body)
        assert len(catalog["services"]) == 22
        assert not next(x for x in catalog["services"] if x["name"] == "CardiLearn")[
            "runnable"
        ]
        assert request(port, "GET", "/", auth=False)[0] == 200
        assert request(port, "GET", "/viewer.js", auth=False)[0] == 200
        assert request(port, "GET", "/tasks.js", auth=False)[0] == 200
        assert request(port, "GET", "/heart-reference.json", auth=False)[0] == 200
        logo_status, logo_headers, logo_body = request(
            port, "GET", "/virelion-logo.png", auth=False
        )
        assert logo_status == 200
        assert logo_headers["Content-Type"] == "image/png"
        assert logo_body.startswith(b"\x89PNG\r\n\x1a\n")
        assert request(port, "GET", "/../web.py", auth=False)[0] == 404


def test_upload_integrity_and_short_stream(tmp_path):
    with gateway(tmp_path) as (app, port):
        raw = b"time,signal\n0,1\n1,2\n"
        status, _, body = request(
            port, "POST", "/api/files", raw, headers={"X-Filename": "../../input.csv"}
        )
        receipt = json.loads(body)
        assert status == 201
        assert receipt["name"] == "input.csv"
        assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
        path = app.resolve_uploads({"$upload": receipt["id"]})
        from pathlib import Path

        Path(path).write_bytes(b"changed")
        with pytest.raises(ValueError, match="integrity"):
            app.resolve_uploads({"$upload": receipt["id"]})
        with pytest.raises(ValueError, match="declared"):
            app.upload(io.BytesIO(b"short"), "partial.bin", 100)
        assert not list((tmp_path / "uploads").glob("*.part"))
        assert len(app.uploads()) == 1


def test_admission_idempotency_and_queued_cancel(tmp_path):
    with gateway(tmp_path) as (app, port):
        learned = next(
            s for s in app.catalog()["services"] if s["name"] == "CardiLearn"
        )
        blocked = {**NATIVE, "capability": learned["capabilities"][0]}
        assert request(port, "POST", "/api/jobs", blocked)[0] == 400
        assert (
            request(port, "POST", "/api/jobs", {**NATIVE, "capability": "unknown"})[0]
            == 400
        )
        headers = {"Idempotency-Key": "same-submission"}
        first = request(port, "POST", "/api/jobs", NATIVE, headers=headers)
        assert first[0] == 202
        job = json.loads(first[2])
        second = request(port, "POST", "/api/jobs", NATIVE, headers=headers)
        assert json.loads(second[2])["job_id"] == job["job_id"]
        assert (
            request(
                port,
                "POST",
                "/api/jobs",
                {**NATIVE, "label": "different"},
                headers=headers,
            )[0]
            == 400
        )
        route = "/api/jobs/" + job["job_id"] + "/cancel"
        assert json.loads(request(port, "POST", route)[2])["status"] == "cancelled"
        assert request(port, "POST", route)[0] == 400


def test_real_subprocess_result_download_persistence_and_tamper(tmp_path):
    with gateway(tmp_path, worker=True) as (app, port):
        identifier = app.submit(NATIVE)["job_id"]
        job = completed(app, identifier)
        assert job["status"] == "ok", job
        assert len(job["result"]["activation_ms"]) == 4
        route = "/api/jobs/" + identifier + "/artifacts/"
        assert request(port, "GET", route + "result.json")[0] == 200
        assert request(port, "GET", route + "%2E%2E%2Frequest.json")[0] == 404
        assert request(port, "GET", route + "request.json")[0] == 404
    restored = Workbench(tmp_path, TOKEN)
    assert restored.job(identifier)["status"] == "ok"
    (tmp_path / "runs" / identifier / "result.json").write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        restored.artifact(identifier, "result.json")
    with pytest.raises(ValueError, match="integrity"):
        restored.job(identifier)


def test_failed_scientific_request_is_not_success(tmp_path):
    with gateway(tmp_path, worker=True) as (app, _):
        job = completed(app, app.submit({**NATIVE, "payload": {}})["job_id"])
        assert job["status"] == "error"
        assert "geometry" in job["log"]
        assert "result" not in job


def test_real_explicit_sequence_handoff(tmp_path):
    with gateway(tmp_path, worker=True) as (app, _):
        second = {**NATIVE["payload"], "root_nodes": {"$step": "first.root_nodes"}}
        steps = [
            {
                "id": "first",
                "capability": NATIVE["capability"],
                "payload": NATIVE["payload"],
            },
            {"id": "second", "capability": NATIVE["capability"], "payload": second},
        ]
        job = completed(
            app, app.submit({"kind": "sequence", "payload": {"steps": steps}})["job_id"]
        )
        assert job["status"] == "ok", job
        assert job["result"]["steps"]["second"]["root_nodes"] == [0]
        assert any(x["path"] == "steps/first.json" for x in job["artifacts"])
        with pytest.raises(ValueError, match="unique"):
            app.validate(
                {"kind": "sequence", "payload": {"steps": [steps[0], steps[0]]}}
            )
        assert resolve_steps({"v": {"$step": "first.a.0"}}, {"first": {"a": [9]}}) == {
            "v": 9
        }


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"value":NaN}'])
def test_json_rejects_ambiguous_values(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_offline_catalog_matches_registered_capabilities():
    static = json.loads(
        files("hearttwin").joinpath("webui", "catalog.json").read_text()
    )
    registry = {
        spec.name: list(spec.capabilities) for spec in load_registry().services()
    }
    assert {
        item["name"]: item["capabilities"] for item in static["services"]
    } == registry


def test_uploaded_ecg_runs_through_real_command_adapter(tmp_path):
    import math
    import shutil

    if not shutil.which("electrotrace-hearttwin"):
        pytest.skip("ElectroTrace command adapter is not installed")
    raw = (
        "time,lead_I\n"
        + "".join(
            f"{i / 100},{math.sin(2 * math.pi * 1.2 * i / 100)}\n" for i in range(200)
        )
    ).encode()
    with gateway(tmp_path, worker=True) as (app, port):
        status, _, body = request(
            port,
            "POST",
            "/api/files",
            raw,
            headers={"X-Filename": "software-fixture.csv"},
        )
        assert status == 201
        receipt = json.loads(body)
        payload = {
            "entity_id": "software-fixture-only",
            "observations": [
                {
                    "modality": "electrical",
                    "values": {
                        "input_path": {"$upload": receipt["id"]},
                        "time_col": "time",
                    },
                }
            ],
        }
        status, _, body = request(
            port,
            "POST",
            "/api/jobs",
            {
                "kind": "capability",
                "capability": "electrical.analyze",
                "payload": payload,
            },
        )
        assert status == 202
        job = completed(app, json.loads(body)["job_id"])
        assert job["status"] == "ok", job
        assert job["result"]["entity_id"] == "software-fixture-only"
