from __future__ import annotations

from typing import Any

from .contracts import Observation
from .orchestrator import HeartTwin
from .service_registry import ServiceRegistry
from .workflow import run_multimodal_workflow


class VirelionServices:
    """Convenience facade exposing registered Virelion capabilities."""

    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
        self.twin = HeartTwin(registry)

    def _invoke(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self.registry.capability(capability)
        if adapter is None:
            raise RuntimeError(f"Capability unavailable: {capability}")
        return adapter.invoke(capability, payload)

    def call(self, capability: str, entity_id: str, **payload: Any) -> dict[str, Any]:
        return self._invoke(capability, {"entity_id": entity_id, **payload})

    def infer(self, subject_id: str, **payload: Any) -> dict[str, Any]:
        """Run a CardiInfer request using the subject-oriented inference contract."""
        return self._invoke("infer.run", {"subject_id": subject_id, **payload})

    def propagate_inference(self, subject_id: str, **payload: Any) -> dict[str, Any]:
        """Propagate a CardiInfer posterior without injecting an entity_id field."""
        return self._invoke("infer.propagate", {"subject_id": subject_id, **payload})

    def inference_backends(self) -> dict[str, Any]:
        return self._invoke("infer.backends", {})

    def inference_ecosystem(self) -> dict[str, Any]:
        return self._invoke("infer.ecosystem", {})

    def run_twin(self, entity_id: str, **kwargs: Any):
        return self.twin.run(entity_id, **kwargs)

    def run_multimodal(self, entity_id: str, observations: list[Observation], **kwargs: Any):
        return run_multimodal_workflow(
            self.registry,
            entity_id=entity_id,
            observations=observations,
            **kwargs,
        )

    def atlas(self, entity_id: str, **p: Any): return self.call("atlas.context", entity_id, **p)
    def design(self, entity_id: str, **p: Any): return self.call("design.generate", entity_id, **p)
    def population(self, entity_id: str, **p: Any): return self.call("population.generate", entity_id, **p)
    def validate_design(self, entity_id: str, **p: Any): return self.call("design.validate", entity_id, **p)
    def power_plan(self, entity_id: str, **p: Any): return self.call("power.plan", entity_id, **p)
    def challenge_validate(self, entity_id: str, **p: Any): return self.call("challenge.validate", entity_id, **p)
    def challenge_assess(self, entity_id: str, **p: Any): return self.call("challenge.assess", entity_id, **p)
    def materialize_challenge(self, entity_id: str, **p: Any): return self.call("challenge.materialize", entity_id, **p)
    def recovery(self, entity_id: str, **p: Any): return self.call("recovery.score", entity_id, **p)
    def host_map(self, entity_id: str, **p: Any): return self.call("host.map", entity_id, **p)
    def electrical(self, entity_id: str, **p: Any): return self.call("electrical.analyze", entity_id, **p)
    def mechanical(self, entity_id: str, **p: Any): return self.call("mechanical.analyze", entity_id, **p)
    def imaging(self, entity_id: str, **p: Any): return self.call("imaging.qc", entity_id, **p)
    def safety(self, entity_id: str, **p: Any): return self.call("safety.score", entity_id, **p)
    def learn(self, entity_id: str, **p: Any): return self.call("learn.infer", entity_id, **p)
    def simulate(self, entity_id: str, **p: Any): return self.call("simulation.run", entity_id, **p)
    def simulate_cardiac_twin(self, entity_id: str, **p: Any): return self.call("simulation.cardiac_twin", entity_id, **p)
    def evaluate(self, entity_id: str, **p: Any): return self.call("evaluation.run", entity_id, **p)
    def benchmark(self, entity_id: str, **p: Any): return self.call("benchmark.resolve", entity_id, **p)
    def benchmark_search(self, query: str, **p: Any): return self._invoke("benchmark.search", {"query": query, **p})
    def benchmark_catalog(self, **p: Any): return self._invoke("benchmark.catalog", p)
    def benchmark_discover(self, **p: Any): return self._invoke("benchmark.discover", p)
    def benchmark_record_result(self, entity_id: str, **p: Any): return self.call("benchmark.result.record", entity_id, **p)
    def benchmark_results(self, **p: Any): return self._invoke("benchmark.results", p)
