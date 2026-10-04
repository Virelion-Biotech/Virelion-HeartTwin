from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import Observation, Provenance, ServiceResult, TwinRun
from .provenance import new_run_id, sha256
from .service_registry import ServiceRegistry
from .state import CardiacStateStore, CardiacStateValidationError

DEFAULT_CAPABILITIES = [
    "atlas.context", "atlas.search", "design.generate", "population.generate",
    "design.validate", "power.plan", "challenge.validate", "challenge.assess",
    "challenge.materialize", "recovery.score", "host.map", "electrical.analyze",
    "mechanical.analyze", "imaging.qc", "safety.score", "learn.infer", "learn.predict",
    "simulation.run", "benchmark.resolve", "evaluation.run", "trace.record",
    "agent.challenge", "vex.observe"
]

TYPED_STATE_CAPABILITIES = {
    "anatomy.build", "atlas.context", "benchmark.resolve", "learn.infer", "learn.predict",
    "ep.simulate", "infer.run", "mechanics.simulate", "flow.simulate", "therapy.run",
    "simulation.run", "evaluation.run", "agent.challenge", "bridge.publish", "vex.observe",
    "trace.record",
}

_EXPLICIT_CONTEXT_REQUESTS = {
    "anatomy.build": "cardianatomy_request",
    "ep.simulate": "cardiep_request",
    "infer.run": "cardiinfer_request",
    "mechanics.simulate": "cardimech_request",
    "mechanics.prepare_calibration": "cardimech_calibration_request",
    "flow.simulate": "cardiflow_request",
    "therapy.run": "carditherapy_request",
}


def _capability_payload(
    capability: str,
    *,
    entity_id: str,
    generic_payload: dict[str, Any],
) -> dict[str, Any]:
    context_key = _EXPLICIT_CONTEXT_REQUESTS.get(capability)
    if context_key is None:
        return generic_payload
    context = generic_payload.get("context") or {}
    template = context.get(context_key)
    if not isinstance(template, dict):
        raise ValueError(
            f"{capability} requires context.{context_key}; "
            "HeartTwin will not broadcast a generic observation envelope into a specialist schema"
        )
    request = deepcopy(template)
    declared = request.get("subject_id")
    if declared is not None and str(declared) != entity_id:
        raise ValueError(
            f"{capability} request subject_id {declared!r} does not match entity_id {entity_id!r}"
        )
    request["subject_id"] = entity_id
    if capability == "anatomy.build":
        acquisition = request.get("acquisition")
        if isinstance(acquisition, dict):
            acquired_subject = acquisition.get("subject_id")
            if acquired_subject is not None and str(acquired_subject) != entity_id:
                raise ValueError(
                    "anatomy.build acquisition subject_id does not match HeartTwin entity_id"
                )
            acquisition = deepcopy(acquisition)
            acquisition["subject_id"] = entity_id
            request["acquisition"] = acquisition
    return request


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
        store = CardiacStateStore.new(
            entity_id,
            observations=observations,
            biological_context=context or {},
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
                invocation_payload = _capability_payload(
                    capability,
                    entity_id=entity_id,
                    generic_payload=payload,
                )
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
                step_run_id = new_run_id(
                    entity_id,
                    {"hearttwin_run_id": run_id, "capability": capability, "data": out},
                )
                p = Provenance(
                    source_service=adapter.spec.name,
                    source_repository=adapter.spec.repository,
                    run_id=step_run_id,
                    parent_run_ids=[run_id],
                    content_sha256=sha256(out),
                )
                result = ServiceResult(
                    service=adapter.spec.name,
                    capability=capability,
                    status="ok",
                    data=out,
                    provenance=p,
                )
                try:
                    store.reduce_service_result(result)
                except CardiacStateValidationError as exc:
                    if capability in TYPED_STATE_CAPABILITIES:
                        raise exc
                    # Specialist legacy adapters are still retained as raw ServiceResult.data.
                results.append(result)
            except Exception as exc:
                results.append(
                    ServiceResult(
                        service=adapter.spec.name,
                        capability=capability,
                        status="error",
                        message=str(exc),
                    )
                )
        state = store.snapshot()
        return TwinRun(
            run_id=run_id,
            entity_id=entity_id,
            requested_capabilities=requested,
            results=results,
            state=state,
        )
