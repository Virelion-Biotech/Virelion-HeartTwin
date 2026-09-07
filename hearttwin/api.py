from __future__ import annotations
from typing import Any
from .orchestrator import HeartTwin
from .service_registry import ServiceRegistry

class VirelionServices:
    """Convenience facade: one object exposes every registered Virelion capability."""
    def __init__(self, registry: ServiceRegistry): self.registry=registry; self.twin=HeartTwin(registry)
    def call(self, capability: str, entity_id: str, **payload: Any) -> dict[str, Any]:
        adapter=self.registry.capability(capability)
        if adapter is None: raise RuntimeError(f"Capability unavailable: {capability}")
        return adapter.invoke(capability, {"entity_id": entity_id, **payload})
    def run_twin(self, entity_id: str, **kwargs): return self.twin.run(entity_id, **kwargs)

    def atlas(self, entity_id: str, **p): return self.call("atlas.context", entity_id, **p)
    def design(self, entity_id: str, **p): return self.call("design.generate", entity_id, **p)
    def population(self, entity_id: str, **p): return self.call("population.generate", entity_id, **p)
    def validate_design(self, entity_id: str, **p): return self.call("design.validate", entity_id, **p)
    def power_plan(self, entity_id: str, **p): return self.call("power.plan", entity_id, **p)
    def challenge_validate(self, entity_id: str, **p): return self.call("challenge.validate", entity_id, **p)
    def challenge_assess(self, entity_id: str, **p): return self.call("challenge.assess", entity_id, **p)
    def materialize_challenge(self, entity_id: str, **p): return self.call("challenge.materialize", entity_id, **p)
    def recovery(self, entity_id: str, **p): return self.call("recovery.score", entity_id, **p)
    def host_map(self, entity_id: str, **p): return self.call("host.map", entity_id, **p)
    def electrical(self, entity_id: str, **p): return self.call("electrical.analyze", entity_id, **p)
    def mechanical(self, entity_id: str, **p): return self.call("mechanical.analyze", entity_id, **p)
    def imaging(self, entity_id: str, **p): return self.call("imaging.qc", entity_id, **p)
    def safety(self, entity_id: str, **p): return self.call("safety.score", entity_id, **p)
    def learn(self, entity_id: str, **p): return self.call("learn.infer", entity_id, **p)
    def simulate(self, entity_id: str, **p): return self.call("simulation.run", entity_id, **p)
    def evaluate(self, entity_id: str, **p): return self.call("evaluation.run", entity_id, **p)
