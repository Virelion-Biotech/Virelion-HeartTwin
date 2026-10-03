"""HeartTwin ↔ CardiBridge adapter tests (in-process)."""
from __future__ import annotations

import pytest

pytest.importorskip("cardibridge")

from hearttwin.cardibridge_adapter import cardibridge_available, invoke_cardibridge
from hearttwin.service_registry import ServiceAdapter, ServiceSpec


def test_cardibridge_importable():
    assert cardibridge_available() is True


def test_bridge_validate_agent_challenge():
    result = invoke_cardibridge(
        "bridge.validate",
        {
            "entity_id": "e-validate-1",
            "message_type": "agent.challenge",
            "challenge_type": "integration",
            "intended_task": "unit-test",
            "population": [{"cell": "cardiomyocyte"}],
        },
    )
    assert result["valid"] is True
    assert result["transport"] == "in-process"
    assert result["message_id"]


def test_bridge_publish_and_duplicate():
    payload = {
        "entity_id": "e-pub-1",
        "message_type": "agent.challenge",
        "idempotency_key": "ht-dup-key-001",
        "challenge_type": "integration",
        "intended_task": "unit-test",
        "population": [{"cell": "cardiomyocyte"}],
        "consumer": "worker",
    }
    from hearttwin.cardibridge_adapter import _local_router
    router, _ = _local_router()
    router.register("agent.challenge", "worker", lambda envelope: {"handled": True})
    first = invoke_cardibridge("bridge.publish", payload)
    payload["message_id"] = first["message_id"]
    second = invoke_cardibridge("bridge.publish", payload)
    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert second["result"] == first["result"] == {"handled": True}
    assert first["message_id"] == second["message_id"]


def test_bridge_contracts_and_asyncapi():
    contracts = invoke_cardibridge("bridge.contracts", {})
    assert "agent.challenge" in str(contracts["catalog"]) or "catalog" in contracts
    asyncapi = invoke_cardibridge("bridge.asyncapi", {})
    doc = asyncapi["asyncapi"]
    assert doc.get("asyncapi") == "3.0.0" or "asyncapi" in doc


def test_service_adapter_builtin_path():
    spec = ServiceSpec(
        name="CardiBridge",
        repository="Virelion-Biotech/Virelion-CardiBridge",
        capabilities=("bridge.validate", "bridge.publish", "bridge.health"),
        builtin="cardibridge",
    )
    adapter = ServiceAdapter(spec)
    assert adapter.available() is True
    health = adapter.invoke("bridge.health", {})
    assert health.get("transport") in {"in-process", "http"}
    assert "status" in health or "store_ok" in health or "ok" in str(health).lower()


def test_bridge_validate_benchmark_admission():
    result = invoke_cardibridge(
        "bridge.validate",
        {
            "entity_id": "admission-validate-1",
            "message_type": "benchmark.admission",
            "contract_payload": {
                "assessment_id": "admission-1",
                "benchmark_id": "candidate",
                "benchmark_version": "1.0",
                "policy": "subject_heldout",
                "status": "blocked",
                "ready_for_review": False,
                "blockers": ["required metadata group_id missing for 1 samples"],
                "warnings": [],
                "required_fields": ["sample_id", "group_id", "study_id", "label"],
                "missing_by_field": {"group_id": ["s1"]},
                "statistics": {"samples": 6, "groups": 5},
                "materialization_preview": None,
                "trace": {
                    "source": "CardiBench",
                    "trace_id": "a" * 32,
                    "span_id": "b" * 16,
                    "created_at": "2026-10-03T20:00:00+00:00",
                },
            },
        },
    )
    assert result["valid"] is True
    assert result["transport"] == "in-process"
