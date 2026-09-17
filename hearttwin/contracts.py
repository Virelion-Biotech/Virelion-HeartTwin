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
    modality: Literal[
        "molecular", "electrical", "mechanical", "imaging", "safety", "structural",
        "clinical", "other"
    ]
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


class AtlasContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    context_id: str
    record_ids: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    provenance: list[str] = Field(default_factory=list)


class BenchmarkSamplePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    group_id: str
    study_id: str
    label: str
    technical_group: str | None = None
    organism: str | None = None
    timepoint: str | None = None
    cell_context: str | None = None
    region: str | None = None


class BenchmarkResolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    benchmark_id: str
    version: str
    policy: str
    seed: int
    assignments: dict[str, str]
    sample_count: int
    group_count: int
    metadata_sha256: str
    samples: list[BenchmarkSamplePayload]


class LearningPredictionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    y_true: int | float | str
    y_pred: int | float | str
    score: float | None = None
    subgroup: str | None = None


class LearningResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    model_id: str
    task: str
    target_column: str
    metrics: dict[str, dict[str, float]]
    predictions: list[LearningPredictionPayload]
    dataset_fingerprint: str | None = None


class SimulationResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    backend: str
    summary: dict[str, Any]
    events: list[str] = Field(default_factory=list)
    population_size: int


class EvaluationResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    benchmark_id: str
    benchmark_version: str
    task_id: str
    model_id: str
    primary_metric: str | None = None
    primary_value: float | None = None
    metrics: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    evaluation_fingerprint: str | None = None


class AgentChallengePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    entity_id: str | None = None
    challenges: list[dict[str, Any]]


class BridgePublicationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    message_type: str
    message_id: str
    status: str
    transport: str
    content_sha256: str | None = None


class WorkflowState(BaseModel):
    """Typed state exchanged between explicit HeartTwin workflow stages."""

    model_config = ConfigDict(extra="forbid")
    contract_version: str = CONTRACT_VERSION
    entity_id: str
    observations: list[Observation] = Field(default_factory=list)
    atlas: AtlasContextPayload | None = None
    benchmark: BenchmarkResolutionPayload | None = None
    learning: LearningResultPayload | None = None
    simulation: SimulationResultPayload | None = None
    agent: AgentChallengePayload | None = None
    evaluation: EvaluationResultPayload | None = None
    bridge: BridgePublicationPayload | None = None
    trace: dict[str, Any] | None = None
    provenance: list[Provenance] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    entity_id: str
    status: Literal["ok", "error"]
    state: WorkflowState
    steps: list[ServiceResult] = Field(default_factory=list)


class TwinRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    entity_id: str
    requested_capabilities: list[str]
    results: list[ServiceResult] = Field(default_factory=list)
    state: CardiacState
