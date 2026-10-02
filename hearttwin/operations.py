from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Callable

from .provenance import sha256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    sha256: str
    size_bytes: int
    path: str
    media_type: str = "application/json"

    def model_dump(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "path": self.path,
            "media_type": self.media_type,
        }


class ArtifactStore:
    """Small content-addressed filesystem store used by operational rehearsals."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, digest: str, suffix: str = ".json") -> Path:
        return self.root / digest[:2] / f"{digest}{suffix}"

    def put_json(self, artifact_id: str, payload: Any) -> ArtifactRecord:
        raw = _canonical_json(payload).encode("utf-8")
        digest = sha256(payload)
        path = self._path_for(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return ArtifactRecord(
            artifact_id=artifact_id,
            sha256=digest,
            size_bytes=len(raw),
            path=str(path),
        )

    def verify(self, record: ArtifactRecord | dict[str, Any]) -> bool:
        if isinstance(record, dict):
            record = ArtifactRecord(**record)
        path = Path(record.path)
        if not path.is_file() or path.stat().st_size != record.size_bytes:
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return False
        return sha256(payload) == record.sha256

    def read_json(self, record: ArtifactRecord | dict[str, Any]) -> Any:
        if isinstance(record, dict):
            record = ArtifactRecord(**record)
        if not self.verify(record):
            raise RuntimeError(f"Artifact verification failed: {record.artifact_id}")
        return json.loads(Path(record.path).read_text(encoding="utf-8"))

    def corrupt_for_rehearsal(self, record: ArtifactRecord) -> None:
        Path(record.path).write_bytes(b"CORRUPTED-BY-HEARTTWIN-REHEARSAL")


class CaseJournal:
    """SQLite-backed durable journal with stage-level idempotency."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    input_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    output_artifact_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stages (
                    case_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    PRIMARY KEY (case_id, stage),
                    FOREIGN KEY (case_id) REFERENCES cases(case_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def begin_case(self, case_id: str, input_payload: Any) -> None:
        digest = sha256(input_payload)
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT input_sha256 FROM cases WHERE case_id=?",
                (case_id,),
            ).fetchone()
            if row is not None and row["input_sha256"] != digest:
                raise RuntimeError(
                    f"case_id {case_id!r} already exists with different input"
                )
            db.execute(
                """
                INSERT INTO cases(case_id,input_sha256,status,attempt,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET
                    attempt=cases.attempt+1,
                    status='running',
                    updated_at=excluded.updated_at
                """,
                (case_id, digest, "running", 1, now, now),
            )
        self.event(case_id, "case.started", {"input_sha256": digest})

    def event(self, case_id: str, event_type: str, payload: Any) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO events(case_id,event_type,payload_json,created_at)
                VALUES(?,?,?,?)
                """,
                (case_id, event_type, _canonical_json(payload), _now()),
            )

    def _stage_row(self, case_id: str, stage: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM stages WHERE case_id=? AND stage=?",
                (case_id, stage),
            ).fetchone()

    def run_stage(
        self,
        case_id: str,
        stage: str,
        payload: Any,
        action: Callable[[], dict[str, Any]],
        *,
        max_attempts: int = 1,
    ) -> tuple[dict[str, Any], bool]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        key = sha256({"case_id": case_id, "stage": stage, "payload": payload})
        previous = self._stage_row(case_id, stage)
        if previous is not None and previous["status"] == "ok":
            if previous["idempotency_key"] != key:
                raise RuntimeError(
                    f"Completed stage {stage!r} was requested with different input"
                )
            return json.loads(previous["result_json"]), True

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            now = _now()
            with self._connect() as db:
                db.execute(
                    """
                    INSERT INTO stages(
                        case_id,stage,idempotency_key,status,attempts,started_at
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(case_id,stage) DO UPDATE SET
                        idempotency_key=excluded.idempotency_key,
                        status='running',
                        attempts=stages.attempts+1,
                        error=NULL,
                        started_at=excluded.started_at,
                        finished_at=NULL
                    """,
                    (case_id, stage, key, "running", 1, now),
                )
            self.event(
                case_id,
                "stage.started",
                {"stage": stage, "attempt": attempt, "idempotency_key": key},
            )
            try:
                result = action()
                encoded = _canonical_json(result)
            except Exception as exc:
                last_error = exc
                with self._connect() as db:
                    db.execute(
                        """
                        UPDATE stages SET status='error', error=?, finished_at=?
                        WHERE case_id=? AND stage=?
                        """,
                        (str(exc), _now(), case_id, stage),
                    )
                self.event(
                    case_id,
                    "stage.failed",
                    {"stage": stage, "attempt": attempt, "error": str(exc)},
                )
                continue

            with self._connect() as db:
                db.execute(
                    """
                    UPDATE stages SET status='ok', result_json=?, error=NULL, finished_at=?
                    WHERE case_id=? AND stage=?
                    """,
                    (encoded, _now(), case_id, stage),
                )
            self.event(
                case_id,
                "stage.completed",
                {"stage": stage, "attempt": attempt, "idempotency_key": key},
            )
            return json.loads(encoded), False

        assert last_error is not None
        raise last_error

    def complete_case(self, case_id: str, artifact: ArtifactRecord) -> None:
        if not ArtifactStore(Path(artifact.path).parents[1]).verify(artifact):
            raise RuntimeError("Cannot complete case with an invalid output artifact")
        with self._connect() as db:
            db.execute(
                """
                UPDATE cases SET status='ok',output_artifact_json=?,updated_at=?
                WHERE case_id=?
                """,
                (_canonical_json(artifact.model_dump()), _now(), case_id),
            )
        self.event(case_id, "case.completed", artifact.model_dump())

    def fail_case(self, case_id: str, error: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE cases SET status='error',updated_at=? WHERE case_id=?",
                (_now(), case_id),
            )
        self.event(case_id, "case.failed", {"error": error})

    def snapshot(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            case = db.execute(
                "SELECT * FROM cases WHERE case_id=?",
                (case_id,),
            ).fetchone()
            if case is None:
                raise KeyError(case_id)
            stages = db.execute(
                "SELECT * FROM stages WHERE case_id=? ORDER BY rowid",
                (case_id,),
            ).fetchall()
            events = db.execute(
                "SELECT * FROM events WHERE case_id=? ORDER BY sequence",
                (case_id,),
            ).fetchall()
        return {
            "case": dict(case),
            "stages": [dict(row) for row in stages],
            "events": [dict(row) for row in events],
        }



class JobQueue:
    """SQLite lease-based queue for crash/reclaim rehearsal semantics."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                ON jobs(status, created_at)
                """
            )

    def enqueue(
        self,
        case_id: str,
        job_type: str,
        payload: Any,
        *,
        idempotency_key: str | None = None,
    ) -> str:
        key = idempotency_key or sha256(
            {
                "case_id": case_id,
                "job_type": job_type,
                "payload": payload,
            }
        )
        job_id = f"job-{key[:24]}"
        encoded = _canonical_json(payload)
        now = _now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT case_id,job_type,payload_json FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["case_id"] != case_id
                    or existing["job_type"] != job_type
                    or existing["payload_json"] != encoded
                ):
                    raise RuntimeError(
                        f"job_id collision for idempotency key {key}"
                    )
                return job_id
            db.execute(
                """
                INSERT INTO jobs(
                    job_id,case_id,job_type,payload_json,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (job_id, case_id, job_type, encoded, "queued", now, now),
            )
        return job_id

    def _requeue_expired(self, db: sqlite3.Connection, now: float) -> int:
        cursor = db.execute(
            """
            UPDATE jobs SET
                status='queued',
                lease_owner=NULL,
                lease_expires_at=NULL,
                updated_at=?
            WHERE status='leased'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (_now(), now),
        )
        return int(cursor.rowcount)

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._requeue_expired(db, now)
            row = db.execute(
                """
                SELECT * FROM jobs
                WHERE status='queued'
                ORDER BY created_at, job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                db.commit()
                return None
            expires = now + lease_seconds
            db.execute(
                """
                UPDATE jobs SET
                    status='leased',
                    attempts=attempts+1,
                    lease_owner=?,
                    lease_expires_at=?,
                    error=NULL,
                    updated_at=?
                WHERE job_id=? AND status='queued'
                """,
                (worker_id, expires, _now(), row["job_id"]),
            )
            claimed = db.execute(
                "SELECT * FROM jobs WHERE job_id=?",
                (row["job_id"],),
            ).fetchone()
            db.commit()

        assert claimed is not None
        result = dict(claimed)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE jobs SET lease_expires_at=?,updated_at=?
                WHERE job_id=? AND status='leased' AND lease_owner=?
                """,
                (time.time() + lease_seconds, _now(), job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cannot heartbeat an unowned or inactive job")

    def complete(
        self,
        job_id: str,
        worker_id: str,
        result: Any,
    ) -> None:
        encoded = _canonical_json(result)
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE jobs SET
                    status='ok',
                    result_json=?,
                    lease_owner=NULL,
                    lease_expires_at=NULL,
                    error=NULL,
                    updated_at=?
                WHERE job_id=? AND status='leased' AND lease_owner=?
                """,
                (encoded, _now(), job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cannot complete an unowned or inactive job")

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        *,
        requeue: bool = False,
    ) -> None:
        status = "queued" if requeue else "error"
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE jobs SET
                    status=?,
                    error=?,
                    lease_owner=NULL,
                    lease_expires_at=NULL,
                    updated_at=?
                WHERE job_id=? AND status='leased' AND lease_owner=?
                """,
                (status, error, _now(), job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cannot fail an unowned or inactive job")

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        if result.get("result_json") is not None:
            result["result"] = json.loads(result["result_json"])
        return result

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at,job_id"
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            if item.get("result_json") is not None:
                item["result"] = json.loads(item["result_json"])
            output.append(item)
        return output
