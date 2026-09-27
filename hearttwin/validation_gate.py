"""Explicit scientific validation gates for canonical HeartTwin state.

Evaluation and validation are deliberately separate. A CardiEval report moves a
state to evaluated; promotion beyond that requires a named, prespecified policy
whose criteria are all satisfied.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import EvaluationResultPayload, Provenance, StatePhase
from .state import CardiacStateStore


class MetricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _has_bound(self) -> "MetricCriterion":
        if self.minimum is None and self.maximum is None:
            raise ValueError("metric criterion requires minimum and/or maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("metric criterion minimum cannot exceed maximum")
        return self


class ValidationGatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    stage: Literal["internal", "external", "decision"]
    criteria: list[MetricCriterion] = Field(default_factory=list)
    require_no_errors: bool = True
    reject_warnings: bool = False
    require_evaluation_fingerprint: bool = True
    require_evidence: bool = False


class ValidationGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    stage: Literal["internal", "external", "decision"]
    passed: bool
    target_phase: StatePhase | None = None
    reasons: list[str] = Field(default_factory=list)
    observed_metrics: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


_TARGET_PHASE: dict[str, StatePhase] = {
    "internal": "internally_validated",
    "external": "externally_validated",
    "decision": "decision_eligible",
}

_REQUIRED_SOURCE_PHASE: dict[str, StatePhase] = {
    "internal": "evaluated",
    "external": "internally_validated",
    "decision": "externally_validated",
}


def _metric_values(report: EvaluationResultPayload) -> dict[str, float]:
    values: dict[str, float] = {}
    for metric in report.metrics:
        name = metric.get("name")
        value = metric.get("value")
        if isinstance(name, str) and isinstance(value, (int, float)):
            values[name] = float(value)
    if report.primary_metric and report.primary_value is not None:
        values.setdefault(report.primary_metric, float(report.primary_value))
    return values


def evaluate_validation_gate(
    report: EvaluationResultPayload,
    policy: ValidationGatePolicy,
    *,
    evidence_ids: list[str] | tuple[str, ...] = (),
) -> ValidationGateDecision:
    """Evaluate a prespecified policy without mutating canonical state."""
    reasons: list[str] = []
    metrics = _metric_values(report)
    evidence = [str(item) for item in evidence_ids if str(item).strip()]

    if policy.require_evaluation_fingerprint and not report.evaluation_fingerprint:
        reasons.append("evaluation_fingerprint is required")
    if policy.require_no_errors and report.errors:
        reasons.append(f"evaluation contains {len(report.errors)} error(s)")
    if policy.reject_warnings and report.warnings:
        reasons.append(f"evaluation contains {len(report.warnings)} warning(s)")
    if policy.require_evidence and not evidence:
        reasons.append("traceable validation evidence is required")

    for criterion in policy.criteria:
        value = metrics.get(criterion.name)
        if value is None:
            reasons.append(f"required metric is missing: {criterion.name}")
            continue
        if criterion.minimum is not None and value < criterion.minimum:
            reasons.append(
                f"{criterion.name}={value:g} is below minimum {criterion.minimum:g}"
            )
        if criterion.maximum is not None and value > criterion.maximum:
            reasons.append(
                f"{criterion.name}={value:g} exceeds maximum {criterion.maximum:g}"
            )

    return ValidationGateDecision(
        policy_id=policy.policy_id,
        stage=policy.stage,
        passed=not reasons,
        target_phase=_TARGET_PHASE[policy.stage] if not reasons else None,
        reasons=reasons,
        observed_metrics=metrics,
        evidence_ids=evidence,
    )


def apply_validation_gate(
    store: CardiacStateStore,
    report: EvaluationResultPayload,
    policy: ValidationGatePolicy,
    provenance: Provenance,
    *,
    evidence_ids: list[str] | tuple[str, ...] = (),
) -> ValidationGateDecision:
    """Promote canonical state only when policy and phase preconditions pass."""
    decision = evaluate_validation_gate(report, policy, evidence_ids=evidence_ids)
    if not decision.passed:
        return decision

    required_phase = _REQUIRED_SOURCE_PHASE[policy.stage]
    if store.state.state_phase != required_phase:
        return decision.model_copy(
            update={
                "passed": False,
                "target_phase": None,
                "reasons": [
                    f"{policy.stage} validation requires state phase "
                    f"{required_phase!r}, observed {store.state.state_phase!r}"
                ],
            }
        )

    target = _TARGET_PHASE[policy.stage]
    store.transition(
        target,
        trigger=f"validation_gate:{policy.policy_id}",
        provenance=provenance,
        details={
            "policy_id": policy.policy_id,
            "stage": policy.stage,
            "criteria": [item.model_dump(mode="json") for item in policy.criteria],
            "observed_metrics": decision.observed_metrics,
            "evidence_ids": decision.evidence_ids,
        },
    )
    return decision
