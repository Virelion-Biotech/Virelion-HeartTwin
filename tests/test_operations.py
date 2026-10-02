from __future__ import annotations

from pathlib import Path

import pytest

from hearttwin.operations import ArtifactStore, CaseJournal, JobQueue


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


def test_job_queue_reclaims_expired_lease_after_worker_crash(tmp_path: Path) -> None:
    import time

    queue = JobQueue(tmp_path / "journal.sqlite3")
    job_id = queue.enqueue("C1", "simulate", {"value": 7})

    first = queue.claim("worker-a", lease_seconds=0.05)
    assert first is not None
    assert first["job_id"] == job_id
    assert first["attempts"] == 1

    time.sleep(0.08)
    second = queue.claim("worker-b", lease_seconds=1.0)
    assert second is not None
    assert second["job_id"] == job_id
    assert second["attempts"] == 2
    assert second["lease_owner"] == "worker-b"

    queue.complete(job_id, "worker-b", {"ok": True})
    final = queue.get(job_id)
    assert final["status"] == "ok"
    assert final["attempts"] == 2
    assert final["result"] == {"ok": True}


def test_job_queue_idempotent_enqueue_returns_same_job(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.sqlite3")
    first = queue.enqueue("C1", "stage", {"x": 1})
    second = queue.enqueue("C1", "stage", {"x": 1})
    assert first == second
    assert len(queue.list_jobs()) == 1


def test_job_queue_rejects_wrong_worker_completion(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.sqlite3")
    job_id = queue.enqueue("C1", "stage", {"x": 1})
    claimed = queue.claim("worker-a")
    assert claimed is not None
    with pytest.raises(RuntimeError, match="unowned"):
        queue.complete(job_id, "worker-b", {"ok": True})


def test_job_queue_can_requeue_explicit_failure(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.sqlite3")
    job_id = queue.enqueue("C1", "stage", {"x": 1})
    claimed = queue.claim("worker-a")
    assert claimed is not None
    queue.fail(job_id, "worker-a", "transient", requeue=True)

    reclaimed = queue.claim("worker-b")
    assert reclaimed is not None
    assert reclaimed["job_id"] == job_id
    assert reclaimed["attempts"] == 2


def test_job_queue_allows_only_one_concurrent_lease(tmp_path: Path) -> None:
    import threading

    queue = JobQueue(tmp_path / "queue.sqlite3")
    job_id = queue.enqueue("C1", "stage", {"x": 1})
    barrier = threading.Barrier(3)
    claims: list[dict | None] = []

    def claimant(worker_id: str) -> None:
        barrier.wait()
        claims.append(queue.claim(worker_id, lease_seconds=5.0))

    first = threading.Thread(target=claimant, args=("worker-a",))
    second = threading.Thread(target=claimant, args=("worker-b",))
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=5)
    second.join(timeout=5)

    successful = [claim for claim in claims if claim is not None]
    assert len(successful) == 1
    assert successful[0]["job_id"] == job_id
    assert queue.get(job_id)["status"] == "leased"
