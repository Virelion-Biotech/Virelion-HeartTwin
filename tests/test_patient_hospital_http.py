"""Only the worker owner can cause authenticated hospital network retrieval."""

import hashlib
import http.client
import json
import threading

from hearttwin.patient_model import PatientCase
from hearttwin.web import Workbench, make_server


def test_operator_cannot_invoke_hospital_transport_owner_can(tmp_path, monkeypatch):
    owner = "owner-" + "o" * 40
    operator = "operator-" + "r" * 40
    app = Workbench(tmp_path, owner, patient_credentials=[{"token_sha256": hashlib.sha256(operator.encode()).hexdigest(),
                    "actor": "operator", "role": "operator", "patients": ["P1"]}])
    app.patients.save({"patient_id": "P1"}, expected_revision=0, actor="owner", reason="create")
    calls = []

    def importer(case, config, *, output_dir):
        calls.append(config)
        return PatientCase.model_validate(case), {"instance_count": 2, "geometry_checked": True}

    monkeypatch.setattr("hearttwin.patient_dicomweb.import_hospital_images", importer)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(token):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("POST", "/api/patients/P1/hospital-imaging-import", body=json.dumps({"expected_revision": 1, "config": {"synthetic": True}}),
                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = conn.getresponse()
        status, data = response.status, json.loads(response.read())
        conn.close()
        return status, data

    try:
        assert post(operator)[0] == 403
        assert calls == []
        status, data = post(owner)
        assert status == 201 and data["snapshot"]["revision"] == 2
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()
