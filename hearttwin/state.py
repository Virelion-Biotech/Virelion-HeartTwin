"""Canonical CardiacState reducer, validation, and fingerprinting utilities."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import (
    AgentChallengePayload,
    AnatomyBundlePayload,
    AtlasContextPayload,
    BenchmarkResolutionPayload,
    BridgePublicationPayload,
    CardiacState,
    EvaluationResultPayload,
    FlowArtifact,
    FlowResultPayload,
    LearningResultPayload,
    InferenceResultPayload,
    ModalityAnalysisPayload,
    Observation,
    PredictionArtifact,
    PosteriorArtifact,
    Provenance,
    ServiceResult,
    SimulationArtifact,
    SimulationResultPayload,
    StatePhase,
    StateTransition,
    StateValue,
    TherapyArtifact,
    TherapyResultPayload,
    ValidationArtifact,
    ValidationGateArtifact,
    VexObservationPayload,
)
from .provenance import sha256


class CardiacStateValidationError(ValueError):
    """Raised when the canonical state contains an invalid cross-reference."""


_CAPABILITY_PREFIX_TO_DOMAIN = {
    "electrical.": "electrical",
    "mechanical.": "mechanical",
    "imaging.": "imaging",
    "safety.": "safety",
}


class CardiacStateStore:
    """Mutable reducer around one canonical :class:`CardiacState`.

    Every typed artifact receives an ID derived from stable content and keeps
    links to the HeartTwin provenance run(s) that produced it. External
    dataset/benchmark provenance namespaces remain embedded in their own
    contracts and are not conflated with HeartTwin run IDs.
    """

    def __init__(self, state: CardiacState):
        self.state = state

    @classmethod
    def new(
        cls,
        entity_id: str,
        observations: list[Observation] | None = None,
        biological_context: Mapping[str, Any] | None = None,
    ) -> "CardiacStateStore":
        observations = list(observations or [])
        state = CardiacState(
            entity_id=entity_id,
            biological_context=dict(biological_context or {}),
            state_phase="baseline" if observations else "unknown",
            observations=observations,
            provenance=[item.provenance for item in observations],
        )
        return cls(state)

    @classmethod
    def from_snapshot(cls, state: CardiacState, *, verify: bool = True) -> "CardiacStateStore":
        """Create a store from an existing snapshot, optionally verifying its fingerprint."""
        store = cls(state.model_copy(deep=True))
        if verify:
            store.verify_fingerprint()
        return store

    def _add_provenance(self, provenance: Provenance | None) -> list[str]:
        if provenance is None:
            return []
        existing = {item.run_id for item in self.state.provenance}
        if provenance.run_id not in existing:
            self.state.provenance.append(provenance)
        return [provenance.run_id]

    def add_observation(self, observation: Observation) -> None:
        if observation.observation_id in {item.observation_id for item in self.state.observations}:
            raise CardiacStateValidationError(
                f"Duplicate observation_id: {observation.observation_id}"
            )
        self._add_provenance(observation.provenance)
        self.state.observations.append(observation)
        if self.state.state_phase == "unknown":
            self.state.state_phase = "baseline"

    def record_atlas(self, payload: AtlasContextPayload, provenance: Provenance | None = None) -> None:
        self.state.atlas_context = payload
        self._add_provenance(provenance)

    def record_anatomy(
        self, payload: AnatomyBundlePayload, provenance: Provenance | None = None
    ) -> None:
        fingerprint = payload.bundle_fingerprint or sha256(payload.model_dump(mode="json"))
        existing = {
            item.bundle_fingerprint or sha256(item.model_dump(mode="json"))
            for item in self.state.anatomy_bundles
        }
        if fingerprint not in existing:
            self.state.anatomy_bundles.append(payload)
        self._add_provenance(provenance)
        self._record_derived(
            domain="structural",
            variable="anatomy.bundle_fingerprint",
            value=fingerprint,
            status="inferred",
            method="CardiAnatomy",
            provenance=provenance,
            value_id=f"value-{fingerprint[:16]}",
        )

    def record_benchmark(
        self, payload: BenchmarkResolutionPayload, provenance: Provenance | None = None
    ) -> None:
        if not any(
            existing.benchmark_id == payload.benchmark_id
            and existing.version == payload.version
            and existing.metadata_sha256 == payload.metadata_sha256
            for existing in self.state.benchmarks
        ):
            self.state.benchmarks.append(payload)
        self._add_provenance(provenance)

    def record_modality(
        self, payload: ModalityAnalysisPayload, provenance: Provenance | None = None
    ) -> None:
        self._append_unique(
            self.state.modality_analyses, payload, "observation_id", payload.observation_id
        )
        self._add_provenance(provenance)
        domain = _CAPABILITY_PREFIX_TO_DOMAIN.get(payload.capability.split(".", 1)[0] + ".")
        if domain:
            self._record_derived(
                domain=domain,
                variable=f"{payload.capability}.status",
                value="ok",
                status="inferred",
                method=payload.service,
                provenance=provenance,
            )

    def record_learning(
        self,
        payload: LearningResultPayload,
        provenance: Provenance | None = None,
        capability: str = "learn.infer",
    ) -> PredictionArtifact:
        artifact_id = f"pred-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = PredictionArtifact(
            prediction_id=artifact_id,
            model_id=payload.model_id,
            task=payload.task,
            target_column=payload.target_column,
            feature_columns=payload.feature_columns,
            predictions=payload.predictions,
            metrics=payload.metrics,
            dataset_fingerprint=payload.dataset_fingerprint,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(
            self.state.prediction_artifacts, artifact, "prediction_id", artifact_id
        )
        self.state.predictions = [
            {"capability": capability, "data": payload.model_dump(mode="json"), "status": "inferred"}
        ]
        return artifact

    def record_flow(
        self, payload: FlowResultPayload, provenance: Provenance | None = None
    ) -> FlowArtifact:
        artifact_id = f"flow-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = FlowArtifact(
            flow_id=artifact_id,
            subject_id=payload.subject_id,
            backend=payload.backend,
            scalar_outputs=payload.scalar_outputs,
            series_outputs=payload.series_outputs,
            qc=payload.qc,
            validation_status=payload.validation_status,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(self.state.flow_artifacts, artifact, "flow_id", artifact_id)
        return artifact

    def record_therapy(
        self, payload: TherapyResultPayload, provenance: Provenance | None = None
    ) -> TherapyArtifact:
        artifact_id = f"therapy-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = TherapyArtifact(
            therapy_id=artifact_id,
            subject_id=payload.subject_id,
            backend=payload.backend,
            plan_id=payload.plan_id,
            outcomes=payload.outcomes,
            artifacts=payload.artifacts,
            validation_status=payload.validation_status,
            warnings=payload.warnings,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(
            self.state.therapy_artifacts, artifact, "therapy_id", artifact_id
        )
        return artifact

    def record_inference(
        self, payload: InferenceResultPayload, provenance: Provenance | None = None
    ) -> PosteriorArtifact:
        artifact_id = f"posterior-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = PosteriorArtifact(
            posterior_id=artifact_id,
            subject_id=payload.subject_id,
            backend=payload.backend,
            model_service=payload.model_service,
            model_capability=payload.model_capability,
            posterior=payload.posterior,
            posterior_samples=payload.posterior_samples,
            convergence=payload.convergence,
            identifiability=payload.identifiability,
            sensitivity=payload.sensitivity,
            diagnostics=payload.diagnostics,
            validation_status=payload.validation_status,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(
            self.state.posterior_artifacts, artifact, "posterior_id", artifact_id
        )
        return artifact

    def record_simulation(
        self, payload: SimulationResultPayload, provenance: Provenance | None = None
    ) -> SimulationArtifact:
        artifact_id = f"sim-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = SimulationArtifact(
            simulation_id=artifact_id,
            service="CardiSim",
            backend=payload.backend,
            summary=payload.summary,
            events=payload.events,
            population_size=payload.population_size,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(
            self.state.simulation_artifacts, artifact, "simulation_id", artifact_id
        )
        self.state.simulations = [
            {"capability": "simulation.run", "data": payload.model_dump(mode="json"), "status": "simulated"}
        ]
        return artifact

    def record_agent(
        self, payload: AgentChallengePayload, provenance: Provenance | None = None
    ) -> None:
        fingerprint = sha256(payload.model_dump(mode="json"))
        existing_ids = {sha256(item.model_dump(mode="json")) for item in self.state.challenges}
        if fingerprint not in existing_ids:
            self.state.challenges.append(payload)
        self._add_provenance(provenance)
        self._record_derived(
            domain="inference",
            variable="challenge.count",
            value=len(payload.challenges),
            status="inferred",
            method="CardiAgent",
            provenance=provenance,
            value_id=f"value-{fingerprint[:16]}",
        )

    def record_vex(
        self, payload: VexObservationPayload, provenance: Provenance | None = None
    ) -> None:
        fingerprint = sha256(payload.model_dump(mode="json"))
        existing_ids = {sha256(item.model_dump(mode="json")) for item in self.state.vex_observations}
        if fingerprint not in existing_ids:
            self.state.vex_observations.append(payload)
        self._add_provenance(provenance)
        self._record_derived(
            domain="inference",
            variable="vex.evidence_tier",
            value=payload.evidence_tier or "unknown",
            status="inferred",
            method="CardiVex",
            provenance=provenance,
            value_id=f"value-{fingerprint[:16]}",
        )

    def record_bridge(
        self, payload: BridgePublicationPayload, provenance: Provenance | None = None
    ) -> None:
        self._append_unique(
            self.state.bridge_publications, payload, "message_id", payload.message_id
        )
        self._add_provenance(provenance)

    def record_evaluation(
        self, payload: EvaluationResultPayload, provenance: Provenance | None = None
    ) -> ValidationArtifact:
        validation_id = f"eval-{sha256(payload.model_dump(mode='json'))[:16]}"
        artifact = ValidationArtifact(
            validation_id=validation_id,
            benchmark_id=payload.benchmark_id,
            benchmark_version=payload.benchmark_version,
            task_id=payload.task_id,
            model_id=payload.model_id,
            primary_metric=payload.primary_metric,
            primary_value=payload.primary_value,
            metrics=payload.metrics,
            warnings=payload.warnings,
            errors=payload.errors,
            evaluation_fingerprint=payload.evaluation_fingerprint,
            provenance_ids=self._add_provenance(provenance),
        )
        self._append_unique(
            self.state.evaluation_artifacts, artifact, "validation_id", validation_id
        )
        self.state.validation = {"evaluation": payload.model_dump(mode="json")}
        return artifact

    def record_validation_gate(
        self,
        artifact: ValidationGateArtifact,
        provenance: Provenance | None = None,
    ) -> None:
        provenance_ids = self._add_provenance(provenance)
        artifact = artifact.model_copy(
            update={"provenance_ids": sorted(set(artifact.provenance_ids) | set(provenance_ids))}
        )
        self._append_unique(self.state.validation_gates, artifact, "gate_id", artifact.gate_id)

    def record_trace(self, data: Mapping[str, Any], provenance: Provenance | None = None) -> None:
        self.state.trace_records.append(dict(data))
        self.state.validation["trace"] = dict(data)
        self._add_provenance(provenance)

    def transition(
        self,
        to_phase: StatePhase,
        *,
        trigger: str,
        provenance: Provenance | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> StateTransition:
        from_phase = self.state.state_phase
        if to_phase == from_phase:
            raise CardiacStateValidationError(f"No-op state transition is not allowed: {to_phase}")
        transition_id = f"transition-{sha256({'entity_id': self.state.entity_id, 'from': from_phase, 'to': to_phase, 'trigger': trigger, 'n': len(self.state.transitions)})[:16]}"
        transition = StateTransition(
            transition_id=transition_id,
            sequence=len(self.state.transitions),
            from_phase=from_phase,
            to_phase=to_phase,
            trigger=trigger,
            timestamp=datetime.now(timezone.utc),
            provenance_ids=self._add_provenance(provenance),
            details=dict(details or {}),
        )
        self.state.transitions.append(transition)
        self.state.state_phase = to_phase
        return transition

    def reduce_service_result(self, result: ServiceResult) -> None:
        """Reduce a successful typed service result into canonical state.

        Unknown or legacy service outputs remain in ``ServiceResult.data`` rather
        than being guessed into a cardiac-state field.
        """
        if result.provenance is not None:
            self._add_provenance(result.provenance)
        if result.status != "ok":
            return

        data = result.data
        try:
            if result.capability == "anatomy.build":
                self.record_anatomy(AnatomyBundlePayload.model_validate(data), result.provenance)
            elif result.capability == "atlas.context":
                self.record_atlas(AtlasContextPayload.model_validate(data), result.provenance)
            elif result.capability == "benchmark.resolve":
                self.record_benchmark(BenchmarkResolutionPayload.model_validate(data), result.provenance)
            elif result.capability in {"learn.infer", "learn.predict"}:
                self.record_learning(
                    LearningResultPayload.model_validate(data),
                    result.provenance,
                    capability=result.capability,
                )
            elif result.capability == "flow.simulate":
                self.record_flow(FlowResultPayload.model_validate(data), result.provenance)
            elif result.capability == "therapy.run":
                self.record_therapy(
                    TherapyResultPayload.model_validate(data), result.provenance
                )
            elif result.capability == "infer.run":
                self.record_inference(
                    InferenceResultPayload.model_validate(data), result.provenance
                )
            elif result.capability == "simulation.run":
                self.record_simulation(SimulationResultPayload.model_validate(data), result.provenance)
                if self.state.state_phase != "simulated":
                    self.transition("simulated", trigger=result.capability, provenance=result.provenance)
            elif result.capability == "agent.challenge":
                self.record_agent(AgentChallengePayload.model_validate(data), result.provenance)
            elif result.capability == "vex.observe":
                self.record_vex(VexObservationPayload.model_validate(data), result.provenance)
            elif result.capability == "evaluation.run":
                self.record_evaluation(EvaluationResultPayload.model_validate(data), result.provenance)
                if self.state.state_phase != "evaluated":
                    self.transition("evaluated", trigger=result.capability, provenance=result.provenance)
            elif result.capability == "bridge.publish":
                self.record_bridge(BridgePublicationPayload.model_validate(data), result.provenance)
            elif result.capability == "trace.record":
                self.record_trace(data, result.provenance)
        except Exception as exc:
            raise CardiacStateValidationError(
                f"Cannot reduce {result.capability} into CardiacState: {exc}"
            ) from exc

    def add_derived_value(
        self,
        *,
        domain: str,
        variable: str,
        value: Any,
        status: str,
        unit: str | None = None,
        anatomical_region: str | None = None,
        confidence: float | None = None,
        method: str | None = None,
        provenance: Provenance | None = None,
        uncertainty: Any = None,
        value_id: str | None = None,
    ) -> StateValue:
        return self._record_derived(
            domain=domain,
            variable=variable,
            value=value,
            status=status,
            unit=unit,
            anatomical_region=anatomical_region,
            confidence=confidence,
            method=method,
            provenance=provenance,
            uncertainty=uncertainty,
            value_id=value_id,
        )

    def validate(self) -> None:
        state = self.state
        if not state.entity_id:
            raise CardiacStateValidationError("entity_id must not be empty")

        self._assert_unique([item.observation_id for item in state.observations], "observation_id")
        self._assert_unique([item.value_id for item in state.derived_values], "value_id")
        self._assert_unique([item.simulation_id for item in state.simulation_artifacts], "simulation_id")
        self._assert_unique([item.prediction_id for item in state.prediction_artifacts], "prediction_id")
        self._assert_unique([item.flow_id for item in state.flow_artifacts], "flow_id")
        self._assert_unique([item.therapy_id for item in state.therapy_artifacts], "therapy_id")
        self._assert_unique([item.posterior_id for item in state.posterior_artifacts], "posterior_id")
        self._assert_unique([item.validation_id for item in state.evaluation_artifacts], "validation_id")
        self._assert_unique([item.gate_id for item in state.validation_gates], "gate_id")
        self._assert_unique([item.message_id for item in state.bridge_publications], "message_id")
        self._assert_unique([item.transition_id for item in state.transitions], "transition_id")

        provenance_ids = {item.run_id for item in state.provenance}
        all_links: list[str] = []
        for item in state.derived_values:
            all_links.extend(item.provenance_ids)
        for collection in (
            state.simulation_artifacts,
            state.prediction_artifacts,
            state.flow_artifacts,
            state.therapy_artifacts,
            state.posterior_artifacts,
            state.evaluation_artifacts,
            state.validation_gates,
            state.transitions,
        ):
            for item in collection:
                all_links.extend(item.provenance_ids)
        for item in all_links:
            if item not in provenance_ids:
                raise CardiacStateValidationError(f"Dangling HeartTwin provenance link: {item}")

        expected_phase: StatePhase = "unknown"
        if state.observations:
            expected_phase = "baseline"
        for transition in sorted(state.transitions, key=lambda item: item.sequence):
            if transition.from_phase != expected_phase:
                raise CardiacStateValidationError(
                    f"Broken phase transition chain at {transition.transition_id}: "
                    f"expected from_phase={expected_phase}, got {transition.from_phase}"
                )
            expected_phase = transition.to_phase
        if state.transitions and state.state_phase != expected_phase:
            raise CardiacStateValidationError(
                f"state_phase={state.state_phase} does not match final transition phase={expected_phase}"
            )
        if not state.transitions and state.state_phase not in {expected_phase, "simulated", "evaluated", "validated"}:
            raise CardiacStateValidationError(
                f"state_phase={state.state_phase} is inconsistent with current observations"
            )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return self.state.model_dump(mode="json", exclude={"state_fingerprint"})

    def snapshot(self) -> CardiacState:
        self.validate()
        snapshot = self.state.model_copy(deep=True)
        snapshot.state_fingerprint = sha256(snapshot.model_dump(mode="json", exclude={"state_fingerprint"}))
        return snapshot

    def verify_fingerprint(self) -> str:
        """Verify the embedded fingerprint and return it."""
        self.validate()
        if not self.state.state_fingerprint:
            raise CardiacStateValidationError("State has no embedded state_fingerprint")
        expected = sha256(self._fingerprint_payload())
        if expected != self.state.state_fingerprint:
            raise CardiacStateValidationError(
                "CardiacState fingerprint mismatch: snapshot may have been modified after creation"
            )
        return expected

    def fingerprint(self) -> str:
        return self.snapshot().state_fingerprint or ""

    def _record_derived(
        self,
        *,
        domain: str,
        variable: str,
        value: Any,
        status: str,
        unit: str | None = None,
        anatomical_region: str | None = None,
        confidence: float | None = None,
        method: str | None = None,
        provenance: Provenance | None = None,
        uncertainty: Any = None,
        value_id: str | None = None,
    ) -> StateValue:
        provenance_ids = self._add_provenance(provenance)
        payload = {
            "entity_id": self.state.entity_id,
            "domain": domain,
            "variable": variable,
            "value": value,
            "unit": unit,
            "anatomical_region": anatomical_region,
            "status": status,
            "method": method,
        }
        value_id = value_id or f"value-{sha256(payload)[:16]}"
        if value_id in {item.value_id for item in self.state.derived_values}:
            return next(item for item in self.state.derived_values if item.value_id == value_id)
        item = StateValue(
            value_id=value_id,
            domain=domain,
            variable=variable,
            value=value,
            unit=unit,
            anatomical_region=anatomical_region,
            status=status,
            confidence=confidence,
            uncertainty=uncertainty,
            method=method,
            provenance_ids=provenance_ids,
        )
        self.state.derived_values.append(item)
        return item

    @staticmethod
    def _assert_unique(values: list[str], label: str) -> None:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        if duplicates:
            raise CardiacStateValidationError(f"Duplicate {label}: {sorted(duplicates)}")

    @staticmethod
    def _append_unique(collection: list[Any], item: Any, field: str, value: str) -> None:
        if any(getattr(existing, field) == value for existing in collection):
            return
        collection.append(item)


def state_from_service_results(
    entity_id: str,
    observations: list[Observation],
    results: list[ServiceResult],
    biological_context: Mapping[str, Any] | None = None,
) -> CardiacState:
    """Build a canonical snapshot from an existing low-level HeartTwin run."""
    store = CardiacStateStore.new(
        entity_id,
        observations=observations,
        biological_context=biological_context,
    )
    for result in results:
        store.reduce_service_result(result)
    return store.snapshot()
