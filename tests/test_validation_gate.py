import pytest

from hearttwin import CardiacStateStore, EvaluationResultPayload, Provenance
from hearttwin.validation_gate import (
    MetricCriterion,
    ValidationGatePolicy,
    apply_validation_gate,
    evaluate_validation_gate,
)


def _report(value: float = 0.9, *, errors=None, warnings=None) -> EvaluationResultPayload:
    return EvaluationResultPayload(
        benchmark_id="b1",
        benchmark_version="1.0",
        task_id="task",
        model_id="model",
        primary_metric="macro_f1",
        primary_value=value,
        metrics=[{"name": "macro_f1", "value": value}],
        errors=list(errors or []),
        warnings=list(warnings or []),
        evaluation_fingerprint="a" * 64,
    )


def _prov(run_id: str) -> Provenance:
    return Provenance(source_service="CardiEval", run_id=run_id)


def test_failed_gate_does_not_promote_state():
    store = CardiacStateStore.new("entity")
    store.transition("evaluated", trigger="fixture", provenance=_prov("eval"))
    policy = ValidationGatePolicy(
        policy_id="internal-v1",
        stage="internal",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.95)],
    )
    decision = apply_validation_gate(store, _report(0.90), policy, _prov("gate"))
    assert not decision.passed
    assert store.state.state_phase == "evaluated"


def test_validation_requires_prespecified_metric_and_phase():
    store = CardiacStateStore.new("entity")
    policy = ValidationGatePolicy(
        policy_id="internal-v1",
        stage="internal",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.8)],
    )
    decision = apply_validation_gate(store, _report(), policy, _prov("gate"))
    assert not decision.passed
    assert "requires state phase" in decision.reasons[0]


def test_validation_progression_is_explicit_and_evidence_gated():
    store = CardiacStateStore.new("entity")
    store.transition("evaluated", trigger="fixture", provenance=_prov("eval"))

    internal = ValidationGatePolicy(
        policy_id="internal-v1",
        stage="internal",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.8)],
    )
    d1 = apply_validation_gate(store, _report(), internal, _prov("internal"))
    assert d1.passed
    assert store.state.state_phase == "internally_validated"

    external = ValidationGatePolicy(
        policy_id="external-v1",
        stage="external",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.8)],
        require_evidence=True,
    )
    blocked = apply_validation_gate(store, _report(), external, _prov("external-none"))
    assert not blocked.passed
    assert store.state.state_phase == "internally_validated"

    d2 = apply_validation_gate(
        store,
        _report(),
        external,
        _prov("external"),
        evidence_ids=["external-benchmark:locked-v1"],
    )
    assert d2.passed
    assert store.state.state_phase == "externally_validated"

    decision_policy = ValidationGatePolicy(
        policy_id="decision-v1",
        stage="decision",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.8)],
        require_evidence=True,
    )
    d3 = apply_validation_gate(
        store,
        _report(),
        decision_policy,
        _prov("decision"),
        evidence_ids=["review:independent-1"],
    )
    assert d3.passed
    assert store.state.state_phase == "decision_eligible"


def test_errors_and_missing_metrics_fail_closed():
    policy = ValidationGatePolicy(
        policy_id="strict",
        stage="internal",
        criteria=[MetricCriterion(name="auroc", minimum=0.8)],
    )
    decision = evaluate_validation_gate(_report(errors=["bad input"]), policy)
    assert not decision.passed
    assert any("error" in reason for reason in decision.reasons)
    assert any("auroc" in reason for reason in decision.reasons)


def test_metric_criterion_requires_a_bound():
    with pytest.raises(ValueError):
        MetricCriterion(name="macro_f1")
