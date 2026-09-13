#!/usr/bin/env python3
"""Fetch and validate the published Cardiac-Digital-Twin example dataset.

The Zenodo record is queried at runtime so filenames are not guessed or hard-coded.
The script downloads every record file, optionally extracts archives, and verifies the
input layout expected by the pinned upstream workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

RECORD_API = "https://zenodo.org/api/records/{record_id}"
REQUIRED_DIRS = ("clinical_data", "geometric_data", "cellular_data")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as fh:
        shutil.copyfileobj(response, fh)


def maybe_extract(path: Path, root: Path) -> None:
    name = path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            archive.extractall(root)
    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz")):
        with tarfile.open(path) as archive:
            archive.extractall(root)


def find_required_root(root: Path) -> Path:
    direct = root
    if all((direct / item).is_dir() for item in REQUIRED_DIRS):
        return direct
    candidates = [p for p in root.rglob("clinical_data") if p.is_dir()]
    for clinical in candidates:
        candidate = clinical.parent
        if all((candidate / item).is_dir() for item in REQUIRED_DIRS):
            return candidate
    raise RuntimeError(
        "Unable to locate the published CDT input layout. Expected directories: "
        + ", ".join(REQUIRED_DIRS)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default="14034739")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    api_url = RECORD_API.format(record_id=args.record)
    with urllib.request.urlopen(api_url, timeout=60) as response:
        record = json.loads(response.read().decode("utf-8"))

    (args.output / "zenodo_record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    files = record.get("files", [])
    if not files:
        raise RuntimeError(f"Zenodo record {args.record} contains no downloadable files")

    downloads = args.output / "downloads"
    extracts = args.output / "extracted"
    extracts.mkdir(exist_ok=True)
    manifest = []

    for item in files:
        key = item.get("key") or item.get("filename")
        links = item.get("links", {})
        url = links.get("content") or links.get("self")
        if not key or not url:
            raise RuntimeError(f"Malformed Zenodo file entry: {item}")
        destination = downloads / key
        if not destination.exists():
            download(url, destination)
        manifest.append({
            "key": key,
            "size": destination.stat().st_size,
            "sha256": sha256(destination),
            "url": url,
        })
        maybe_extract(destination, extracts)

    (args.output / "download_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    reference_root = find_required_root(extracts if any(extracts.iterdir()) else downloads)
    (args.output / "reference_root.txt").write_text(str(reference_root.resolve()), encoding="utf-8")
    print(reference_root.resolve())


if __name__ == "__main__":
    main()
