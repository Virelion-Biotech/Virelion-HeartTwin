"""Immutable patient revisions and append-only review/run lineage on SQLite.

This is a trusted-worker research store, not an EHR or a de-identification tool.
There is deliberately no destructive delete API. Archive is reversible.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .patient_model import PatientCase, assess_patient_case_for_ep
from .provenance import sha256

_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(
            "Patient identifiers must contain 1–100 letters, digits, underscores or hyphens"
        )
    return value


class RevisionConflict(ValueError):
    """The caller attempted to overwrite a newer patient revision."""


class PatientCaseStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS patient_revisions (
                    patient_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, parent_fingerprint TEXT,
                    envelope TEXT NOT NULL, PRIMARY KEY(patient_id, revision));
                CREATE TABLE IF NOT EXISTS patient_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    envelope TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    previous_fingerprint TEXT);
                CREATE TABLE IF NOT EXISTS patient_runs (
                    run_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, request_sha256 TEXT NOT NULL,
                    envelope TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(
        self,
        case: PatientCase | dict,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ):
        case = PatientCase.model_validate(case)
        identifier(case.patient_id)
        if not actor.strip() or not reason.strip():
            raise ValueError("Revision actor and reason are required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM patient_revisions WHERE patient_id=? ORDER BY revision DESC LIMIT 1",
                (case.patient_id,),
            ).fetchone()
            revision = previous["revision"] if previous else 0
            if revision != expected_revision:
                raise RevisionConflict(
                    f"Patient revision changed: expected {expected_revision}, current {revision}"
                )
            envelope = {
                "patient_id": case.patient_id,
                "revision": revision + 1,
                "parent_fingerprint": previous["fingerprint"] if previous else None,
                "created_at": now(),
                "actor": actor,
                "reason": reason,
                "case": case.model_dump(mode="json"),
            }
            fingerprint = sha256(envelope)
            db.execute(
                "INSERT INTO patient_revisions VALUES(?,?,?,?,?)",
                (
                    case.patient_id,
                    revision + 1,
                    fingerprint,
                    envelope["parent_fingerprint"],
                    canonical(envelope),
                ),
            )
        return {**envelope, "fingerprint": fingerprint}

    def get(self, patient_id: str, revision: int | None = None):
        identifier(patient_id)
        with self.connect() as db:
            if revision is None:
                row = db.execute(
                    "SELECT * FROM patient_revisions WHERE patient_id=? ORDER BY revision DESC LIMIT 1",
                    (patient_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM patient_revisions WHERE patient_id=? AND revision=?",
                    (patient_id, revision),
                ).fetchone()
            if row is None:
                raise KeyError(patient_id)
            # Verify the complete ancestry, rather than trusting a mutable parent column.
            chain = db.execute(
                "SELECT * FROM patient_revisions WHERE patient_id=? AND revision<=? ORDER BY revision",
                (patient_id, row["revision"]),
            ).fetchall()
        parent = None
        for number, entry in enumerate(chain, 1):
            value = json.loads(entry["envelope"])
            if (
                entry["revision"] != number
                or value["revision"] != number
                or value["patient_id"] != patient_id
                or sha256(value) != entry["fingerprint"]
                or value["parent_fingerprint"] != parent
                or entry["parent_fingerprint"] != parent
            ):
                raise ValueError("Patient revision integrity verification failed")
            parent = entry["fingerprint"]
        return {**json.loads(row["envelope"]), "fingerprint": row["fingerprint"]}

    def history(self, patient_id):
        latest = self.get(patient_id)
        return [
            {
                key: value
                for key, value in self.get(patient_id, n).items()
                if key != "case"
            }
            for n in range(1, latest["revision"] + 1)
        ]

    def list_cases(self):
        with self.connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT patient_id FROM patient_revisions ORDER BY patient_id"
                )
            ]
        result = []
        for patient_id in ids:
            item = self.get(patient_id)
            events = self.events(patient_id)
            archive = [e for e in events if e["kind"] in {"archive", "restore"}]
            result.append(
                {
                    "patient_id": patient_id,
                    "revision": item["revision"],
                    "fingerprint": item["fingerprint"],
                    "updated_at": item["created_at"],
                    "archived": bool(archive and archive[-1]["kind"] == "archive"),
                }
            )
        return result

    def event(self, patient_id, revision, *, kind, actor, detail):
        if kind not in {"review", "archive", "restore", "run_bound", "run_result"}:
            raise ValueError("Unsupported patient event")
        if not actor.strip() or not isinstance(detail, dict):
            raise ValueError("Event actor and detail are required")
        snapshot = self.get(patient_id, revision)
        if kind == "review" and (
            not detail.get("rationale")
            or detail.get("decision") not in {"approved", "changes_requested"}
        ):
            raise ValueError("Review requires a decision and rationale")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT fingerprint FROM patient_events WHERE patient_id=? ORDER BY sequence DESC LIMIT 1",
                (patient_id,),
            ).fetchone()
            previous = last[0] if last else None
            value = {
                "patient_id": patient_id,
                "revision": revision,
                "case_fingerprint": snapshot["fingerprint"],
                "kind": kind,
                "actor": actor,
                "created_at": now(),
                "detail": detail,
                "previous_fingerprint": previous,
            }
            digest = sha256(value)
            db.execute(
                "INSERT INTO patient_events(patient_id,revision,envelope,fingerprint,previous_fingerprint) VALUES(?,?,?,?,?)",
                (patient_id, revision, canonical(value), digest, previous),
            )
        return {**value, "fingerprint": digest}

    def events(self, patient_id):
        self.get(patient_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM patient_events WHERE patient_id=? ORDER BY sequence",
                (patient_id,),
            ).fetchall()
        previous, result = None, []
        for row in rows:
            item = json.loads(row["envelope"])
            if (
                sha256(item) != row["fingerprint"]
                or item["previous_fingerprint"] != previous
                or row["previous_fingerprint"] != previous
                or item["patient_id"] != patient_id
                or item["revision"] != row["revision"]
            ):
                raise ValueError("Patient event integrity verification failed")
            previous = row["fingerprint"]
            result.append({**item, "fingerprint": previous})
        return result

    def bind_run(self, run_id, patient_id, revision, request):
        snapshot = self.get(patient_id, revision)
        digest = sha256(request)
        value = {
            "run_id": run_id,
            "patient_id": patient_id,
            "revision": revision,
            "case_fingerprint": snapshot["fingerprint"],
            "request_sha256": digest,
            "request": request,
            "created_at": now(),
        }
        with self.connect() as db:
            existing = db.execute(
                "SELECT envelope FROM patient_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                old = json.loads(existing[0])
                if any(
                    old[k] != value[k]
                    for k in (
                        "patient_id",
                        "revision",
                        "case_fingerprint",
                        "request_sha256",
                    )
                ):
                    raise RevisionConflict(
                        "Run is already bound to a different immutable request"
                    )
                return old
            db.execute(
                "INSERT INTO patient_runs VALUES(?,?,?,?,?)",
                (run_id, patient_id, revision, digest, canonical(value)),
            )
        return value

    def runs(self, patient_id):
        self.get(patient_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM patient_runs WHERE patient_id=? ORDER BY rowid",
                (patient_id,),
            ).fetchall()
        output = []
        for row in rows:
            item = json.loads(row["envelope"])
            if (
                sha256(item["request"]) != row["request_sha256"]
                or item["request_sha256"] != row["request_sha256"]
                or item["run_id"] != row["run_id"]
                or item["patient_id"] != patient_id
                or item["revision"] != row["revision"]
                or self.get(patient_id, item["revision"])["fingerprint"]
                != item["case_fingerprint"]
            ):
                raise ValueError("Patient run integrity verification failed")
            output.append(item)
        return output

    def readiness(self, patient_id, revision=None):
        snapshot = self.get(patient_id, revision)
        case = PatientCase.model_validate(snapshot["case"])
        return {
            "revision": snapshot["revision"],
            "case_fingerprint": snapshot["fingerprint"],
            "ep": assess_patient_case_for_ep(case).model_dump(mode="json"),
            "reviews": [
                e
                for e in self.events(patient_id)
                if e["revision"] == snapshot["revision"] and e["kind"] == "review"
            ],
        }

    def compare(self, patient_id, older, newer):
        a, b = self.get(patient_id, older), self.get(patient_id, newer)
        changes = {}
        for field, key in (
            ("encounters", "encounter_id"),
            ("acquisitions", "acquisition_id"),
            ("model_inputs", "input_id"),
            ("evidence", "evidence_id"),
        ):
            left, right = ({x[key]: x for x in v["case"][field]} for v in (a, b))
            changes[field] = {
                "added": sorted(right.keys() - left.keys()),
                "removed": sorted(left.keys() - right.keys()),
                "changed": sorted(
                    k for k in left.keys() & right.keys() if left[k] != right[k]
                ),
            }
        return {
            "patient_id": patient_id,
            "older": older,
            "newer": newer,
            "changes": changes,
            "interpretation": "Recorded input changes only; not evidence of physiological change or model accuracy",
        }
