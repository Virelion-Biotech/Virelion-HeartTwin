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
    hospital = commands.add_parser("hospital-import")
    hospital.add_argument("patient_id")
    hospital.add_argument("mapping", type=Path)
    hospital.add_argument("--bundle", type=Path)
    hospital.add_argument("--url")
    hospital.add_argument("--token-env")
    hospital.add_argument("--output-dir", type=Path, required=True)
    hospital.add_argument("--expected-revision", type=int, required=True)
    hospital.add_argument("--actor", required=True)
    imaging = commands.add_parser("hospital-images")
    imaging.add_argument("patient_id")
    imaging.add_argument("config", type=Path)
    imaging.add_argument("--output-dir", type=Path, required=True)
    imaging.add_argument("--expected-revision", type=int, required=True)
    imaging.add_argument("--actor", required=True)
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
    elif args.command == "hospital-images":
        from .patient_dicomweb import import_hospital_images

        snapshot = store.get(args.patient_id, args.expected_revision)
        case, report = import_hospital_images(snapshot["case"], json.loads(args.config.read_text()), output_dir=args.output_dir)
        result = {"snapshot": store.save(case, expected_revision=args.expected_revision, actor=args.actor,
                                         reason="Import configured hospital imaging acquisition"), "import": report}
    elif args.command == "hospital-import":
        import hashlib
        from .patient_hospital import fetch_fhir_bundle, import_fhir_bundle

        if bool(args.bundle) == bool(args.url):
            raise ValueError("Choose exactly one hospital Bundle file or HTTPS URL")
        snapshot = store.get(args.patient_id, args.expected_revision)
        if args.url:
            source = fetch_fhir_bundle(args.url, output_dir=args.output_dir, token_env=args.token_env)
        else:
            path = args.bundle.resolve()
            source = {"file_id": "fhir-bundle", "uri": path.as_uri(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        case, report = import_fhir_bundle(snapshot["case"], source, json.loads(args.mapping.read_text()), output_dir=args.output_dir)
        result = {"import": report, "snapshot": snapshot}
        if report["imported"]:
            result["snapshot"] = store.save(case, expected_revision=args.expected_revision, actor=args.actor, reason="Reviewed FHIR observation import")
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
