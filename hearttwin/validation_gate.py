"""Prespecified, provenance-linked scientific-evidence gates."""

from __future__ import annotations
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import EvaluationResultPayload, Provenance, ValidationGateArtifact
from .state import CardiacStateStore


class MetricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def has_bound(self) -> "MetricCriterion":
        if self.minimum is None and self.maximum is None:
            raise ValueError("metric criterion requires minimum and/or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("metric criterion minimum cannot exceed maximum")
        return self


class ValidationGatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    evidence_level: Literal[
        "numerical_verification",
        "synthetic_recovery",
        "empirical_validation",
        "clinical_validation",
    ]
    criteria: list[MetricCriterion] = Field(default_factory=list)
    require_no_errors: bool = True
    reject_warnings: bool = False
    require_evaluation_fingerprint: bool = True
    require_evidence: bool = True

    @model_validator(mode="after")
    def evidence_required_for_real_world_claims(self) -> "ValidationGatePolicy":
        if (
            self.evidence_level in {"empirical_validation", "clinical_validation"}
            and not self.require_evidence
        ):
            raise ValueError(
                "empirical/clinical validation policies must require evidence"
            )
        if (
            self.evidence_level in {"empirical_validation", "clinical_validation"}
            and not self.criteria
        ):
            raise ValueError(
                "empirical/clinical validation requires prespecified metric criteria"
            )
        if len({item.name for item in self.criteria}) != len(self.criteria):
            raise ValueError("metric criteria must have unique names")
        return self


class ValidationGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    evidence_level: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    observed_metrics: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


def _metric_values(report: EvaluationResultPayload) -> dict[str, float]:
    values: dict[str, float] = {}
    for metric in report.metrics:
        name, value = metric.get("name"), metric.get("value")
        if (
            isinstance(name, str)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
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
    reasons: list[str] = []
    metrics = _metric_values(report)
    evidence = [str(item) for item in evidence_ids if str(item).strip()]
    names = [item.get("name") for item in report.metrics]
    if len(names) != len(set(str(name) for name in names)):
        reasons.append("duplicate metric names are ambiguous")
    if any(not math.isfinite(value) for value in metrics.values()):
        reasons.append("non-finite evaluation metrics are not admissible")
        metrics = {
            name: value for name, value in metrics.items() if math.isfinite(value)
        }
    if report.primary_metric in metrics and report.primary_value is not None:
        if metrics[report.primary_metric] != report.primary_value:
            reasons.append("primary metric conflicts with the metric table")
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
        evidence_level=policy.evidence_level,
        passed=not reasons,
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
    decision = evaluate_validation_gate(report, policy, evidence_ids=evidence_ids)
    artifact = ValidationGateArtifact(
        gate_id=f"gate-{policy.policy_id}-{len(store.state.validation_gates)}",
        policy_id=policy.policy_id,
        evidence_level=policy.evidence_level,
        passed=decision.passed,
        criteria=[item.model_dump(mode="json") for item in policy.criteria],
        observed_metrics=decision.observed_metrics,
        evidence_ids=decision.evidence_ids,
        reasons=decision.reasons,
    )
    store.record_validation_gate(artifact, provenance)
    return decision
