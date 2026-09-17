import json
from pathlib import Path

from jsonschema import Draft202012Validator

from hearttwin.contracts import CardiacState, Observation, Provenance
from hearttwin.state import CardiacStateStore


SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "cardiac-state-1.1.0.schema.json"


def test_published_cardiac_state_schema_is_valid_and_accepts_runtime_snapshot():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    observation = Observation(
        observation_id="obs-1",
        modality="clinical",
        values={"heart_rate": 70},
        provenance=Provenance(source_service="fixture", run_id="fixture-1"),
    )
    snapshot = CardiacStateStore.new("entity-1", [observation]).snapshot()
    errors = sorted(Draft202012Validator(schema).iter_errors(snapshot.model_dump(mode="json")), key=str)
    assert not errors, [error.message for error in errors]
    assert snapshot.contract_version == "1.1.0"
    assert schema["$id"].endswith("cardiac-state-1.1.0.json")


def test_card_state_default_is_backward_compatible():
    state = CardiacState(entity_id="entity-1")
    assert state.state_phase == "unknown"
    assert state.simulations == []
    assert state.predictions == []
    assert state.validation == {}
