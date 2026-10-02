from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.error

import pytest

from hearttwin.service_registry import ServiceAdapter, ServiceSpec


class _Server:
    def __init__(self, statuses: list[int]):
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
