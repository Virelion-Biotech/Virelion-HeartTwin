from __future__ import annotations

import pytest

cardiatlas = pytest.importorskip("cardiatlas")

from cardiatlas import MarkerRecord, SQLiteAtlasStore

from hearttwin.native_services import _cardiatlas


def _marker(record_id: str = "marker:postn", name: str = "POSTN") -> MarkerRecord:
    return MarkerRecord(id=record_id, name=name, entity_id=name)


def test_cardiatlas_native_uses_persistent_store_when_configured(tmp_path, monkeypatch) -> None:
    db = tmp_path / "atlas.sqlite"
    with SQLiteAtlasStore(db) as store:
        store.upsert(_marker())

    monkeypatch.setenv("CARDIATLAS_DB", str(db))

    search = _cardiatlas("atlas.search", {"query": "POSTN", "limit": 5})
    assert search["contract_version"] == "1.0"
    assert search["count"] == 1
    assert search["records"][0]["id"] == "marker:postn"

    context = _cardiatlas(
        "atlas.context",
        {"entity_id": "sample-1", "record_ids": ["marker:postn"]},
    )
    assert context["context_id"] == "ctx-sample-1"
    assert context["context"]["marker_ids"] == ["marker:postn"]
    assert context["provenance"] == ["marker:postn"]


def test_cardiatlas_native_transient_records_overlay_without_db(monkeypatch) -> None:
    monkeypatch.delenv("CARDIATLAS_DB", raising=False)
    marker = _marker("marker:tnnt2", "TNNT2").to_dict()

    context = _cardiatlas(
        "atlas.context",
        {
            "record_ids": ["marker:tnnt2", "evidence:missing"],
            "records": [marker],
        },
    )
    assert context["record_ids"] == ["marker:tnnt2", "evidence:missing"]
    assert context["context"]["marker_ids"] == ["marker:tnnt2"]
    assert context["provenance"] == ["marker:tnnt2"]
    assert context["context"]["metadata"]["missing_record_ids"] == ["evidence:missing"]


def test_cardiatlas_native_omitted_inputs_do_not_fabricate_context_or_dump_records(tmp_path, monkeypatch) -> None:
    db = tmp_path / "atlas.sqlite"
    with SQLiteAtlasStore(db) as store:
        store.upsert(_marker())
    monkeypatch.setenv("CARDIATLAS_DB", str(db))

    search = _cardiatlas("atlas.search", {"entity_id": "sample-1"})
    assert search == {"contract_version": "1.0", "query": "", "records": [], "count": 0}

    context = _cardiatlas("atlas.context", {"entity_id": "sample-1"})
    assert context["record_ids"] == []
    assert context["provenance"] == []
    assert context["context"]["marker_ids"] == []


def test_cardiatlas_native_validates_payload_types(monkeypatch) -> None:
    monkeypatch.delenv("CARDIATLAS_DB", raising=False)
    invalid = [
        ("atlas.search", {"query": 123}),
        ("atlas.search", {"query": "POSTN", "tags": "fibrosis"}),
        ("atlas.search", {"query": "POSTN", "limit": True}),
        ("atlas.context", {"record_ids": "marker:postn"}),
        ("atlas.context", {"record_ids": [1]}),
        ("atlas.context", {"records": ["not-an-object"]}),
    ]
    for capability, payload in invalid:
        with pytest.raises(ValueError):
            _cardiatlas(capability, payload)


def test_cardiatlas_native_missing_configured_db_fails_closed(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("CARDIATLAS_DB", str(missing))
    with pytest.raises(RuntimeError, match="CARDIATLAS_DB does not exist"):
        _cardiatlas("atlas.context", {"record_ids": []})
