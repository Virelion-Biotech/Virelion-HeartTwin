import pytest

from hearttwin.contracts import (
    AtlasContextPayload,
    EvaluationResultPayload,
    LearningPredictionPayload,
    LearningResultPayload,
    Observation,
    Provenance,
    ServiceResult,
    SimulationResultPayload,
    StateValue,
)
from hearttwin.state import CardiacStateStore, CardiacStateValidationError, state_from_service_results


def provenance(service: str, run_id: str) -> Provenance:
    return Provenance(source_service=service, run_id=run_id, content_sha256="a" * 64)


def test_canonical_store_reduces_typed_artifacts_and_fingerprints():
    observation_prov = provenance("fixture", "obs-1")
    observation = Observation(
        observation_id="obs-1",
        modality="electrical",
        values={"rhythm": "synthetic"},
        provenance=observation_prov,
    )
    store = CardiacStateStore.new("subject-1", [observation])

    atlas_prov = provenance("CardiAtlas", "atlas-1")
    store.record_atlas(
        AtlasContextPayload(context_id="subject-1-context", record_ids=["r1"]),
        atlas_prov,
    )

    learning_prov = provenance("CardiLearn", "learn-1")
    learning = LearningResultPayload(
        model_id="demo-model",
        task="classification",
        target_column="target",
        feature_columns=["x1"],
        metrics={"test": {"accuracy": 1.0}},
        predictions=[LearningPredictionPayload(sample_id="s1", y_true=1, y_pred=1, score=0.9)],
        dataset_fingerprint="b" * 64,
    )
    store.record_learning(learning, learning_prov, capability="learn.predict")

    simulation_prov = provenance("CardiSim", "sim-1")
    simulation = SimulationResultPayload(
        backend="synthetic",
        summary={"cardiac_health_score": 0.8},
        events=["start", "finish"],
        population_size=4,
    )
    store.record_simulation(simulation, simulation_prov)

    evaluation_prov = provenance("CardiEval", "eval-1")
    evaluation = EvaluationResultPayload(
        benchmark_id="b1",
        benchmark_version="1.0",
        task_id="t1",
        model_id="demo-model",
        primary_metric="macro_f1",
        primary_value=1.0,
        metrics=[{"name": "macro_f1", "value": 1.0}],
        evaluation_fingerprint="c" * 64,
    )
    store.record_evaluation(evaluation, evaluation_prov)

    store.add_derived_value(
        domain="simulation",
        variable="cardiac_health_score",
        value=0.8,
        status="simulated",
        provenance=simulation_prov,
    )

    snapshot = store.snapshot()
    assert snapshot.contract_version == "1.1.0"
    assert snapshot.state_phase == "validated"
    assert len(snapshot.prediction_artifacts) == 1
    assert snapshot.predictions[0]["capability"] == "learn.predict"
    assert len(snapshot.simulation_artifacts) == 1
    assert len(snapshot.evaluation_artifacts) == 1
    assert snapshot.state_fingerprint
    assert snapshot.state_fingerprint == store.fingerprint()
    assert all(
        link in {p.run_id for p in snapshot.provenance}
        for item in snapshot.derived_values
        for link in item.provenance_ids
    )


def test_state_from_service_results_uses_typed_reduction():
    obs = Observation(
        observation_id="obs-1",
        modality="clinical",
        values={"heart_rate": 70},
        provenance=provenance("fixture", "obs-1"),
    )
    results = [
        ServiceResult(
            service="CardiAtlas",
            capability="atlas.context",
            status="ok",
            data=AtlasContextPayload(context_id="ctx-1").model_dump(mode="json"),
            provenance=provenance("CardiAtlas", "atlas-1"),
        )
    ]
    state = state_from_service_results("subject-1", [obs], results)
    assert state.atlas_context is not None
    assert state.atlas_context.context_id == "ctx-1"
    assert state.state_fingerprint


def test_dangling_provenance_is_rejected():
    store = CardiacStateStore.new("subject-1")
    store.state.derived_values.append(
        StateValue(
            value_id="v1",
            domain="clinical",
            variable="heart_rate",
            value=70,
            status="inferred",
            provenance_ids=["missing-run"],
        )
    )
    with pytest.raises(CardiacStateValidationError, match="Dangling HeartTwin provenance"):
        store.validate()


def test_snapshot_fingerprint_detects_tampering():
    store = CardiacStateStore.new("subject-1")
    snapshot = store.snapshot()
    snapshot.biological_context["changed"] = True
    tampered = CardiacStateStore.from_snapshot(snapshot, verify=False)
    with pytest.raises(CardiacStateValidationError, match="fingerprint mismatch"):
        tampered.verify_fingerprint()


def test_from_snapshot_rejects_invalid_embedded_fingerprint():
    store = CardiacStateStore.new("subject-1")
    snapshot = store.snapshot()
    snapshot.state_fingerprint = "0" * 64
    with pytest.raises(CardiacStateValidationError, match="fingerprint mismatch"):
        CardiacStateStore.from_snapshot(snapshot)
