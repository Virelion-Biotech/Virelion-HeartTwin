import pytest

from hearttwin.contracts import EvaluationResultPayload, Provenance
from hearttwin.state import CardiacStateStore, CardiacStateValidationError


def _prov(run_id: str) -> Provenance:
    return Provenance(source_service="fixture", run_id=run_id, content_sha256="a" * 64)


def _evaluated_store() -> CardiacStateStore:
    store = CardiacStateStore.new("subject-1")
    store.record_evaluation(
        EvaluationResultPayload(
            benchmark_id="locked",
            benchmark_version="1.0",
            task_id="classification",
            model_id="model-1",
            primary_metric="macro_f1",
            primary_value=0.8,
            metrics=[{"name": "macro_f1", "value": 0.8}],
            evaluation_fingerprint="b" * 64,
        ),
        _prov("eval-1"),
    )
    return store


def test_validation_gates_require_explicit_progression() -> None:
    store = _evaluated_store()
    assert store.state.state_phase == "evaluated"

    store.apply_validation_gate(
        "internally_validated",
        criteria={
            "locked_holdout": True,
            "independent_ground_truth": True,
            "prespecified_metric": True,
        },
        provenance=_prov("internal-1"),
    )
    assert store.state.state_phase == "internally_validated"

    store.apply_validation_gate(
        "externally_validated",
        criteria={
            "independent_external_dataset": True,
            "reproduced_primary_result": True,
        },
        evidence_ids=["external-dataset:GSE-fixture"],
        provenance=_prov("external-1"),
    )
    assert store.state.state_phase == "externally_validated"

    store.apply_validation_gate(
        "decision_eligible",
        criteria={
            "safety_review": True,
            "uncertainty_review": True,
            "domain_scope_defined": True,
        },
        evidence_ids=["review:safety", "review:uncertainty"],
        provenance=_prov("decision-1"),
    )
    snapshot = store.snapshot()
    assert snapshot.state_phase == "decision_eligible"
    assert [gate.target_phase for gate in snapshot.validation_gates] == [
        "internally_validated",
        "externally_validated",
        "decision_eligible",
    ]


def test_validation_gate_rejects_missing_scientific_criterion() -> None:
    store = _evaluated_store()
    with pytest.raises(CardiacStateValidationError, match="criteria not satisfied"):
        store.apply_validation_gate(
            "internally_validated",
            criteria={
                "locked_holdout": True,
                "independent_ground_truth": False,
                "prespecified_metric": True,
            },
        )


def test_external_validation_requires_explicit_external_evidence() -> None:
    store = _evaluated_store()
    store.apply_validation_gate(
        "internally_validated",
        criteria={
            "locked_holdout": True,
            "independent_ground_truth": True,
            "prespecified_metric": True,
        },
    )
    with pytest.raises(CardiacStateValidationError, match="evidence_ids"):
        store.apply_validation_gate(
            "externally_validated",
            criteria={
                "independent_external_dataset": True,
                "reproduced_primary_result": True,
            },
        )
