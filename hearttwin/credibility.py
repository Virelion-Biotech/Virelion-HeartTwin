"""Context-specific scientific evidence contracts; absent evidence never implies validity."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

DataRole = Literal[
    "training",
    "calibration",
    "tuning",
    "internal_validation",
    "external_validation",
    "challenge",
    "prospective",
]
UncertaintyKind = Literal[
    "measurement",
    "segmentation",
    "registration",
    "scar",
    "fiber",
    "parameter",
    "numerical",
    "model_discrepancy",
]


class ScientificModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, str_strip_whitespace=True
    )


class ContextOfUse(ScientificModel):
    context_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    population: str = Field(min_length=1)
    endpoints: tuple[str, ...] = Field(min_length=1)
    applicability_domain: tuple[str, ...] = Field(min_length=1)
    model_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    required_uncertainty_kinds: tuple[UncertaintyKind, ...] = (
        "measurement",
        "parameter",
        "numerical",
        "model_discrepancy",
    )


class DataRoleRecord(ScientificModel):
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ids: tuple[str, ...] = Field(min_length=1)
    role: DataRole
    context_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self):
        if len(set(self.subject_ids)) != len(self.subject_ids) or any(
            not x.strip() for x in self.subject_ids
        ):
            raise ValueError("subject IDs must be unique and nonblank")
        return self


def validate_data_roles(records):
    # Same role can span files; biological reuse across fit/evaluation roles cannot.
    subjects, artifacts = {}, {}
    for row in records:
        for key, registry in [((row.context_id, row.dataset_sha256), artifacts)]:
            previous = registry.setdefault(key, row.role)
            if previous != row.role:
                raise ValueError("Dataset fingerprint has conflicting data roles")
        for subject in row.subject_ids:
            key = row.context_id, subject
            previous = subjects.setdefault(key, row.role)
            if previous != row.role:
                raise ValueError(
                    f"Biological unit {subject!r} has conflicting data roles"
                )


class UncertaintyRecord(ScientificModel):
    uncertainty_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    kind: UncertaintyKind
    status: Literal["unknown", "quantified", "sensitivity_only"] = "unknown"
    method: str | None = None
    evidence_ids: tuple[str, ...] = ()
    ensemble_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def declared_uncertainty(self):
        if (
            self.lower is not None
            and self.upper is not None
            and self.lower > self.upper
        ):
            raise ValueError("Uncertainty bounds are reversed")
        if self.status != "unknown" and (not self.method or not self.evidence_ids):
            raise ValueError(
                "Quantified/sensitivity uncertainty requires method and evidence"
            )
        return self


class CredibilityEvidence(ScientificModel):
    evidence_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    context_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    level: int = Field(ge=0, le=6, strict=True)
    comparator: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    population: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float
    acceptance_minimum: float | None = None
    acceptance_maximum: float | None = None
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_role: DataRole

    @field_validator("value", "acceptance_minimum", "acceptance_maximum", mode="before")
    @classmethod
    def numeric_evidence(cls, value):
        if isinstance(value, bool):
            raise ValueError("Boolean values cannot be scientific numeric evidence")
        return value

    @model_validator(mode="after")
    def prespecified(self):
        lo, hi = self.acceptance_minimum, self.acceptance_maximum
        if lo is None and hi is None:
            raise ValueError("Evidence requires prespecified acceptance bounds")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("Acceptance bounds are reversed")
        if self.level >= 4 and self.data_role not in {
            "external_validation",
            "challenge",
            "prospective",
        }:
            raise ValueError(
                "Transport/predictive evidence requires an independent data role"
            )
        if self.level == 6 and self.data_role != "prospective":
            raise ValueError("Decision validation requires prospective evidence")
        return self

    @property
    def passed(self):
        return (
            self.acceptance_minimum is None or self.value >= self.acceptance_minimum
        ) and (self.acceptance_maximum is None or self.value <= self.acceptance_maximum)


class CredibilityEnvelope(ScientificModel):
    context: ContextOfUse | None = None
    evidence: tuple[CredibilityEvidence, ...] = ()
    data_roles: tuple[DataRoleRecord, ...] = ()
    uncertainties: tuple[UncertaintyRecord, ...] = ()

    @model_validator(mode="after")
    def integrity(self):
        validate_data_roles(self.data_roles)
        if len({x.evidence_id for x in self.evidence}) != len(self.evidence):
            raise ValueError("Duplicate credibility evidence ID")
        if len({x.uncertainty_id for x in self.uncertainties}) != len(
            self.uncertainties
        ):
            raise ValueError("Duplicate uncertainty ID")
        for row in (*self.evidence, *self.data_roles):
            if self.context is None or row.context_id != self.context.context_id:
                raise ValueError("Evidence/data role must match the declared context")
        return self


def require_scientific_claim(envelope, *, artifact_id, endpoint, claim):
    """Require evidence cumulatively and for the exact result, endpoint and context.

    This checks submitted evidence contracts. Hashes identify evidence; they do
    not authenticate its author or establish its scientific truth.
    """
    required = {
        "software": 0,
        "numerical": 2,
        "empirical": 3,
        "transport": 4,
        "predictive": 5,
        "clinical_decision": 6,
    }
    if claim not in required:
        raise ValueError("Unknown scientific claim tier")
    envelope = CredibilityEnvelope.model_validate(envelope.model_dump(mode="python"))
    if envelope.context is None or endpoint not in envelope.context.endpoints:
        raise ValueError("Scientific claims require a matching context and endpoint")
    if required[claim] >= 5 and envelope.context.model_risk == "unknown":
        raise ValueError(
            "Predictive/decision claims require an explicit model-risk assessment"
        )
    rows = [
        x
        for x in envelope.evidence
        if x.artifact_id == artifact_id
        and x.endpoint == endpoint
        and x.population == envelope.context.population
        and x.passed
    ]
    achieved = {x.level for x in rows}
    missing = set(range(required[claim] + 1)) - achieved
    if missing:
        raise ValueError(
            f"Claim {claim!r} lacks passing evidence levels {sorted(missing)}"
        )
    if required[claim] >= 3:
        roles = {(x.dataset_id, x.role) for x in envelope.data_roles}
        if any((x.dataset_id, x.data_role) not in roles for x in rows if x.level >= 3):
            raise ValueError(
                "Validation evidence must link to declared data-role lineage"
            )
        budgets = [x for x in envelope.uncertainties if x.artifact_id == artifact_id]
        kinds = {x.kind for x in budgets}
        if (
            not set(envelope.context.required_uncertainty_kinds).issubset(kinds)
            or not budgets
            or any(x.status == "unknown" for x in budgets)
        ):
            raise ValueError(
                "Empirical or stronger claims require a declared uncertainty budget"
            )
    return True


class ScientificClaim(ScientificModel):
    artifact_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    claim: Literal[
        "software",
        "numerical",
        "empirical",
        "transport",
        "predictive",
        "clinical_decision",
    ]


def validate_result_claims(envelope, claims, data):
    """Check every declared claim and prohibit patient-outcome tier jumps.

    This is contract admission, not scientific authentication of the supplied evidence.
    """
    declared = data.get("scientific_claims", [])
    if not isinstance(declared, (list, tuple)):
        raise ValueError("scientific_claims must be an array")
    parsed = tuple(ScientificClaim.model_validate(x) for x in (*claims, *declared))
    for claim in parsed:
        require_scientific_claim(envelope, **claim.model_dump())
    outcomes = data.get("outcomes", [])
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if (
                not isinstance(outcome, dict)
                or outcome.get("endpoint_scope") != "patient_outcome"
            ):
                continue
            artifact = outcome.get("artifact_ref")
            artifact_id = (
                artifact.get("artifact_id") if isinstance(artifact, dict) else None
            )
            if not artifact_id or not any(
                claim.artifact_id == artifact_id
                and claim.endpoint == outcome.get("endpoint")
                and claim.claim == "clinical_decision"
                for claim in parsed
            ):
                raise ValueError(
                    "Patient outcomes require an artifact-bound clinical_decision evidence claim"
                )
    return tuple(dict.fromkeys(parsed))


def merge_credibility(first, second):
    if (
        first.context is not None
        and second.context is not None
        and first.context != second.context
    ):
        raise ValueError("Cannot merge results with conflicting scientific contexts")

    def unique(rows, key):
        mapping = {}
        for row in rows:
            identity = key(row)
            if identity in mapping and mapping[identity] != row:
                raise ValueError("Conflicting scientific evidence or lineage identity")
            mapping[identity] = row
        return tuple(mapping.values())

    return CredibilityEnvelope(
        context=first.context or second.context,
        evidence=unique((*first.evidence, *second.evidence), lambda x: x.evidence_id),
        uncertainties=unique(
            (*first.uncertainties, *second.uncertainties), lambda x: x.uncertainty_id
        ),
        data_roles=unique(
            (*first.data_roles, *second.data_roles),
            lambda x: (
                x.context_id,
                x.dataset_id,
                x.dataset_sha256,
                x.subject_ids,
                x.role,
            ),
        ),
    )
