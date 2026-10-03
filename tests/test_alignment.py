from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from hearttwin import (
    AlignmentPolicy,
    CardiacStateStore,
    Observation,
    Provenance,
    assess_observation_alignment,
)


def _obs(
    observation_id: str,
    *,
    subject_id: str | None = None,
    timepoint_id: str | None = None,
    modality: str = "clinical",
) -> Observation:
    return Observation(
        observation_id=observation_id,
        modality=modality,
        values={},
        subject_id=subject_id,
        timepoint_id=timepoint_id,
        provenance=Provenance(source_service="fixture", run_id=observation_id),
    )


def test_explicit_cross_subject_mismatch_always_fails() -> None:
    report = assess_observation_alignment(
        "S1",
        [_obs("o1", subject_id="S2", timepoint_id="baseline")],
    )
    assert report.passed is False
    assert report.status == "failed"
    assert any("disagrees" in reason for reason in report.reasons)


def test_missing_declarations_are_visible_but_permitted_by_default() -> None:
    report = assess_observation_alignment("S1", [_obs("o1")])
    assert report.passed is True
    assert report.status == "incomplete"
    assert report.missing_subject_ids == ["o1"]
    assert report.missing_timepoint_ids == ["o1"]


def test_strict_alignment_requires_one_explicit_subject_and_timepoint() -> None:
    policy = AlignmentPolicy(
        require_subject_id=True,
        require_timepoint_id=True,
        require_single_timepoint=True,
        required_modalities=["electrical", "mechanical"],
    )
    good = assess_observation_alignment(
        "S1",
        [
            _obs(
                "e1",
                subject_id="S1",
                timepoint_id="baseline",
                modality="electrical",
            ),
            _obs(
                "m1",
                subject_id="S1",
                timepoint_id="baseline",
                modality="mechanical",
            ),
        ],
        policy,
    )
    assert good.passed is True
    assert good.status == "consistent"

    mixed = assess_observation_alignment(
        "S1",
        [
            _obs("e1", subject_id="S1", timepoint_id="t0", modality="electrical"),
            _obs("m1", subject_id="S1", timepoint_id="t1", modality="mechanical"),
        ],
        policy,
    )
    assert mixed.passed is False
    assert any("multiple timepoints" in reason for reason in mixed.reasons)


def test_observation_alignment_fields_roundtrip_through_canonical_schema() -> None:
    observation = _obs(
        "o1",
        subject_id="S1",
        timepoint_id="baseline",
        modality="electrical",
    )
    snapshot = CardiacStateStore.new("S1", [observation]).snapshot()
    payload = snapshot.model_dump(mode="json")
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "cardiac-state-1.2.0.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.validate(payload, schema)
