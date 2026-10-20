"""Local patient case lifecycle commands; source files remain on the trusted worker."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
from .patient_store import PatientCaseStore


def main():
    parser = argparse.ArgumentParser(
        description="Version and inspect research patient cases"
    )
    parser.add_argument(
        "--store", type=Path, default=Path("hearttwin-workspace/patients.sqlite3")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    save = commands.add_parser("save")
    save.add_argument("input", type=Path)
    save.add_argument("--expected-revision", type=int, required=True)
    save.add_argument("--actor", required=True)
    save.add_argument("--reason", required=True)
    for action in ("get", "history", "readiness", "runs"):
        command = commands.add_parser(action)
        command.add_argument("patient_id")
        if action in {"get", "readiness"}:
            command.add_argument("--revision", type=int)
    compare = commands.add_parser("compare")
    compare.add_argument("patient_id")
    compare.add_argument("older", type=int)
    compare.add_argument("newer", type=int)
    backup = commands.add_parser("backup")
    backup.add_argument("output", type=Path)
    args = parser.parse_args()
    store = PatientCaseStore(args.store)
    if args.command == "list":
        result = store.list_cases()
    elif args.command == "save":
        result = store.save(
            json.loads(args.input.read_text()),
            expected_revision=args.expected_revision,
            actor=args.actor,
            reason=args.reason,
        )
    elif args.command == "backup":
        destination = args.output.resolve()
        if destination.exists():
            raise ValueError("Backup destination must be a new file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # SQLite backup API includes committed WAL pages; copying a live DB file does not.
        with store.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        result = {
            "backup": str(destination),
            "scope": "Patient records, reviews and run bindings; numerical artifacts require a separate worker-volume backup",
        }
    elif args.command == "compare":
        result = store.compare(args.patient_id, args.older, args.newer)
    elif args.command == "history":
        result = {
            "revisions": store.history(args.patient_id),
            "events": store.events(args.patient_id),
        }
    elif args.command == "runs":
        result = store.runs(args.patient_id)
    else:
        result = getattr(store, args.command)(args.patient_id, args.revision)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
