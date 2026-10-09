import pytest
from hearttwin import CardiacStateStore, EvaluationResultPayload, Provenance
from hearttwin.validation_gate import (
    MetricCriterion,
    ValidationGatePolicy,
    apply_validation_gate,
    evaluate_validation_gate,
)


def _report(value=0.9, *, errors=None, warnings=None):
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


def _prov(run_id):
    return Provenance(source_service="CardiEval", run_id=run_id)


def test_gate_is_recorded_without_promoting_biological_phase():
    store = CardiacStateStore.new("entity")
    policy = ValidationGatePolicy(
        policy_id="empirical-v1",
        evidence_level="empirical_validation",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.8)],
    )
    decision = apply_validation_gate(
        store,
        _report(),
        policy,
        _prov("gate"),
        evidence_ids=["locked-cohort:v1"],
    )
    assert decision.passed
    snapshot = store.snapshot()
    assert snapshot.state_phase == "unknown"
    assert snapshot.validation_gates[0].passed is True
    assert snapshot.validation_gates[0].evidence_level == "empirical_validation"


def test_failed_gate_remains_auditable():
    store = CardiacStateStore.new("entity")
    policy = ValidationGatePolicy(
        policy_id="numerical-v1",
        evidence_level="numerical_verification",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.95)],
        require_evidence=False,
    )
    decision = apply_validation_gate(store, _report(0.90), policy, _prov("gate"))
    assert not decision.passed
    assert store.snapshot().validation_gates[0].reasons


def test_empirical_policy_cannot_disable_evidence_requirement():
    with pytest.raises(ValueError, match="must require evidence"):
        ValidationGatePolicy(
            policy_id="bad",
            evidence_level="empirical_validation",
            require_evidence=False,
        )


def test_missing_metrics_and_errors_fail_closed():
    policy = ValidationGatePolicy(
        policy_id="strict",
        evidence_level="numerical_verification",
        criteria=[MetricCriterion(name="auroc", minimum=0.8)],
        require_evidence=False,
    )
    decision = evaluate_validation_gate(_report(errors=["bad input"]), policy)
    assert not decision.passed
    assert any("error" in reason for reason in decision.reasons)
    assert any("auroc" in reason for reason in decision.reasons)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_metrics_never_pass(value):
    policy = ValidationGatePolicy(
        policy_id="finite",
        evidence_level="numerical_verification",
        criteria=[MetricCriterion(name="macro_f1", minimum=0.5)],
        require_evidence=False,
    )
    assert not evaluate_validation_gate(_report(value), policy).passed


def test_nonfinite_bounds_and_duplicate_criteria_rejected():
    with pytest.raises(ValueError):
        MetricCriterion(name="x", minimum=float("nan"))
    with pytest.raises(ValueError, match="unique"):
        ValidationGatePolicy(
            policy_id="x",
            evidence_level="numerical_verification",
            criteria=[
                MetricCriterion(name="x", minimum=0),
                MetricCriterion(name="x", maximum=1),
            ],
        )
