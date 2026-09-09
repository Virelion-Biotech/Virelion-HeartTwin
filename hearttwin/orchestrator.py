from __future__ import annotations

from typing import Any

from .contracts import CardiacState, Observation, Provenance, ServiceResult, TwinRun
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry

DEFAULT_CAPABILITIES = [
    "atlas.context", "atlas.search", "design.generate", "population.generate",
    "design.validate", "power.plan", "challenge.validate", "challenge.assess",
    "challenge.materialize", "recovery.score", "host.map", "electrical.analyze",
    "mechanical.analyze", "imaging.qc", "safety.score", "learn.infer", "learn.predict",
    "simulation.run", "benchmark.resolve", "evaluation.run", "trace.record",
    "agent.challenge", "vex.observe"
]


class HeartTwin:
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry

    def run(
        self,
        entity_id: str,
        observations: list[Observation] | None = None,
        capabilities: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> TwinRun:
        observations = observations or []
        requested = capabilities or DEFAULT_CAPABILITIES
        payload = {
            "entity_id": entity_id,
            "context": context or {},
            "observations": [o.model_dump(mode="json") for o in observations],
        }
        run_id = new_run_id(entity_id, payload)
        state = CardiacState(
            entity_id=entity_id,
            biological_context=context or {},
            observations=observations,
            provenance=[o.provenance for o in observations],
        )
        results: list[ServiceResult] = []
        for capability in requested:
            adapter = self.registry.capability(capability)
            if adapter is None:
                results.append(
                    ServiceResult(
                        service="registry",
                        capability=capability,
                        status="unavailable",
                        message="No registered service advertises this capability.",
                    )
                )
                continue
            try:
                invocation_payload = payload
                if capability == "trace.record":
                    invocation_payload = {
                        **payload,
                        "results": [
                            {
                                "service": item.service,
                                "capability": item.capability,
                                "status": item.status,
                                "data": item.data,
                                "message": item.message,
                            }
                            for item in results
                        ],
                        "hearttwin_run_id": run_id,
                    }
                out = adapter.invoke(capability, invocation_payload)
                p = Provenance(
                    source_service=adapter.spec.name,
                    source_repository=adapter.spec.repository,
                    run_id=run_id,
                    content_sha256=sha256(out),
                )
                results.append(
                    ServiceResult(
                        service=adapter.spec.name,
                        capability=capability,
                        status="ok",
                        data=out,
                        provenance=p,
                    )
                )
                if capability.startswith("learn."):
                    state.predictions.append({"capability": capability, "data": out, "status": "inferred"})
                elif capability.startswith("simulation."):
                    state.simulations.append({"capability": capability, "data": out, "status": "simulated"})
                elif capability == "trace.record":
                    state.validation["trace"] = out
                elif capability == "evaluation.run":
                    state.validation["evaluation"] = out
            except Exception as exc:
                results.append(
                    ServiceResult(
                        service=adapter.spec.name,
                        capability=capability,
                        status="error",
                        message=str(exc),
                    )
                )
        return TwinRun(
            run_id=run_id,
            entity_id=entity_id,
            requested_capabilities=requested,
            results=results,
            state=state,
        )
