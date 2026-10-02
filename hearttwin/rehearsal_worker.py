from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from typing import Any
import urllib.request

from .config import load_registry
from .operations import IdempotencyStore
from .provenance import sha256


class RehearsalWorker:
    def __init__(self, service_name: str):
        self.registry = load_registry()
        try:
            self.adapter = self.registry.adapters[service_name]
        except KeyError as exc:
            raise ValueError(f"Unknown HeartTwin service: {service_name}") from exc
        self.service_name = service_name
        self.cache: dict[str, dict[str, Any]] = {}
        self.inflight: dict[str, dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        store_path = os.environ.get("HEARTTWIN_REHEARSAL_IDEMPOTENCY_DB")
        self.idempotency_store = (
            IdempotencyStore(store_path)
            if store_path
            else None
        )
        if service_name == "CardiBridge":
            self._configure_remote_vex()

    def capability_for_path(self, path: str) -> str | None:
        for capability in self.adapter.spec.capabilities:
            if self.adapter._request_path(capability) == path:
                return capability
        return None

    def _configure_remote_vex(self) -> None:
        raw = os.environ.get("HEARTTWIN_REHEARSAL_ENDPOINTS", "{}")
        try:
            endpoints = json.loads(raw)
        except json.JSONDecodeError:
            return
        vex_endpoint = endpoints.get("CardiVex")
        if not vex_endpoint:
            return

        from .cardibridge_adapter import _local_router

        router, _ = _local_router()

        def handler(envelope: Any) -> dict[str, Any]:
            payload = getattr(envelope, "payload", None)
            scenario = None
            entity_id = "hearttwin-entity"
            if isinstance(payload, dict):
                population = payload.get("population")
                if (
                    isinstance(population, list)
                    and population
                    and isinstance(population[0], dict)
                ):
                    entity_id = str(
                        population[0].get("entity_id") or entity_id
                    )
                    candidate = population[0].get("scenario")
                    if isinstance(candidate, dict):
                        scenario = candidate
            if scenario is None:
                raise RuntimeError(
                    "Distributed CardiBridge rehearsal message has no scenario"
                )
            body = {
                "entity_id": entity_id,
                "scenario": scenario,
                "bridge_message_id": envelope.message_id,
            }
            request = urllib.request.Request(
                vex_endpoint.rstrip("/") + "/v1/vex/observe",
                data=json.dumps(body, allow_nan=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-HeartTwin-Idempotency-Key": str(envelope.message_id),
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))

        router.register("agent.challenge", "CardiVex", handler)

    def invoke(
        self,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        delay = payload.pop("_rehearsal_delay_seconds", None)
        if delay is not None:
            delay_value = float(delay)
            if delay_value < 0 or delay_value > 30:
                raise ValueError("_rehearsal_delay_seconds must be in [0, 30]")
            time.sleep(delay_value)

        key = idempotency_key or sha256(
            {
                "service": self.service_name,
                "capability": capability,
                "payload": payload,
            }
        )
        idempotency_store = getattr(self, "idempotency_store", None)
        if idempotency_store is not None:
            persisted = idempotency_store.get(
                self.service_name,
                key,
                capability,
                payload,
            )
            if persisted is not None:
                with self.cache_lock:
                    self.cache[key] = persisted
                return persisted, True

        with self.cache_lock:
            cached = self.cache.get(key)
            if cached is not None:
                return cached, True
            entry = self.inflight.get(key)
            if entry is None:
                entry = {
                    "event": threading.Event(),
                    "result": None,
                    "error": None,
                }
                self.inflight[key] = entry
                leader = True
            else:
                leader = False

        if not leader:
            if not entry["event"].wait(timeout=180.0):
                raise TimeoutError(
                    f"Timed out waiting for in-flight duplicate {key}"
                )
            if entry["error"] is not None:
                raise RuntimeError(
                    f"Original idempotent request failed: {entry['error']}"
                ) from entry["error"]
            result = entry["result"]
            if not isinstance(result, dict):
                raise RuntimeError(
                    "In-flight idempotent request completed without a result"
                )
            return result, True

        try:
            result = self.adapter.invoke(capability, payload)
            if not isinstance(result, dict):
                raise TypeError(
                    f"{self.service_name} returned a non-object response"
                )
        except Exception as exc:
            entry["error"] = exc
            raise
        else:
            entry["result"] = result
            if idempotency_store is not None:
                idempotency_store.put(
                    self.service_name,
                    key,
                    capability,
                    payload,
                    result,
                )
            with self.cache_lock:
                self.cache[key] = result
            return result, False
        finally:
            entry["event"].set()
            with self.cache_lock:
                if self.inflight.get(key) is entry:
                    self.inflight.pop(key, None)


def _handler(worker: RehearsalWorker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HeartTwinRehearsal/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(
            self,
            status: int,
            payload: Any,
            *,
            cached: bool = False,
        ) -> None:
            raw = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "X-HeartTwin-Rehearsal-Cached",
                "1" if cached else "0",
            )
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self._json(404, {"error": "not found"})
                return
            available = worker.adapter.available()
            self._json(
                200 if available else 503,
                {
                    "service": worker.service_name,
                    "status": "ok" if available else "unavailable",
                    "native_available": available,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            capability = worker.capability_for_path(self.path)
            if capability is None:
                self._json(404, {"error": "unknown capability path"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 32 * 1024 * 1024:
                    raise ValueError("request body exceeds rehearsal limit")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("request payload must be a JSON object")
                result, cached = worker.invoke(
                    capability,
                    payload,
                    self.headers.get("X-HeartTwin-Idempotency-Key"),
                )
            except Exception as exc:  # noqa: BLE001
                self._json(
                    500,
                    {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                return
            self._json(200, result, cached=cached)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(prog="hearttwin-rehearsal-worker")
    parser.add_argument("--service", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    worker = RehearsalWorker(args.service)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        _handler(worker),
    )
    server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
