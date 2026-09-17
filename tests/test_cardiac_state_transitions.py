import pytest

from hearttwin.contracts import Observation, Provenance, ServiceResult
from hearttwin.orchestrator import HeartTwin
from hearttwin.service_registry import ServiceRegistry, ServiceSpec
from hearttwin.state import CardiacStateStore, CardiacStateValidationError


def test_phase_history_is_explicit_and_ordered():
    observation = Observation(
        observation_id="obs-1",
        modality="clinical",
        values={"status": "synthetic"},
        provenance=Provenance(source_service="fixture", run_id="obs-1"),
    )
    store = CardiacStateStore.new("entity-1", [observation])
    store.transition("simulated", trigger="simulation.run", provenance=Provenance(source_service="CardiSim", run_id="sim-1"))
    store.transition("validated", trigger="evaluation.run", provenance=Provenance(source_service="CardiEval", run_id="eval-1"))
    snapshot = store.snapshot()
    assert [(t.from_phase, t.to_phase) for t in snapshot.transitions] == [
        ("baseline", "simulated"),
        ("simulated", "validated"),
    ]
    assert snapshot.state_phase == "validated"


def test_noop_transition_is_rejected():
    store = CardiacStateStore.new("entity-1")
    with pytest.raises(CardiacStateValidationError, match="No-op"):
        store.transition("unknown", trigger="fixture")


def test_typed_reduction_error_is_reported_by_legacy_runner():
    class BadAtlas:
        spec = ServiceSpec("CardiAtlas", "Virelion-Biotech/Virelion-CardiAtlas", ("atlas.context",))

        def invoke(self, capability, payload):
            return {"not": "an AtlasContextPayload"}

    class Registry(ServiceRegistry):
        def __init__(self):
            super().__init__([])
            self._adapter = BadAtlas()

        def capability(self, capability):
            return self._adapter if capability == "atlas.context" else None

    run = HeartTwin(Registry()).run("entity-1", capabilities=["atlas.context"])
    assert run.results[0].status == "error"
    assert "atlas.context" in (run.results[0].message or "")
