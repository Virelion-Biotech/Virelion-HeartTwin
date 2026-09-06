"""Versioned contracts shared by the HeartTwin orchestrator."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0.0"

class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_service: str
    source_repository: str | None = None
    source_version: str | None = None
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parent_run_ids: list[str] = Field(default_factory=list)
    content_sha256: str | None = None

class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    modality: Literal["molecular", "electrical", "mechanical", "imaging", "safety", "structural", "clinical", "other"]
    values: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    status: Literal["observed", "inferred", "simulated"] = "observed"
    uncertainty: dict[str, Any] = Field(default_factory=dict)

class CardiacState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = CONTRACT_VERSION
    entity_id: str
    biological_context: dict[str, Any] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    inferred_state: dict[str, Any] = Field(default_factory=dict)
    simulations: list[dict[str, Any]] = Field(default_factory=list)
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)

class ServiceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    capability: str
    status: Literal["ok", "unavailable", "error", "skipped"]
    data: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    provenance: Provenance | None = None

class TwinRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    entity_id: str
    requested_capabilities: list[str]
    results: list[ServiceResult] = Field(default_factory=list)
    state: CardiacState
