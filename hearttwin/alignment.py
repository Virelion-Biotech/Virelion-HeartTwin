"""Fail-closed checks for caller-declared observation alignment."""
from __future__ import annotations

from collections import Counter

from .contracts import AlignmentPolicy, AlignmentReport, Observation


def assess_observation_alignment(
    entity_id: str,
    observations: list[Observation],
    policy: AlignmentPolicy | dict | None = None,
) -> AlignmentReport:
    """Check explicit subject/timepoint declarations without inferring identity."""
    selected = (
        policy
        if isinstance(policy, AlignmentPolicy)
        else AlignmentPolicy.model_validate(policy or {})
    )
    reasons: list[str] = []
    ids = [item.observation_id for item in observations]
    duplicates = sorted(
        observation_id
        for observation_id, count in Counter(ids).items()
        if count > 1
    )
    if duplicates:
        reasons.append(
            "duplicate observation_id values: " + ", ".join(duplicates)
        )

    declared_subject_ids = sorted(
        {item.subject_id for item in observations if item.subject_id}
    )
    mismatched_subjects = sorted(
        {
            item.subject_id
            for item in observations
            if item.subject_id and item.subject_id != entity_id
        }
    )
    if mismatched_subjects:
        reasons.append(
            "observation subject_id disagrees with workflow entity_id "
            f"{entity_id!r}: {', '.join(mismatched_subjects)}"
        )

    missing_subject = [
        item.observation_id for item in observations if not item.subject_id
    ]
    if selected.require_subject_id and missing_subject:
        reasons.append(
            "subject_id is required for observations: "
            + ", ".join(sorted(missing_subject))
        )

    declared_timepoints = sorted(
        {item.timepoint_id for item in observations if item.timepoint_id}
    )
    missing_timepoint = [
        item.observation_id for item in observations if not item.timepoint_id
    ]
    if selected.require_timepoint_id and missing_timepoint:
        reasons.append(
            "timepoint_id is required for observations: "
            + ", ".join(sorted(missing_timepoint))
        )
    if selected.require_single_timepoint and len(declared_timepoints) > 1:
        reasons.append(
            "observations declare multiple timepoints under a single-timepoint "
            "policy: " + ", ".join(declared_timepoints)
        )

    present_modalities = {item.modality for item in observations}
    missing_modalities = sorted(
        set(selected.required_modalities) - present_modalities
    )
    if missing_modalities:
        reasons.append(
            "required observation modalities are missing: "
            + ", ".join(missing_modalities)
        )

    incomplete = bool(missing_subject or missing_timepoint)
    return AlignmentReport(
        entity_id=entity_id,
        passed=not reasons,
        status=(
            "failed"
            if reasons
            else ("incomplete" if incomplete else "consistent")
        ),
        observation_ids=ids,
        declared_subject_ids=declared_subject_ids,
        declared_timepoint_ids=declared_timepoints,
        missing_subject_ids=sorted(missing_subject),
        missing_timepoint_ids=sorted(missing_timepoint),
        reasons=reasons,
        policy=selected,
    )
