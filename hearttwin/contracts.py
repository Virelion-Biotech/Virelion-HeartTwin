"""Versioned contracts shared by the HeartTwin orchestrator and state layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.1.0"


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
    recorded_at: datetime | None = None
    anatomical_region: str | None = None
    measurement_method: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)


class UncertaintySpec(BaseModel):
    """Structured uncertainty metadata for canonical derived state values."""

    model_config = ConfigDict(extra="forbid")
    distribution: str | None = None
    lower: float | None = None
    upper: float | None = None
    std: float | None = None
    confidence_level: float | None = Field(default=None, ge=0.0, le=1.0)
    method: str | None = None


class StateValue(BaseModel):
    """A typed, provenance-linked variable contributing to canonical cardiac state."""

    model_config = ConfigDict(extra="forbid")
    value_id: str
    domain: Literal[
        "electrical", "mechanical", "structural", "molecular", "metabolic",
        "imaging", "clinical", "safety", "simulation", "inference", "other"
    ]
    variable: str
    value: Any
    unit: str | None = None
    anatomical_region: str | None = None
    time: datetime | None = None
    status: Literal["observed", "inferred", "simulated"]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainty: UncertaintySpec | None = None
    method: str | None = None
    provenance_ids: list[str] = Field(default_factory=list)


class SimulationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simulation_id: str
    service: str
    backend: str
    summary: dict[str, Any] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    population_size: int = Field(ge=0)
    provenance_ids: list[str] = Field(default_factory=list)


class PredictionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prediction_id: str
    model_id: str
    task: str
    target_column: str
    feature_columns: list[str] = Field(default_factory=list)
    predictions: list["LearningPredictionPayload"] = Field(default_factory=list)
    metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    dataset_fingerprint: str | None = None
    provenance_ids: list[str] = Field(default_factory=list)


class ValidationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validation_id: str
    benchmark_id: str
    benchmark_version: str
    task_id: str
    model_id: str
    primary_metric: str | None = None
    primary_value: float | None = None
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    evaluation_fingerprint: str | None = None
    provenance_ids: list[str] = Field(default_factory=list)


StatePhase = Literal[
    "unknown", "baseline", "injury", "acute", "remodeling", "recovery",
    "intervention", "post_intervention", "simulated", "validated"
]


class StateTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transition_id: str
    sequence: int = Field(ge=0)
    from_phase: StatePhase
    to_phase: StatePhase
    trigger: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provenance_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


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
    feature_columns: list[str] = Field(default_factory=list)
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


class VexObservationPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    contract_version: str = "1.0"
    scenario_id: str | None = None
    evidence_tier: str | None = None
    confidence: str | None = None
    primary: dict[str, Any] = Field(default_factory=dict)


class ModalityAnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    observation_id: str
    modality: Literal["electrical", "mechanical", "imaging", "safety"]
    capability: str
    service: str
    input_path: str
    output: dict[str, Any]
    content_sha256: str


class BridgePublicationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1.0"
    message_type: str
    message_id: str
    status: str
    transport: str
    content_sha256: str | None = None
    consumer_result: dict[str, Any] | None = None


class CardiacState(BaseModel):
    """Canonical typed state shared by all HeartTwin workflow stages.

    The older ``inferred_state``, ``simulations``, ``predictions`` and ``validation``
    dictionaries remain as compatibility mirrors. New code should use the typed
    artifact collections below them.
    """

    model_config = ConfigDict(extra="forbid")
    contract_version: str = CONTRACT_VERSION
    entity_id: str
    biological_context: dict[str, Any] = Field(default_factory=dict)
    state_phase: StatePhase = "unknown"
    observations: list[Observation] = Field(default_factory=list)
    atlas_context: AtlasContextPayload | None = None
    benchmarks: list[BenchmarkResolutionPayload] = Field(default_factory=list)
    modality_analyses: list[ModalityAnalysisPayload] = Field(default_factory=list)
    derived_values: list[StateValue] = Field(default_factory=list)
    simulation_artifacts: list[SimulationArtifact] = Field(default_factory=list)
    prediction_artifacts: list[PredictionArtifact] = Field(default_factory=list)
    evaluation_artifacts: list[ValidationArtifact] = Field(default_factory=list)
    challenges: list[AgentChallengePayload] = Field(default_factory=list)
    vex_observations: list[VexObservationPayload] = Field(default_factory=list)
    bridge_publications: list[BridgePublicationPayload] = Field(default_factory=list)
    transitions: list[StateTransition] = Field(default_factory=list)
    trace_records: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    # Backward-compatible mirrors retained for existing consumers.
    inferred_state: dict[str, Any] = Field(default_factory=dict)
    simulations: list[dict[str, Any]] = Field(default_factory=list)
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class ServiceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    capability: str
    status: Literal["ok", "unavailable", "error", "skipped"]
    data: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    provenance: Provenance | None = None


class WorkflowState(BaseModel):
    """Execution view of the canonical CardiacState."""

    model_config = ConfigDict(extra="forbid")
    contract_version: str = CONTRACT_VERSION
    entity_id: str
    observations: list[Observation] = Field(default_factory=list)
    cardiac_state: CardiacState | None = None
    modality_analyses: list[ModalityAnalysisPayload] = Field(default_factory=list)
    atlas: AtlasContextPayload | None = None
    benchmark: BenchmarkResolutionPayload | None = None
    learning: LearningResultPayload | None = None
    simulation: SimulationResultPayload | None = None
    agent: AgentChallengePayload | None = None
    vex: VexObservationPayload | None = None
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
