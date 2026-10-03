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
    store.transition("evaluated", trigger="evaluation.run", provenance=Provenance(source_service="CardiEval", run_id="eval-1"))
    snapshot = store.snapshot()
    assert [(t.from_phase, t.to_phase) for t in snapshot.transitions] == [
        ("baseline", "simulated"),
        ("simulated", "evaluated"),
    ]
    assert snapshot.state_phase == "evaluated"


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


def test_inference_result_becomes_first_class_posterior_artifact():
    store = CardiacStateStore.new("entity-1")
    result = ServiceResult(
        service="CardiInfer",
        capability="infer.run",
        status="ok",
        data={
            "contract_version": "1.1",
            "subject_id": "entity-1",
            "backend": "native-abc-smc-v1",
            "model_service": "CardiEP",
            "model_capability": "ep.simulate",
            "posterior": [
                {
                    "parameter": "fibre_speed",
                    "mean": 0.1,
                    "median": 0.1,
                    "sd": 0.01,
                    "q025": 0.08,
                    "q975": 0.12,
                    "unit": "cm/ms",
                }
            ],
            "posterior_samples": None,
            "convergence": {"converged": True},
            "identifiability": {"status": "acceptable", "weak_parameters": [], "diagnostics": {}},
            "sensitivity": None,
            "diagnostics": {},
            "validation_status": "synthetic_recovery_checked",
            "provenance": {},
        },
        provenance=Provenance(source_service="CardiInfer", run_id="infer-1"),
    )
    store.reduce_service_result(result)
    snapshot = store.snapshot()
    assert len(snapshot.posterior_artifacts) == 1
    artifact = snapshot.posterior_artifacts[0]
    assert artifact.subject_id == "entity-1"
    assert artifact.model_service == "CardiEP"
    assert artifact.validation_status == "synthetic_recovery_checked"


def test_flow_and_therapy_results_become_typed_artifacts():
    store = CardiacStateStore.new("entity-1")
    store.reduce_service_result(
        ServiceResult(
            service="CardiFlow",
            capability="flow.simulate",
            status="ok",
            data={
                "contract_version": "1.0",
                "subject_id": "entity-1",
                "backend": "windkessel-3element-v1",
                "outputs": [],
                "scalar_outputs": {"mean_outlet_pressure": 10.0},
                "series_outputs": {"outlet_pressure": [10.0, 10.0]},
                "qc": {"passed": True},
                "validation_status": "software_checked",
                "provenance": {},
            },
            provenance=Provenance(source_service="CardiFlow", run_id="flow-1"),
        )
    )
    store.reduce_service_result(
        ServiceResult(
            service="CardiTherapy",
            capability="therapy.run",
            status="ok",
            data={
                "contract_version": "1.0",
                "subject_id": "entity-1",
                "backend": "validated-delegate-example",
                "plan_id": "plan-1",
                "outcomes": [{"arm_id": "control", "endpoint": "x", "value": 1.0}],
                "artifacts": [],
                "validation_status": "unvalidated",
                "warnings": ["fixture"],
                "provenance": {},
            },
            provenance=Provenance(source_service="CardiTherapy", run_id="therapy-1"),
        )
    )
    snapshot = store.snapshot()
    assert snapshot.flow_artifacts[0].validation_status == "software_checked"
    assert snapshot.therapy_artifacts[0].plan_id == "plan-1"
