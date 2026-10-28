"""Single-owner HTTP workbench with authenticated, isolated execution jobs.

Deploy behind HTTPS when remote. This gateway is not a multi-tenant sandbox:
its authenticated owner is trusted to invoke the installed scientific services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .config import load_registry
from .operations import JobQueue
from .patient_access import PatientAccess
from .patient_store import PatientCaseStore, RevisionConflict

ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
JSON_LIMIT = 2 * 1024 * 1024


def strict_json(raw: bytes) -> Any:
    def reject(value):
        raise ValueError("Non-finite JSON values are not accepted: " + value)

    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError("Duplicate JSON key: " + key)
            output[key] = value
        return output

    return json.loads(raw, parse_constant=reject, object_pairs_hook=pairs)


class Workbench:
    def __init__(
        self,
        root: Path,
        token: str,
        *,
        registry=None,
        origins=(),
        timeout=3600,
        max_upload=256 * 1024 * 1024,
        patient_credentials=(),
    ):
        if len(token) < 32:
            raise ValueError("Workbench token must contain at least 32 characters")
        if not 1 <= timeout <= 86400 or max_upload < 1:
            raise ValueError("Invalid execution or upload limits")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token, self.origins = token, set(origins)
        self.access = PatientAccess(token, patient_credentials)
        self.patients = PatientCaseStore(self.root / "patients.sqlite3")
        self.timeout, self.max_upload = timeout, max_upload
        self.registry = registry or load_registry()
        self.queue = JobQueue(self.root / "jobs.sqlite3")
        self.stop = threading.Event()
        self.process = None
        self.thread = None
        (self.root / "uploads").mkdir(exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)
        with self.queue._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, name TEXT, size INTEGER, sha256 TEXT, path TEXT)"
            )

    def catalog(self):
        output = []
        for spec in self.registry.services():
            policy = spec.name == "CardiLearn"
            available = self.registry.adapters[spec.name].available()
            output.append(
                {
                    "name": spec.name,
                    "repository": spec.repository,
                    "capabilities": list(spec.capabilities),
                    "installed": available,
                    "runnable": available and not policy,
                    "gate": "Raw-count benchmark/training gates incomplete"
                    if policy
                    else None,
                }
            )
        return {
            "services": output,
            "execution": "trusted-owner-subprocess",
            "worker": "online",
            "upload_limit_bytes": self.max_upload,
        }

    def upload(self, stream, name: str, size: int):
        if not 0 < size <= self.max_upload:
            raise ValueError("Upload size is outside the configured limit")
        name = (
            re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)[:160].lstrip(".")
            or "input.bin"
        )
        identifier = "file-" + secrets.token_hex(12)
        path = self.root / "uploads" / (identifier + "-" + name)
        temporary = path.with_name(path.name + ".part")
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as handle:
                remaining = size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Upload ended before the declared byte size")
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            temporary.replace(path)
            with self.queue._connect() as db:
                db.execute(
                    "INSERT INTO uploads VALUES(?,?,?,?,?)",
                    (identifier, name, size, digest.hexdigest(), str(path)),
                )
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return {
            "id": identifier,
            "name": name,
            "size": size,
            "sha256": digest.hexdigest(),
        }

    def uploads(self):
        with self.queue._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,name,size,sha256 FROM uploads ORDER BY rowid DESC"
                )
            ]

    def resolve_uploads(self, value):
        if isinstance(value, dict):
            if set(value) == {"$upload"}:
                with self.queue._connect() as db:
                    row = db.execute(
                        "SELECT path,sha256 FROM uploads WHERE id=?",
                        (str(value["$upload"]),),
                    ).fetchone()
                if row is None:
                    raise ValueError("Unknown upload identifier")
                path = Path(row["path"]).resolve()
                if not path.is_relative_to(self.root / "uploads") or not path.is_file():
                    raise ValueError("Upload is unavailable")
                digest = self.file_hash(path)
                if digest != row["sha256"]:
                    raise ValueError("Upload integrity verification failed")
                return str(path)
            return {key: self.resolve_uploads(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_uploads(item) for item in value]
        return value

    @staticmethod
    def file_hash(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def validate(self, request):
        if not isinstance(request, dict) or set(request) - {
            "kind",
            "payload",
            "capability",
            "label",
        }:
            raise ValueError("Invalid job request fields")
        if not isinstance(request.get("payload"), dict):
            raise TypeError("Payload must be a JSON object")
        kind = request.get("kind")
        if kind == "capability":
            adapter = self.registry.capability(request.get("capability", ""))
            if adapter is None:
                raise ValueError("Unknown capability")
            if adapter.spec.name == "CardiLearn":
                raise ValueError(
                    "CardiLearn is blocked pending its data and model gates"
                )
            if not adapter.available():
                raise ValueError("Selected module is unavailable on this worker")
        elif kind == "sequence":
            steps = request["payload"].get("steps")
            if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
                raise ValueError("Sequence needs 1–32 explicit steps")
            seen = set()
            for step in steps:
                if not isinstance(step, dict) or set(step) != {
                    "id",
                    "capability",
                    "payload",
                }:
                    raise ValueError("Each step requires id, capability and payload")
                if (
                    not isinstance(step["id"], str)
                    or not ID.fullmatch(step["id"])
                    or step["id"] in seen
                ):
                    raise ValueError("Step IDs must be unique simple names")
                seen.add(step["id"])
                self.validate(
                    {
                        "kind": "capability",
                        "capability": step["capability"],
                        "payload": step["payload"],
                    }
                )
        elif kind == "patient_ep":
            from .patient_execution import PatientEPJob
            from .patient_model import validate_patient_ep_workflow

            job = PatientEPJob.model_validate(request["payload"])
            snapshot = self.patients.get(job.case.patient_id, job.case_revision)
            if snapshot["fingerprint"] != job.case_fingerprint or snapshot[
                "case"
            ] != job.case.model_dump(mode="json"):
                raise ValueError(
                    "Patient job does not match its immutable case revision"
                )
            if any(
                x["patient_id"] == job.case.patient_id and x["archived"]
                for x in self.patients.list_cases()
            ):
                raise ValueError("Restore the patient case before submitting a run")
            validate_patient_ep_workflow(job.case, job.spec)
            if job.evaluation_spec.acceptance is None or not job.spec.scientific_grade:
                raise ValueError(
                    "Patient jobs require scientific execution and predeclared acceptance criteria"
                )
        elif kind in {"patient_domain", "patient_plan"}:
            from .patient_domain import PatientDomainJob, validate_domain_job

            if kind == "patient_plan":
                from .patient_personalization import PatientPlanJob

                job = PatientPlanJob.model_validate(request["payload"])
            else:
                job = PatientDomainJob.model_validate(request["payload"])
            snapshot = self.patients.get(job.case.patient_id, job.case_revision)
            if snapshot["fingerprint"] != job.case_fingerprint or snapshot[
                "case"
            ] != job.case.model_dump(mode="json"):
                raise ValueError(
                    "Patient domain job differs from immutable case revision"
                )
            if any(
                x["patient_id"] == job.case.patient_id and x["archived"]
                for x in self.patients.list_cases()
            ):
                raise ValueError("Restore the patient case before submitting a run")
            if kind == "patient_plan":
                from .patient_personalization import validate_patient_plan

                validate_patient_plan(job)
            else:
                validate_domain_job(job)
        elif kind == "mechanistic":
            required = [
                "CardiAnatomy",
                "CardiEP",
                "CardiInfer",
                "CardiMech",
                "CardiFlow",
                "CardiTherapy",
                "CardiEval",
                "CardiTrace",
            ]
            if any(not self.registry.adapters[n].available() for n in required):
                raise ValueError(
                    "The mechanistic workflow requires all eight installed services"
                )
        else:
            raise ValueError(
                "Supported job kinds: capability, sequence, mechanistic, patient_ep, patient_domain"
            )
        label = request.get("label", request.get("capability", kind))
        if not isinstance(label, str) or not label.strip() or len(label) > 160:
            raise ValueError("Job label must contain 1–160 characters")
        return {**request, "label": label}

    def submit(self, request, key=None):
        request = self.validate(request)
        # Resolve before admission to reject missing/corrupt uploads. Recheck at execution.
        self.resolve_uploads(request["payload"])
        if key is not None and not ID.fullmatch(key):
            raise ValueError("Invalid idempotency key")
        try:
            identifier = self.queue.enqueue(
                request["label"],
                "web",
                request,
                idempotency_key=key or secrets.token_hex(16),
            )
        except RuntimeError as exc:
            raise ValueError(
                "Idempotency key already belongs to a different request"
            ) from exc
        if request["kind"] in {"patient_ep", "patient_domain", "patient_plan"}:
            payload = request["payload"]
            try:
                self.patients.bind_run(
                    identifier,
                    payload["case"]["patient_id"],
                    payload["case_revision"],
                    request,
                )
            except Exception:
                self.cancel(identifier)
                raise
        return self.job(identifier)

    def patient_request(self, request):
        """Expand a revision reference server-side; clients cannot substitute case data."""
        request = dict(request)
        payload = dict(request.get("payload") or {})
        patient_id = payload.pop("patient_id")
        revision = payload.get("case_revision")
        if type(revision) is not int or revision < 1:
            raise ValueError("Select an explicit positive case_revision")
        snapshot = self.patients.get(patient_id, revision)
        if "case" in payload or "case_fingerprint" in payload:
            raise ValueError("Patient case and fingerprint are populated by the server")
        payload.update(case=snapshot["case"], case_fingerprint=snapshot["fingerprint"])
        request["payload"] = payload
        return request

    def job(self, identifier, *, detail=True):
        if not ID.fullmatch(identifier):
            raise KeyError(identifier)
        row = self.queue.get(identifier)
        output = {
            key: row[key]
            for key in [
                "job_id",
                "case_id",
                "status",
                "attempts",
                "created_at",
                "updated_at",
                "error",
            ]
        }
        output.update(
            {
                "kind": row["payload"]["kind"],
                "capability": row["payload"].get("capability"),
            }
        )
        if detail:
            output["request"] = row["payload"]
            directory = self.root / "runs" / identifier
            artifacts = []
            if directory.is_dir():
                for path in sorted(directory.rglob("*")):
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and path.name != "request.json"
                    ):
                        artifacts.append(
                            {
                                "path": str(path.relative_to(directory)),
                                "size": path.stat().st_size,
                            }
                        )
                result = directory / "result.json"
                if (
                    row["status"] == "ok"
                    and result.is_file()
                    and result.stat().st_size <= JSON_LIMIT
                ):
                    if self.file_hash(result) != row.get("result", {}).get(
                        "result_sha256"
                    ):
                        raise ValueError(
                            "Completed result integrity verification failed"
                        )
                    output["result"] = strict_json(result.read_bytes())
                log = directory / "execution.log"
                if log.exists():
                    with log.open("rb") as handle:
                        handle.seek(max(0, log.stat().st_size - 24000))
                        output["log"] = handle.read().decode(errors="replace")
            output["artifacts"] = artifacts
        return output

    def artifact(self, identifier, name):
        row = self.queue.get(identifier)
        base = self.root / "runs" / identifier
        artifact = (base / name).resolve()
        if (
            not artifact.is_relative_to(base)
            or not artifact.is_file()
            or artifact.name == "request.json"
        ):
            raise KeyError("Artifact is unavailable")
        if artifact.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Artifact exceeds 64 MiB; retrieve it on the worker")
        if row["status"] == "ok":
            record = (
                row.get("result", {})
                .get("artifacts", {})
                .get(str(artifact.relative_to(base)))
            )
            if (
                not record
                or record["size"] != artifact.stat().st_size
                or record["sha256"] != self.file_hash(artifact)
            ):
                raise ValueError("Artifact integrity verification failed")
        return artifact

    def patient_geometry(self, job_id):
        job = self.job(job_id)
        if job["status"] != "ok":
            raise ValueError("Select a completed patient EP run")
        artifacts = (
            job.get("result", {})
            .get("workflow", {})
            .get("anatomy_bundle", {})
            .get("artifacts", [])
        )
        candidates = [a for a in artifacts if a.get("kind") == "volume_mesh"]
        selected = [
            a
            for a in candidates
            if a.get("metadata", {}).get("selected_for_patient_model") is True
        ]
        candidates = selected or candidates
        if len(candidates) != 1:
            raise ValueError("Patient run has no unambiguous volume mesh")
        artifact = candidates[0]
        from .patient_ecg_artifacts import _local_path

        path = _local_path(artifact["uri"])
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError(
                "Patient mesh exceeds the browser limit; use the worker for visualization"
            )
        if self.file_hash(path) != artifact.get("sha256"):
            raise ValueError("Patient display mesh failed SHA-256 verification")
        raw = strict_json(path.read_bytes())
        geometry = raw.get("fields", raw)
        nodes = geometry.get("node_xyz")
        tetra = geometry.get("tetrahedra", geometry.get("tetra"))
        index_base = raw.get(
            "index_base", artifact.get("metadata", {}).get("index_base")
        )
        if index_base not in {0, 1}:
            raise ValueError("Display mesh must declare index_base explicitly")
        if not isinstance(nodes, list) or not isinstance(tetra, list):
            raise TypeError(
                "Browser display requires JSON node_xyz and tetrahedra arrays"
            )
        if len(nodes) > 30000:
            raise ValueError("Patient mesh exceeds the 30,000-node browser limit")
        unit = raw.get("units", artifact.get("metadata", {}).get("units"))
        if isinstance(unit, dict):
            unit = unit.get("node_xyz")
        if unit not in {"mm", "cm", "m"}:
            raise ValueError("Display mesh requires explicit physical coordinate units")
        return {
            "geometry": {
                "node_xyz": nodes,
                "tetrahedra": [[i - index_base for i in cell] for cell in tetra],
                "units": {"node_xyz": unit},
            },
            "source_sha256": artifact["sha256"],
            "scope": "Patient anatomy only; no activation field is inferred for display",
        }

    def cancel(self, identifier):
        with self.queue._connect() as db:
            changed = db.execute(
                "UPDATE jobs SET status='cancelled' WHERE job_id=? AND status='queued'",
                (identifier,),
            ).rowcount
        if not changed:
            raise ValueError("Only queued jobs can be cancelled")
        return self.job(identifier)

    def start(self):
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _work(self):
        while not self.stop.is_set():
            # Never silently replay scientific work after an interrupted lease.
            with self.queue._connect() as db:
                db.execute(
                    "UPDATE jobs SET status='error',error='Worker interrupted; resubmit explicitly',lease_owner=NULL,lease_expires_at=NULL WHERE status='leased' AND lease_expires_at <= ?",
                    (time.time(),),
                )
            row = self.queue.claim("web-worker", lease_seconds=30)
            if row is None:
                self.stop.wait(0.25)
                continue
            identifier = row["job_id"]
            directory = self.root / "runs" / identifier
            directory.mkdir(exist_ok=True)
            try:
                request = {
                    **row["payload"],
                    "payload": self.resolve_uploads(row["payload"]["payload"]),
                }
                self.validate(request)
                (directory / "request.json").write_text(
                    json.dumps(request, allow_nan=False), encoding="utf-8"
                )
                environment = dict(
                    os.environ,
                    MPLBACKEND="Agg",
                    PYTHONUNBUFFERED="1",
                    HEARTTWIN_PAYLOAD_STDIN="1",
                )
                # API credentials must not be inherited by numerical subprocesses.
                environment.pop("HEARTTWIN_WEB_TOKEN", None)
                environment.pop("HEARTTWIN_PATIENT_CREDENTIALS", None)
                with (directory / "execution.log").open("wb") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "hearttwin.web_job"],
                        cwd=directory,
                        stdin=subprocess.PIPE,
                        stdout=log,
                        stderr=log,
                        env=environment,
                        start_new_session=True,
                    )
                    self.process = process
                    process.stdin.write(json.dumps(request, allow_nan=False).encode())
                    process.stdin.close()
                    started = time.monotonic()
                    while process.poll() is None:
                        if (
                            self.stop.wait(0.5)
                            or time.monotonic() - started > self.timeout
                        ):
                            if os.name == "posix":
                                os.killpg(process.pid, signal.SIGKILL)
                            else:
                                process.kill()
                            process.wait()
                            raise RuntimeError(
                                "Execution interrupted or exceeded its time limit"
                            )
                        self.queue.heartbeat(identifier, "web-worker", lease_seconds=30)
                    if process.returncode:
                        raise RuntimeError(
                            "Module execution failed; inspect the execution log"
                        )
                strict_json((directory / "result.json").read_bytes())
                artifacts = {
                    str(p.relative_to(directory)): {
                        "size": p.stat().st_size,
                        "sha256": self.file_hash(p),
                    }
                    for p in directory.rglob("*")
                    if p.is_file()
                    and not p.is_symlink()
                    and p.resolve().is_relative_to(directory)
                    and p.name != "request.json"
                }
                self.queue.complete(
                    identifier,
                    "web-worker",
                    {
                        "result_sha256": self.file_hash(directory / "result.json"),
                        "execution_completed": True,
                        "artifacts": artifacts,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - record all module failures at the job boundary
                self.queue.fail(identifier, "web-worker", str(exc))
            finally:
                self.process = None


def make_server(app: Workbench, host="127.0.0.1", port=8787):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HeartTwinWorkbench/1"

        def log_message(self, format, *args):
            pass

        def origin_allowed(self):
            origin = self.headers.get("Origin")
            return (
                not origin
                or origin in app.origins
                or origin == "http://" + self.headers.get("Host", "")
            )

        def send_bytes(self, status, body, mime="application/json", disposition=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self' https: http://127.0.0.1:* http://localhost:*; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
            if self.headers.get("Origin") and self.origin_allowed():
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Authorization,Content-Type,X-Filename,Idempotency-Key",
                )
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)

        def json(self, status, value):
            self.send_bytes(status, json.dumps(value, allow_nan=False).encode())

        def do_OPTIONS(self):
            if not self.origin_allowed():
                return self.json(403, {"error": "Origin is not permitted"})
            self.send_bytes(204, b"")

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def read_json(self):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("A fixed Content-Length is required")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= JSON_LIMIT:
                raise ValueError("Patient JSON exceeds its limit or is empty")
            value = strict_json(self.rfile.read(size))
            if not isinstance(value, dict):
                raise TypeError("Patient request must be a JSON object")
            return value

        def patient_dispatch(self, path, principal):
            if path == "/api/patients" and self.command == "GET":
                return self.json(
                    200,
                    {
                        "patients": [
                            x
                            for x in app.patients.list_cases()
                            if principal.permits(x["patient_id"])
                        ]
                    },
                )
            match = re.fullmatch(r"/api/patients/([A-Za-z0-9_-]+)(?:/(.*))?", path)
            if not match:
                return self.json(404, {"error": "Not found"})
            patient_id, suffix = match.groups()
            principal.require("read", patient_id)
            if self.command == "GET":
                if not suffix:
                    return self.json(200, app.patients.get(patient_id))
                if suffix == "history":
                    return self.json(
                        200,
                        {
                            "revisions": app.patients.history(patient_id),
                            "events": app.patients.events(patient_id),
                        },
                    )
                if suffix == "readiness":
                    return self.json(200, app.patients.readiness(patient_id))
                revision = re.fullmatch(r"revisions/([1-9][0-9]*)", suffix)
                if revision:
                    return self.json(
                        200, app.patients.get(patient_id, int(revision[1]))
                    )
                if suffix == "runs":
                    return self.json(
                        200,
                        {
                            "runs": [
                                {k: v for k, v in item.items() if k != "request"}
                                for item in app.patients.runs(patient_id)
                            ]
                        },
                    )
                run = re.fullmatch(
                    r"runs/([A-Za-z0-9_-]+)(?:/(artifacts/.*|geometry))?", suffix
                )
                if run:
                    if run[1] not in {
                        x["run_id"] for x in app.patients.runs(patient_id)
                    }:
                        raise KeyError(run[1])
                    job = app.job(run[1])
                    # Scoped readers see results, not local source paths and execution logs.
                    if principal.role != "owner":
                        from .patient_trace import _redact_artifact_uris

                        job.pop("request", None)
                        job.pop("log", None)
                        job = _redact_artifact_uris(job)
                    if run[2]:
                        if principal.role != "owner":
                            raise PermissionError(
                                "Raw artifacts require the trusted worker owner"
                            )
                        if run[2] == "geometry":
                            return self.json(200, app.patient_geometry(run[1]))
                        artifact = app.artifact(
                            run[1], unquote(run[2].removeprefix("artifacts/"))
                        )
                        return self.send_bytes(
                            200, artifact.read_bytes(), "application/octet-stream"
                        )
                    return self.json(200, job)
            if self.command == "POST":
                value = self.read_json()
                if not suffix:
                    principal.require("write", patient_id)
                    if value.get("case", {}).get("patient_id") != patient_id:
                        raise ValueError("Route patient differs from case patient")
                    return self.json(
                        201,
                        app.patients.save(
                            value["case"],
                            expected_revision=value["expected_revision"],
                            actor=principal.actor,
                            reason=value["reason"],
                        ),
                    )
                if suffix in {"review", "archive", "restore"}:
                    principal.require(
                        "review" if suffix == "review" else "archive", patient_id
                    )
                    return self.json(
                        201,
                        app.patients.event(
                            patient_id,
                            value["revision"],
                            kind=suffix,
                            actor=principal.actor,
                            detail=value["detail"],
                        ),
                    )
                if suffix == "attach":
                    principal.require("write", patient_id)
                    # Upload access is owner-only; no cross-case file discovery for scoped users.
                    if principal.role != "owner":
                        raise PermissionError(
                            "Acquisition uploads require the worker owner"
                        )
                    from .patient_model import (
                        CoordinateFrame,
                        PatientAcquisition,
                        PatientCase,
                        PatientEvidenceItem,
                    )

                    snapshot = app.patients.get(patient_id)
                    if value["expected_revision"] != snapshot["revision"]:
                        raise RevisionConflict(
                            "Patient changed; reload before attaching an acquisition"
                        )
                    case = snapshot["case"]
                    receipt_id = value["upload_id"]
                    source_path = app.resolve_uploads({"$upload": receipt_id})
                    receipt = next(x for x in app.uploads() if x["id"] == receipt_id)
                    acquisition = dict(value["acquisition"])
                    acquisition.update(
                        subject_id=patient_id,
                        source_files=[
                            {
                                "file_id": receipt_id,
                                "uri": Path(source_path).as_uri(),
                                "sha256": receipt["sha256"],
                            }
                        ],
                    )
                    case["acquisitions"].append(
                        PatientAcquisition.model_validate(acquisition).model_dump(
                            mode="json"
                        )
                    )
                    if value.get("frame"):
                        frame = CoordinateFrame.model_validate(
                            value["frame"]
                        ).model_dump(mode="json")
                        existing = [
                            x
                            for x in case["coordinate_frames"]
                            if x["frame_id"] == frame["frame_id"]
                        ]
                        if existing and existing[0] != frame:
                            raise ValueError(
                                "Frame already exists with a different definition"
                            )
                        if not existing:
                            case["coordinate_frames"].append(frame)
                    if value.get("evidence"):
                        evidence = dict(value["evidence"])
                        evidence.update(
                            subject_id=patient_id,
                            acquisition_id=acquisition["acquisition_id"],
                        )
                        case["evidence"].append(
                            PatientEvidenceItem.model_validate(evidence).model_dump(
                                mode="json"
                            )
                        )
                    return self.json(
                        201,
                        app.patients.save(
                            PatientCase.model_validate(case),
                            expected_revision=value["expected_revision"],
                            actor=principal.actor,
                            reason="Attach acquisition "
                            + acquisition["acquisition_id"],
                        ),
                    )
                if suffix == "hospital-imaging-import":
                    principal.require("write", patient_id)
                    if principal.role != "owner":
                        raise PermissionError("Hospital network imports require the worker owner")
                    snapshot = app.patients.get(patient_id)
                    if value["expected_revision"] != snapshot["revision"]:
                        raise RevisionConflict("Patient changed; reload before hospital imaging import")
                    from .patient_dicomweb import import_hospital_images

                    case, report = import_hospital_images(snapshot["case"], value["config"],
                        output_dir=app.root / "patient-images" / secrets.token_hex(16))
                    result = app.patients.save(case, expected_revision=value["expected_revision"], actor=principal.actor,
                                               reason="Import configured hospital imaging acquisition")
                    return self.json(201, {"snapshot": result, "import": report})
                if suffix in {"encounters", "observations", "inputs", "hospital-import"}:
                    principal.require("write", patient_id)
                    snapshot = app.patients.get(patient_id)
                    if value["expected_revision"] != snapshot["revision"]:
                        raise RevisionConflict(
                            "Patient changed; reload before adding patient data"
                        )
                    case = snapshot["case"]
                    if suffix == "encounters":
                        from .patient_model import PatientEncounter

                        encounter = PatientEncounter.model_validate(value["encounter"])
                        case["encounters"].append(encounter.model_dump(mode="json"))
                    elif suffix == "inputs":
                        from .patient_model import PatientModelInput

                        item = PatientModelInput.model_validate(value["input"])
                        case["model_inputs"] = [
                            x
                            for x in case["model_inputs"]
                            if x["input_id"] != item.input_id
                        ]
                        case["model_inputs"].append(item.model_dump(mode="json"))
                    else:
                        if principal.role != "owner":
                            raise PermissionError(
                                "Observation file imports require the worker owner"
                            )
                        from .patient_hospital import import_fhir_bundle
                        from .patient_ingest import import_observation

                        source_path = app.resolve_uploads(
                            {"$upload": value["upload_id"]}
                        )
                        receipt = next(
                            x for x in app.uploads() if x["id"] == value["upload_id"]
                        )
                        importer = import_fhir_bundle if suffix == "hospital-import" else import_observation
                        case, _ = importer(
                            case,
                            {
                                "file_id": receipt["id"],
                                "uri": Path(source_path).as_uri(),
                                "sha256": receipt["sha256"],
                            },
                            value["mapping"] if suffix == "hospital-import" else value["observation"],
                            output_dir=app.root / "patient-observations" / patient_id,
                        )
                    return self.json(
                        201,
                        app.patients.save(
                            case,
                            expected_revision=value["expected_revision"],
                            actor=principal.actor,
                            reason=value.get("reason") or "Add patient " + suffix,
                        ),
                    )
                if suffix == "circulation-recipe":
                    principal.require("execute", patient_id)
                    from .patient_recipes import circulation_recipe

                    snapshot = app.patients.get(patient_id, value["case_revision"])
                    return self.json(
                        200, circulation_recipe(snapshot["case"], value["encounter_id"])
                    )
                if suffix == "plan-preview":
                    principal.require("execute", patient_id)
                    from .patient_personalization import (
                        PatientPlanJob,
                        patient_stage_capabilities,
                        validate_patient_plan,
                    )

                    expanded = app.patient_request({"payload": value})["payload"]
                    job = PatientPlanJob.model_validate(expanded)
                    manifest = validate_patient_plan(job, verify=True)
                    return self.json(
                        200,
                        {
                            "manifest": manifest,
                            "capabilities": {
                                capability: bool(
                                    app.registry.capability(capability)
                                    and app.registry.capability(capability).available()
                                )
                                for stage in job.stages
                                for capability in patient_stage_capabilities(stage)
                            },
                            "clinical_qualification": "not_established",
                        },
                    )
                if suffix == "longitudinal":
                    from .patient_personalization import longitudinal_changes

                    older = app.patients.get(patient_id, value["older"])
                    newer = app.patients.get(patient_id, value["newer"])
                    return self.json(
                        200, longitudinal_changes(older["case"], newer["case"])
                    )
                if suffix == "compare":
                    return self.json(
                        200,
                        app.patients.compare(
                            patient_id, value["older"], value["newer"]
                        ),
                    )
                if suffix == "runs":
                    principal.require("execute", patient_id)
                    if value.get("kind") not in {
                        "patient_ep",
                        "patient_domain",
                        "patient_plan",
                    }:
                        raise ValueError("Patient runs require a patient workflow kind")
                    if value.get("payload", {}).get("patient_id") != patient_id:
                        raise ValueError("Run patient differs from route patient")
                    return self.json(
                        202,
                        app.submit(
                            app.patient_request(value),
                            self.headers.get("Idempotency-Key"),
                        ),
                    )
            return self.json(404, {"error": "Not found"})

        def dispatch(self):
            try:
                host = self.headers.get("Host", "").split(":")[0]
                if host not in {"127.0.0.1", "localhost", app.public_host}:
                    return self.json(403, {"error": "Host is not permitted"})
                if not self.origin_allowed():
                    return self.json(403, {"error": "Origin is not permitted"})
                path = urlparse(self.path).path
                if not path.startswith("/api/") and self.command == "GET":
                    names = {
                        "/": "index.html",
                        "/index.html": "index.html",
                        "/app.js": "app.js",
                        "/tasks.js": "tasks.js",
                        "/viewer.js": "viewer.js",
                        "/heart-reference.json": "heart-reference.json",
                        "/styles.css": "styles.css",
                        "/virelion-logo.png": "virelion-logo.png",
                        "/catalog.json": "catalog.json",
                    }
                    if path not in names:
                        return self.json(404, {"error": "Not found"})
                    mime = {
                        "html": "text/html; charset=utf-8",
                        "js": "text/javascript",
                        "css": "text/css",
                        "png": "image/png",
                        "json": "application/json",
                    }[names[path].rsplit(".", 1)[-1]]
                    return self.send_bytes(
                        200,
                        files("hearttwin").joinpath("webui", names[path]).read_bytes(),
                        mime,
                    )
                supplied = self.headers.get("Authorization", "")
                principal = app.access.authenticate(supplied)
                if principal is None:
                    return self.json(
                        401, {"error": "A valid workbench token is required"}
                    )
                if path.startswith("/api/patients"):
                    return self.patient_dispatch(path, principal)
                if path == "/api/identity" and self.command == "GET":
                    return self.json(
                        200,
                        {
                            "actor": principal.actor,
                            "role": principal.role,
                            "patients": list(principal.patients),
                        },
                    )
                if principal.role != "owner":
                    if self.command == "GET" and path == "/api/catalog":
                        return self.json(
                            200,
                            {
                                "services": [],
                                "execution": "patient-scoped",
                                "worker": "online",
                                "upload_limit_bytes": 0,
                            },
                        )
                    return self.json(
                        403,
                        {
                            "error": "Use assigned patient endpoints with this credential"
                        },
                    )
                if self.command == "GET" and path == "/api/catalog":
                    return self.json(200, app.catalog())
                if self.command == "GET" and path == "/api/files":
                    return self.json(200, {"files": app.uploads()})
                if self.command == "GET" and path == "/api/jobs":
                    return self.json(
                        200,
                        {
                            "jobs": [
                                app.job(x["job_id"], detail=False)
                                for x in app.queue.list_jobs()
                            ][-200:][::-1]
                        },
                    )
                match = re.fullmatch(r"/api/jobs/([a-zA-Z0-9_-]+)(?:/(.*))?", path)
                if match:
                    identifier, suffix = match.groups()
                    if self.command == "GET" and not suffix:
                        return self.json(200, app.job(identifier))
                    if self.command == "POST" and suffix == "cancel":
                        return self.json(200, app.cancel(identifier))
                    if (
                        self.command == "GET"
                        and suffix
                        and suffix.startswith("artifacts/")
                    ):
                        artifact = app.artifact(
                            identifier, unquote(suffix.removeprefix("artifacts/"))
                        )
                        return self.send_bytes(
                            200,
                            artifact.read_bytes(),
                            "application/octet-stream",
                            'attachment; filename="'
                            + re.sub(r"[^a-zA-Z0-9._-]", "_", artifact.name)
                            + '"',
                        )
                if self.command == "POST" and path in {"/api/files", "/api/jobs"}:
                    if self.headers.get("Transfer-Encoding"):
                        raise ValueError("A fixed Content-Length is required")
                    size = int(self.headers.get("Content-Length", "0"))
                    if path == "/api/files":
                        return self.json(
                            201,
                            app.upload(
                                self.rfile,
                                unquote(self.headers.get("X-Filename", "input.bin")),
                                size,
                            ),
                        )
                    if not 0 < size <= JSON_LIMIT:
                        raise ValueError("Job JSON exceeds its limit or is empty")
                    return self.json(
                        202,
                        app.submit(
                            strict_json(self.rfile.read(size)),
                            self.headers.get("Idempotency-Key"),
                        ),
                    )
                return self.json(404, {"error": "Not found"})
            except PermissionError as exc:
                self.json(403, {"error": str(exc)})
            except RevisionConflict as exc:
                self.json(409, {"error": str(exc)})
            except KeyError:
                self.json(404, {"error": "Requested item is unavailable"})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.json(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - do not expose internal gateway errors to HTTP clients
                self.json(
                    500,
                    {"error": "Gateway operation failed; inspect worker configuration"},
                )

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    app.public_host = getattr(app, "public_host", host)
    return server


def main():
    parser = argparse.ArgumentParser(
        description="Serve the single-owner HeartTwin workbench"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--workspace", type=Path, default=Path("hearttwin-workspace"))
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument(
        "--public-host", help="Exact hostname supplied by your HTTPS reverse proxy"
    )
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    supplied = os.environ.get("HEARTTWIN_WEB_TOKEN")
    if args.host not in {"127.0.0.1", "localhost"} and not supplied:
        parser.error("Non-loopback binding requires an explicit HEARTTWIN_WEB_TOKEN")
    token = supplied or secrets.token_urlsafe(32)
    app = Workbench(
        args.workspace,
        token,
        origins=args.allow_origin,
        timeout=args.timeout,
        patient_credentials=json.loads(
            os.environ.get("HEARTTWIN_PATIENT_CREDENTIALS", "[]")
        ),
    )
    if args.public_host:
        app.public_host = args.public_host
    server = make_server(app, args.host, args.port)
    app.start()
    print(f"HeartTwin workbench: http://{args.host}:{server.server_port}", flush=True)
    if not supplied:
        print("Connection token (keep private): " + token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
