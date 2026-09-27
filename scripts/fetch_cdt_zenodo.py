#!/usr/bin/env python3
"""Fetch and validate the published Cardiac-Digital-Twin example dataset.

Zenodo filenames are discovered from the record API rather than guessed. Archives
are extracted with path-traversal protection, and the resulting reference root is
recorded for downstream fixture builders.
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
REQUIRED_DIRS = ("clinical_data", "cellular_data")
GEOMETRY_DIRS = ("geometric_data", "geometric_data_ruben")


def _digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256(path: Path) -> str:
    return _digest(path, "sha256")


def md5(path: Path) -> str:
    # Zenodo exposes MD5 for interoperability; this is not a security primitive.
    return _digest(path, "md5")  # nosec B324


def download(url: str, destination: Path, *, expected_size: int | None = None) -> None:
    """Download with a resumable .part file when the server supports HTTP Range."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(destination) + ".part")
    for _attempt in range(2):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            status = int(getattr(response, "status", 200) or 200)
            if offset and status != 206:
                partial.unlink(missing_ok=True)
                continue
            mode = "ab" if offset else "wb"
            with partial.open(mode) as fh:
                shutil.copyfileobj(response, fh)
        size = partial.stat().st_size
        if expected_size is not None and size != expected_size:
            raise RuntimeError(
                f"Incomplete download for {destination.name}: expected {expected_size} bytes, got {size}"
            )
        partial.replace(destination)
        return
    raise RuntimeError(f"Server did not honor resume request for {destination.name}")


def _safe_member_path(root: Path, member_name: str) -> Path:
    target = (root / member_name).resolve()
    base = root.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise RuntimeError(f"Archive contains unsafe path: {member_name}") from exc
    return target


def _safe_extract_zip(path: Path, root: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            _safe_member_path(root, info.filename)
        archive.extractall(root)


def _safe_extract_tar(path: Path, root: Path) -> None:
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            _safe_member_path(root, member.name)
            if member.issym() or member.islnk():
                raise RuntimeError(f"Archive contains link entry: {member.name}")
        archive.extractall(root)


def maybe_extract(path: Path, root: Path) -> None:
    name = path.name.lower()
    if name.endswith(".zip"):
        _safe_extract_zip(path, root)
    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz")):
        _safe_extract_tar(path, root)


def find_required_root(root: Path) -> Path:
    candidates = [root] + [p for p in root.rglob("clinical_data") if p.is_dir()]
    for clinical in candidates:
        candidate = clinical if clinical.name != "clinical_data" else clinical.parent
        has_geometry = any((candidate / item).is_dir() for item in GEOMETRY_DIRS)
        if has_geometry and (candidate / "cellular_data").is_dir() and (candidate / "clinical_data").is_dir():
            return candidate
    raise RuntimeError(
        "Unable to locate the published CDT input layout. Expected clinical_data, "
        "cellular_data and geometric_data/geometric_data_ruben."
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
    downloads.mkdir(exist_ok=True)
    extracts.mkdir(exist_ok=True)
    manifest = []

    for item in files:
        key = item.get("key") or item.get("filename")
        links = item.get("links", {})
        url = links.get("content") or links.get("self")
        if not key or not url:
            raise RuntimeError(f"Malformed Zenodo file entry: {item}")
        destination = downloads / key
        expected_size = int(item["size"]) if item.get("size") is not None else None
        if destination.exists() and expected_size is not None and destination.stat().st_size != expected_size:
            destination.unlink()
        if not destination.exists():
            download(url, destination, expected_size=expected_size)
        actual = sha256(destination)
        expected = item.get("checksum")
        if expected and expected.startswith("md5:"):
            expected_md5 = expected.removeprefix("md5:")
            actual_md5 = md5(destination)
            if actual_md5 != expected_md5:
                destination.unlink(missing_ok=True)
                download(url, destination, expected_size=expected_size)
                actual = sha256(destination)
                actual_md5 = md5(destination)
                if actual_md5 != expected_md5:
                    raise RuntimeError(f"Checksum mismatch for {key} after clean re-download")
        manifest.append({"key": key, "size": destination.stat().st_size, "sha256": actual, "zenodo_checksum": expected, "url": url})
        maybe_extract(destination, extracts)

    (args.output / "download_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    reference_root = find_required_root(extracts if any(extracts.iterdir()) else downloads)
    (args.output / "reference_root.txt").write_text(str(reference_root.resolve()), encoding="utf-8")
    print(reference_root.resolve())


if __name__ == "__main__":
    main()
