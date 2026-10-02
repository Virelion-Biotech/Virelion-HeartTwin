from __future__ import annotations

from pathlib import Path

import pytest

from hearttwin.operations import ArtifactStore, CaseJournal


def test_artifact_store_detects_corruption_and_repairs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    payload = {"case": "C1", "value": [1, 2, 3]}
    record = store.put_json("result", payload)

    assert store.verify(record)
    assert store.read_json(record) == payload

    store.corrupt_for_rehearsal(record)
    assert not store.verify(record)
    with pytest.raises(RuntimeError, match="verification failed"):
        store.read_json(record)

    repaired = store.put_json("result", payload)
    assert repaired.sha256 == record.sha256
    assert store.verify(repaired)


def test_case_journal_replays_completed_stage_without_execution(
    tmp_path: Path,
) -> None:
    journal = CaseJournal(tmp_path / "journal.sqlite3")
    journal.begin_case("C1", {"input": 1})
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        return {"value": 42}

    first, first_reused = journal.run_stage(
        "C1",
        "stage-a",
        {"x": 1},
        action,
    )
    second, second_reused = journal.run_stage(
        "C1",
        "stage-a",
        {"x": 1},
        action,
    )

    assert first == second == {"value": 42}
    assert not first_reused
    assert second_reused
    assert calls == 1


def test_case_journal_rejects_changed_input_for_completed_stage(
    tmp_path: Path,
) -> None:
    journal = CaseJournal(tmp_path / "journal.sqlite3")
    journal.begin_case("C1", {"input": 1})
    journal.run_stage("C1", "stage-a", {"x": 1}, lambda: {"ok": True})

    with pytest.raises(RuntimeError, match="different input"):
        journal.run_stage(
            "C1",
            "stage-a",
            {"x": 2},
            lambda: {"ok": False},
        )


def test_case_journal_survives_reopen_and_retries_failed_stage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = CaseJournal(path)
    journal.begin_case("C1", {"input": 1})
    attempts = 0

    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    result, reused = journal.run_stage(
        "C1",
        "stage-a",
        {"x": 1},
        flaky,
        max_attempts=2,
    )
    assert result == {"ok": True}
    assert not reused
    assert attempts == 2

    reopened = CaseJournal(path)
    snapshot = reopened.snapshot("C1")
    assert snapshot["stages"][0]["status"] == "ok"
    assert snapshot["stages"][0]["attempts"] == 2


def test_case_id_cannot_be_reused_for_different_input(tmp_path: Path) -> None:
    journal = CaseJournal(tmp_path / "journal.sqlite3")
    journal.begin_case("C1", {"input": 1})
    with pytest.raises(RuntimeError, match="different input"):
        journal.begin_case("C1", {"input": 2})
