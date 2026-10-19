"""Single-owner HTTP workbench with authenticated, isolated execution jobs.

Deploy behind HTTPS when remote. This gateway is not a multi-tenant sandbox:
its authenticated owner is trusted to invoke the installed scientific services.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
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
    ):
        if len(token) < 32:
            raise ValueError("Workbench token must contain at least 32 characters")
        if not 1 <= timeout <= 86400 or max_upload < 1:
            raise ValueError("Invalid execution or upload limits")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token, self.origins = token, set(origins)
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
            raise ValueError("Supported job kinds: capability, sequence, mechanistic")
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
        return self.job(identifier)

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
                if not hmac.compare_digest(supplied, "Bearer " + app.token):
                    return self.json(
                        401, {"error": "A valid workbench token is required"}
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
        args.workspace, token, origins=args.allow_origin, timeout=args.timeout
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
