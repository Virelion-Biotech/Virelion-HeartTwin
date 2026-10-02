from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
import urllib.error

import pytest

from hearttwin.service_registry import ServiceAdapter, ServiceSpec


class _Server:
    def __init__(self, statuses: list[int | None]):
        self.statuses = list(statuses)
        self.requests: list[dict[str, str]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                parent.requests.append(
                    {
                        "path": self.path,
                        "idempotency": self.headers.get(
                            "X-HeartTwin-Idempotency-Key",
                            "",
                        ),
                        "attempt": self.headers.get("X-HeartTwin-Attempt", ""),
                        "body": body.decode("utf-8"),
                    }
                )
                status = (
                    parent.statuses.pop(0)
                    if parent.statuses
                    else 200
                )
                if status is None:
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                payload = (
                    {"error": "transient"}
                    if status >= 400
                    else {"ok": True}
                )
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _adapter(endpoint: str) -> ServiceAdapter:
    return ServiceAdapter(
        ServiceSpec(
            name="fixture",
            repository="fixture",
            capabilities=("atlas.search",),
            endpoint=endpoint,
        )
    )


def test_http_retry_reuses_same_idempotency_key(monkeypatch) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "3")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")

    with _Server([503, 200]) as server:
        result = _adapter(server.endpoint).invoke(
            "atlas.search",
            {"entity_id": "C1", "query": "canary"},
        )

    assert result == {"ok": True}
    assert len(server.requests) == 2
    assert server.requests[0]["idempotency"]
    assert (
        server.requests[0]["idempotency"]
        == server.requests[1]["idempotency"]
    )
    assert [item["attempt"] for item in server.requests] == ["1", "2"]
    assert server.requests[0]["body"] == server.requests[1]["body"]


def test_http_client_error_is_not_retried(monkeypatch) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "3")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")

    with _Server([400, 200]) as server:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _adapter(server.endpoint).invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "bad"},
            )

    assert exc_info.value.code == 400
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HEARTTWIN_HTTP_ATTEMPTS", "0"),
        ("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "0"),
        ("HEARTTWIN_HTTP_BACKOFF_SECONDS", "-1"),
        ("HEARTTWIN_HTTP_MAX_RESPONSE_BYTES", "0"),
    ],
)
def test_invalid_http_retry_configuration_fails_closed(
    monkeypatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv(name, value)

    with _Server([200]) as server:
        with pytest.raises(ValueError, match="retry configuration"):
            _adapter(server.endpoint).invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "x"},
            )

    assert server.requests == []



def test_http_retry_recovers_when_service_starts_during_outage(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "6")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0.05")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "0.2")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    requests: list[str] = []
    ready = threading.Event()
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            requests.append(
                self.headers.get("X-HeartTwin-Idempotency-Key", "")
            )
            raw = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server_holder: list[ThreadingHTTPServer] = []

    def delayed_server() -> None:
        time.sleep(0.12)
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server_holder.append(server)
        ready.set()
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()
            stopped.set()

    thread = threading.Thread(target=delayed_server, daemon=True)
    thread.start()
    try:
        adapter = _adapter(f"http://127.0.0.1:{port}")
        result = adapter.invoke(
            "atlas.search",
            {"entity_id": "C1", "query": "recover"},
        )
        assert result == {"ok": True}
        assert ready.is_set()
        assert len(requests) == 1
        assert requests[0]
    finally:
        if server_holder:
            server_holder[0].shutdown()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert stopped.is_set()



def test_http_retry_recovers_after_response_connection_drop(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "3")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "1")

    requests: list[dict[str, str]] = []
    dropped = False
    parent = {"dropped": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            requests.append(
                {
                    "idempotency": self.headers.get(
                        "X-HeartTwin-Idempotency-Key",
                        "",
                    ),
                    "attempt": self.headers.get("X-HeartTwin-Attempt", ""),
                    "body": body,
                }
            )
            if not parent["dropped"]:
                parent["dropped"] = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return

            raw = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    try:
        host, port = server.server_address
        result = _adapter(f"http://{host}:{port}").invoke(
            "atlas.search",
            {"entity_id": "C1", "query": "drop-response"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result == {"ok": True}
    assert len(requests) == 2
    assert requests[0]["idempotency"]
    assert requests[0]["idempotency"] == requests[1]["idempotency"]
    assert [item["attempt"] for item in requests] == ["1", "2"]
    assert requests[0]["body"] == requests[1]["body"]



def test_http_remote_disconnect_is_retried_with_same_idempotency_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "3")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")

    with _Server([None, 200]) as server:
        result = _adapter(server.endpoint).invoke(
            "atlas.search",
            {"entity_id": "C1", "query": "worker-restart"},
        )

    assert result == {"ok": True}
    assert len(server.requests) == 2
    assert [item["attempt"] for item in server.requests] == ["1", "2"]
    assert (
        server.requests[0]["idempotency"]
        == server.requests[1]["idempotency"]
    )
    assert server.requests[0]["body"] == server.requests[1]["body"]



def test_http_response_rejects_declared_oversize_body(monkeypatch) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("HEARTTWIN_HTTP_MAX_RESPONSE_BYTES", "64")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            raw = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with pytest.raises(RuntimeError, match="exceeds 64 bytes"):
            _adapter(f"http://{host}:{port}").invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "oversize"},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_response_rejects_streamed_oversize_body(monkeypatch) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("HEARTTWIN_HTTP_MAX_RESPONSE_BYTES", "64")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            raw = b'{"payload":"' + (b"x" * 256) + b'"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with pytest.raises(RuntimeError, match="exceeds 64 bytes"):
            _adapter(f"http://{host}:{port}").invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "streamed-oversize"},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_response_requires_json_object(monkeypatch) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("HEARTTWIN_HTTP_MAX_RESPONSE_BYTES", "1024")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            raw = b'["not", "an", "object"]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with pytest.raises(RuntimeError, match="must be a JSON object"):
            _adapter(f"http://{host}:{port}").invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "bad-shape"},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_http_transport_rejects_nonstandard_json_response(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            raw = b'{"score": NaN}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with pytest.raises(ValueError, match="non-standard JSON constant"):
            _adapter(f"http://{host}:{port}").invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "strict-json"},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_transport_requires_json_object_response(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEARTTWIN_HTTP_ATTEMPTS", "1")
    monkeypatch.setenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "2")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            raw = b'[1, 2, 3]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with pytest.raises(TypeError, match="expected a JSON object"):
            _adapter(f"http://{host}:{port}").invoke(
                "atlas.search",
                {"entity_id": "C1", "query": "object-only"},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
