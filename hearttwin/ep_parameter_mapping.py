"""Explicit parameter semantics for replay between heterogeneous EP backends.

A matching parameter name is not evidence that two solvers use the same
physiological quantity, units, convention, or numerical meaning. This module
therefore makes cross-backend replay an explicit, versioned transformation.
Nothing is auto-renamed, auto-converted, or silently dropped.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .provenance import sha256


class EPParameterMapRule(BaseModel):
    """One declared affine mapping from a source parameter to a target parameter."""

    model_config = ConfigDict(extra="forbid")
    source_parameter: str
    target_parameter: str
    source_unit: str
    target_unit: str
    scale: float
    offset: float = 0.0
    rationale: str = Field(min_length=8)

    @model_validator(mode="after")
    def validate_rule(self) -> EPParameterMapRule:
        if not self.source_parameter.strip() or not self.target_parameter.strip():
            raise ValueError("parameter names must be non-empty")
        if not self.source_unit.strip() or not self.target_unit.strip():
            raise ValueError("source_unit and target_unit must be explicit; use '1' if dimensionless")
        if not math.isfinite(self.scale) or self.scale == 0:
            raise ValueError("mapping scale must be finite and non-zero")
        if not math.isfinite(self.offset):
            raise ValueError("mapping offset must be finite")
        return self


class IgnoredEPParameter(BaseModel):
    """A source parameter intentionally not propagated to the target backend."""

    model_config = ConfigDict(extra="forbid")
    source_parameter: str
    reason: str = Field(min_length=8)

    @model_validator(mode="after")
    def validate_ignored(self) -> IgnoredEPParameter:
        if not self.source_parameter.strip():
            raise ValueError("ignored source_parameter must be non-empty")
        return self


class EPParameterMappingSpec(BaseModel):
    """Versioned cross-backend mapping contract."""

    model_config = ConfigDict(extra="forbid")
    mapping_id: str
    mapping_version: str
    source_backend: str
    target_backend: str
    rules: list[EPParameterMapRule]
    ignored_source_parameters: list[IgnoredEPParameter] = Field(default_factory=list)
    intended_use: str = Field(min_length=8)
    evidence_reference: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mapping(self) -> EPParameterMappingSpec:
        if not self.mapping_id.strip() or not self.mapping_version.strip():
            raise ValueError("mapping_id and mapping_version must be non-empty")
        if not self.source_backend.strip() or not self.target_backend.strip():
            raise ValueError("source_backend and target_backend must be non-empty")
        if self.source_backend == self.target_backend:
            raise ValueError("cross-backend mapping requires different source and target backends")
        if not self.rules and not self.ignored_source_parameters:
            raise ValueError(
                "mapping must declare at least one mapped or explicitly ignored source parameter"
            )

        sources = [rule.source_parameter for rule in self.rules]
        targets = [rule.target_parameter for rule in self.rules]
        ignored = [item.source_parameter for item in self.ignored_source_parameters]
        if len(sources) != len(set(sources)):
            raise ValueError("each source parameter may be mapped at most once")
        if len(targets) != len(set(targets)):
            raise ValueError("each target parameter may be produced at most once")
        if len(ignored) != len(set(ignored)):
            raise ValueError("ignored source parameters must be unique")
        overlap = sorted(set(sources) & set(ignored))
        if overlap:
            raise ValueError(
                "source parameters cannot be both mapped and ignored: " + ", ".join(overlap)
            )
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(self.model_dump(mode="json"))


class EPParameterMappingResult(BaseModel):
    """Auditable output of applying one mapping contract."""

    model_config = ConfigDict(extra="forbid")
    mapping_id: str
    mapping_version: str
    mapping_fingerprint: str
    source_backend: str
    target_backend: str
    values: dict[str, float]
    units: dict[str, str]
    mapped_source_parameters: list[str]
    ignored_source_parameters: list[str]
    source_parameter_fingerprint: str


def apply_ep_parameter_mapping(
    spec: EPParameterMappingSpec,
    *,
    source_backend: str,
    target_backend: str,
    values: dict[str, float],
    units: dict[str, str],
) -> EPParameterMappingResult:
    """Apply a fully explicit parameter mapping and reject incomplete coverage."""
    if source_backend != spec.source_backend:
        raise ValueError(
            f"mapping expects source backend {spec.source_backend!r}, got {source_backend!r}"
        )
    if target_backend != spec.target_backend:
        raise ValueError(
            f"mapping expects target backend {spec.target_backend!r}, got {target_backend!r}"
        )
    if not values:
        raise ValueError("source parameter set is empty")

    numeric_values: dict[str, float] = {}
    for name, raw in values.items():
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"source parameter {name!r} is not numeric") from exc
        if not math.isfinite(value):
            raise ValueError(f"source parameter {name!r} is non-finite")
        numeric_values[str(name)] = value

    mapped_sources = {rule.source_parameter for rule in spec.rules}
    ignored_sources = {item.source_parameter for item in spec.ignored_source_parameters}
    incoming = set(numeric_values)
    uncovered = sorted(incoming - mapped_sources - ignored_sources)
    if uncovered:
        raise ValueError(
            "mapping does not account for source parameters: " + ", ".join(uncovered)
        )
    missing_required = sorted(mapped_sources - incoming)
    if missing_required:
        raise ValueError(
            "mapping requires source parameters that are absent: " + ", ".join(missing_required)
        )

    target_values: dict[str, float] = {}
    target_units: dict[str, str] = {}
    for rule in spec.rules:
        actual_unit = units.get(rule.source_parameter)
        if actual_unit is None:
            raise ValueError(
                f"source parameter {rule.source_parameter!r} has no declared unit; "
                f"mapping requires {rule.source_unit!r}"
            )
        if actual_unit != rule.source_unit:
            raise ValueError(
                f"source parameter {rule.source_parameter!r} unit {actual_unit!r} does not "
                f"match mapping unit {rule.source_unit!r}"
            )
        mapped = numeric_values[rule.source_parameter] * rule.scale + rule.offset
        if not math.isfinite(mapped):
            raise ValueError(
                f"mapped target parameter {rule.target_parameter!r} is non-finite"
            )
        target_values[rule.target_parameter] = mapped
        target_units[rule.target_parameter] = rule.target_unit

    ignored_present = sorted(incoming & ignored_sources)
    source_parameter_fingerprint = sha256(
        {
            "backend": source_backend,
            "values": numeric_values,
            "units": {name: units.get(name) for name in sorted(numeric_values)},
        }
    )
    return EPParameterMappingResult(
        mapping_id=spec.mapping_id,
        mapping_version=spec.mapping_version,
        mapping_fingerprint=spec.fingerprint,
        source_backend=source_backend,
        target_backend=target_backend,
        values=target_values,
        units=target_units,
        mapped_source_parameters=sorted(mapped_sources),
        ignored_source_parameters=ignored_present,
        source_parameter_fingerprint=source_parameter_fingerprint,
    )
