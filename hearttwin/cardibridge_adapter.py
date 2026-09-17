"""In-process CardiBridge adapter for HeartTwin.

Uses the installed ``cardibridge`` package when available. Falls back to the
HTTP gateway at ``CARDIBRIDGE_URL`` / service endpoint when the library is not
importable. Does not change CardiBridge public APIs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .provenance import new_run_id, sha256


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cardibridge_available() -> bool:
    try:
        import cardibridge  # noqa: F401
        return True
    except ImportError:
        return False


def _local_router():
    """Lazy singleton ProductionRouter + default registry for in-process use."""
    global _ROUTER, _REGISTRY
    try:
        return _ROUTER, _REGISTRY
    except NameError:
        pass
    from cardibridge.defaults import default_registry
    from cardibridge.production import ProductionRouter
    from cardibridge.store import EventStore

    _REGISTRY = default_registry()
    store_path = os.environ.get("CARDIBRIDGE_STORE_PATH", ":memory:")
    _ROUTER = ProductionRouter(_REGISTRY, EventStore(store_path))
    return _ROUTER, _REGISTRY


def _http_post(base: str, path: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    key = os.environ.get("CARDIBRIDGE_GATEWAY_KEY")
    if key:
        req.add_header("X-Bridge-Key", key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(base: str, path: str, timeout: float = 15.0) -> Any:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either a full BridgeEnvelope dict or a capability-style HeartTwin payload.

    Deterministic fields (idempotency_key, challenge_id, optional message_id/timestamp)
    keep content hashes stable so duplicate publishes are recognized.
    """
    if "message_type" in payload and "idempotency_key" in payload and "payload" in payload:
        # Full envelope-like dict; strip HeartTwin-only keys if any
        return {
            k: v
            for k, v in payload.items()
            if k
            in {
                "message_id",
                "message_type",
                "producer",
                "consumer",
                "idempotency_key",
                "payload",
                "trace",
                "timestamp",
                "signature",
                "execution",
                "artifact_refs",
            }
        }

    from cardibridge import AgentChallenge, BridgeEnvelope, TraceContext

    entity_id = str(payload.get("entity_id") or "hearttwin-entity")
    message_type = str(payload.get("message_type") or "agent.challenge")
    consumer = str(payload.get("consumer") or "worker")
    producer = str(payload.get("producer") or "hearttwin")
    key = str(payload.get("idempotency_key") or f"ht-{entity_id}-{new_run_id(entity_id, payload)[:24]}")

    # Stable trace + challenge_id so identical HeartTwin payloads hash identically
    trace = TraceContext(
        source="hearttwin",
        trace_id=str(payload.get("trace_id") or sha256({"key": key, "entity": entity_id})[:32]),
        span_id=str(payload.get("span_id") or sha256({"key": key, "span": "1"})[:16]),
        created_at=datetime.fromisoformat(payload["created_at"])
        if payload.get("created_at")
        else datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    if message_type == "agent.challenge":
        challenge_payload = AgentChallenge(
            challenge_id=str(payload.get("challenge_id") or sha256({"key": key})[:32]),
            challenge_type=str(payload.get("challenge_type") or "integration"),
            population=list(payload.get("population") or [{"cell": "cardiomyocyte", "entity_id": entity_id}]),
            intended_task=str(payload.get("intended_task") or "hearttwin-bridge"),
            trace=trace,
        ).model_dump(mode="json")
    else:
        challenge_payload = dict(payload.get("contract_payload") or payload)

    kwargs: dict[str, Any] = {}
    if payload.get("message_id"):
        kwargs["message_id"] = str(payload["message_id"])
    ts = (
        datetime.fromisoformat(payload["timestamp"])
        if payload.get("timestamp")
        else datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    env = BridgeEnvelope(
        message_type=message_type,
        producer=producer,
        consumer=consumer,
        idempotency_key=key,
        payload=challenge_payload,
        trace=trace,
        timestamp=ts,
        **kwargs,
    )
    return env.model_dump(mode="json")


def invoke_cardibridge(
    capability: str,
    payload: dict[str, Any],
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Dispatch a HeartTwin capability request to CardiBridge (local or HTTP)."""
    base = (endpoint or os.environ.get("CARDIBRIDGE_URL") or "").strip() or None
    use_local = cardibridge_available() and (not base or os.environ.get("CARDIBRIDGE_FORCE_LOCAL") == "1")

    if capability == "bridge.validate":
        return _validate(payload, use_local=use_local, base=base)
    if capability == "bridge.publish":
        return _publish(payload, use_local=use_local, base=base)
    if capability == "bridge.contracts":
        return _contracts(use_local=use_local, base=base)
    if capability == "bridge.asyncapi":
        return _asyncapi(use_local=use_local, base=base)
    if capability == "bridge.replay":
        return _replay(payload, use_local=use_local, base=base)
    if capability == "bridge.health":
        return _health(use_local=use_local, base=base)
    raise ValueError(f"CardiBridge adapter does not support capability {capability!r}")


def _validate(payload: dict[str, Any], *, use_local: bool, base: str | None) -> dict[str, Any]:
    envelope = _build_envelope(payload)
    if use_local:
        from cardibridge.contracts import BridgeEnvelope

        router, registry = _local_router()
        obj = BridgeEnvelope.model_validate(envelope)
        report = registry.validate(obj.message_type, obj.payload)
        return {
            "valid": report.valid,
            "report": report.model_dump(mode="json"),
            "message_id": obj.message_id,
            "transport": "in-process",
            "content_sha256": sha256(json.dumps(envelope, sort_keys=True)),
        }
    if not base:
        raise RuntimeError(
            "CardiBridge not installed and CARDIBRIDGE_URL is unset; "
            "pip install virelion-cardibridge (or clone) or set CARDIBRIDGE_URL"
        )
    try:
        result = _http_post(base, "/v1/validate", envelope)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CardiBridge validate failed ({exc.code}): {body}") from exc
    result["transport"] = "http"
    return result


def _publish(payload: dict[str, Any], *, use_local: bool, base: str | None) -> dict[str, Any]:
    envelope = _build_envelope(payload)
    if use_local:
        from cardibridge.contracts import BridgeEnvelope

        router, registry = _local_router()
        obj = BridgeEnvelope.model_validate(envelope)
        report = registry.validate(obj.message_type, obj.payload)
        if not report.valid:
            return {
                "status": "invalid",
                "report": report.model_dump(mode="json"),
                "message_id": obj.message_id,
                "transport": "in-process",
            }

        # Register a default no-op handler for demo/e2e if none exists
        try:
            result = router.dispatch(obj)
        except LookupError:
            def _default_handler(_env: Any) -> dict[str, Any]:
                return {"ok": True, "echo_type": _env.message_type}

            router.register(obj.message_type, obj.consumer, _default_handler)
            result = router.dispatch(obj)

        status = result.get("status") if isinstance(result, dict) else None
        return {
            "status": status if status in {"processed", "duplicate"} else "processed",
            "message_id": obj.message_id,
            "result": result,
            "transport": "in-process",
            "content_sha256": sha256(json.dumps(envelope, sort_keys=True)),
        }
    if not base:
        raise RuntimeError("CardiBridge not installed and CARDIBRIDGE_URL is unset")
    try:
        result = _http_post(base, "/v1/messages", envelope)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CardiBridge publish failed ({exc.code}): {body}") from exc
    result["transport"] = "http"
    return result


def _contracts(*, use_local: bool, base: str | None) -> dict[str, Any]:
    if use_local:
        _, registry = _local_router()
        return {"catalog": registry.catalog(), "transport": "in-process"}
    if not base:
        raise RuntimeError("CardiBridge not installed and CARDIBRIDGE_URL is unset")
    return {"catalog": _http_get(base, "/v1/contracts"), "transport": "http"}


def _asyncapi(*, use_local: bool, base: str | None) -> dict[str, Any]:
    if use_local:
        from cardibridge.catalog import export_asyncapi

        _, registry = _local_router()
        return {"asyncapi": export_asyncapi(registry), "transport": "in-process"}
    if not base:
        raise RuntimeError("CardiBridge not installed and CARDIBRIDGE_URL is unset")
    return {"asyncapi": _http_get(base, "/v1/asyncapi"), "transport": "http"}


def _replay(payload: dict[str, Any], *, use_local: bool, base: str | None) -> dict[str, Any]:
    topic = payload.get("topic")
    after = int(payload.get("after") or 0)
    limit = payload.get("limit")
    limit_i = int(limit) if limit is not None else 100
    if use_local:
        router, _ = _local_router()
        items = list(router.replay(topic, after, limit_i))
        return {
            "events": [
                {"seq": seq, "envelope": env.model_dump(mode="json")} for seq, env in items
            ],
            "transport": "in-process",
        }
    if not base:
        raise RuntimeError("CardiBridge not installed and CARDIBRIDGE_URL is unset")
    q = f"/v1/replay?after={after}&limit={limit_i}"
    if topic:
        q += f"&topic={topic}"
    return {"events": _http_get(base, q), "transport": "http"}


def _health(*, use_local: bool, base: str | None) -> dict[str, Any]:
    if use_local:
        from cardibridge.health import health as cb_health

        router, registry = _local_router()
        snap = cb_health(registry, router.store)
        data = snap.as_dict()
        data["transport"] = "in-process"
        return data
    if not base:
        return {"status": "unavailable", "transport": "none"}
    try:
        data = _http_get(base, "/health")
        data["transport"] = "http"
        return data
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc), "transport": "http"}
