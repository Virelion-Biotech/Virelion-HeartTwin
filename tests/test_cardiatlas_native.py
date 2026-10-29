from __future__ import annotations

import pytest

cardiatlas = pytest.importorskip("cardiatlas")

from cardiatlas import EvidenceRecord, MarkerRecord, SQLiteAtlasStore

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
        ("atlas.retrieve", {"query": "POSTN", "limit": True}),
        ("atlas.explain", {"record_id": ""}),
        ("atlas.graph", {"record_id": "marker:postn", "hops": True}),
        ("atlas.context", {"record_ids": "marker:postn"}),
        ("atlas.context", {"record_ids": [1]}),
        ("atlas.context", {"records": ["not-an-object"]}),
    ]
    for capability, payload in invalid:
        with pytest.raises(ValueError):
            _cardiatlas(capability, payload)


def test_cardiatlas_missing_configured_db_bootstraps_seed(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "nested" / "missing.sqlite"
    monkeypatch.setenv("CARDIATLAS_DB", str(missing))
    result = _cardiatlas("atlas.retrieve", {"query": "GSE132880", "limit": 10})
    assert missing.is_file()
    assert any(item["record"]["id"] == "dataset:gse132880" for item in result["results"])


def test_cardiatlas_native_retrieval_and_explanation(tmp_path, monkeypatch) -> None:
    db = tmp_path / "atlas.sqlite"
    with SQLiteAtlasStore(db) as store:
        store.upsert(_marker())
    monkeypatch.setenv("CARDIATLAS_DB", str(db))
    retrieved = _cardiatlas("atlas.retrieve", {"query": "POSTN", "limit": 5})
    assert retrieved["results"][0]["record"]["id"] == "marker:postn"
    assert 0 < retrieved["results"][0]["lexical_score"] <= 1
    explained = _cardiatlas("atlas.explain", {"record_id": "marker:postn"})
    assert explained["record"]["id"] == "marker:postn"


def test_cardiatlas_claim_capabilities_use_versioned_api_when_installed(tmp_path, monkeypatch) -> None:
    from cardiatlas.api import AtlasAPI
    if not hasattr(AtlasAPI, "claim"):
        pytest.skip("Requires CardiAtlas claim-persistence release")
    db = tmp_path / "atlas.sqlite"
    with SQLiteAtlasStore(db) as store:
        service = store.load_service()
        service.add(MarkerRecord(id="marker:postn", name="POSTN", entity_id="POSTN"))
        service.add(EvidenceRecord(id="evidence:1", name="PMID 1", source_type="pubmed", source_identifier="1", polarity="supports", evidence_level="primary"))
        citation = cardiatlas.ClaimEvidence(
            evidence_id="evidence:1",
            source_locator="https://pubmed.ncbi.nlm.nih.gov/1/",
            locator_type="abstract",
            review_status="source_verified",
        )
        service.add_claim(cardiatlas.Claim(
            id="claim:example", subject="marker:postn", predicate="associated_with",
            object="state:activated_fibroblast", polarity="supports",
            evidence_ids=("evidence:1",), citations=(citation,),
        ))
        store.save_service(service)
    monkeypatch.setenv("CARDIATLAS_DB", str(db))
    claim = _cardiatlas("atlas.claim", {"claim_id": "claim:example"})
    assert claim["claim"]["id"] == "claim:example"
    assert claim["assessment"]["status"] == "supported"
    assert claim["citations"][0]["source_locator"] == "https://pubmed.ncbi.nlm.nih.gov/1/"
    assert _cardiatlas("atlas.claims", {})["count"] == 1


def test_cardiatlas_empty_persistent_store_seeds_metadata_graph_and_claims_when_supported(tmp_path, monkeypatch) -> None:
    from cardiatlas.api import AtlasAPI

    db = tmp_path / "persistent" / "cardiatlas" / "atlas.sqlite"
    monkeypatch.setenv("CARDIATLAS_DB", str(db))

    result = _cardiatlas("atlas.retrieve", {"query": "GSE132880 cardiac endothelial", "limit": 10})
    assert any(item["record"]["id"] == "dataset:gse132880" for item in result["results"])

    with SQLiteAtlasStore(db) as store:
        assert store.count() == 11
        assert len(store.all_relations()) == 17
        store.upsert(_marker("marker:local-note", "LOCALNOTE"))
        if hasattr(AtlasAPI, "claim"):
            assert [item.id for item in store.all_claims()] == ["claim:gse132880:design"]

    # Reopening must preserve the curated reference and user data without reseeding.
    reopened = _cardiatlas_api_for_test()
    assert reopened.health()["record_count"] == 12
    if hasattr(AtlasAPI, "claim"):
        claim = _cardiatlas("atlas.claim", {"claim_id": "claim:gse132880:design"})
        assert claim["assessment"]["status"] == "supported"
        readiness = _cardiatlas("atlas.dataset.readiness", {
            "study_id": "study:gse132880", "dataset_id": "dataset:gse132880"
        })
        assert readiness["ready"] is True
        assert readiness["checks"]["subject_mapping_provenance"] is True


def _cardiatlas_api_for_test():
    from hearttwin.native_services import _cardiatlas_api
    return _cardiatlas_api({})
