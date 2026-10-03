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
GEOMETRY_DIRS = ("geometric_data", "geometric_data_ruben")


def _download_priority(item: dict, layout: str) -> tuple[int, str]:
    """Prefer the smallest plausible input bundle for the requested layout."""
    key = str(item.get("key") or item.get("filename") or "")
    lowered = key.lower()
    is_geometry = any(token in lowered for token in ("geometric", "geometry"))
    is_metadata = "meta_data" in lowered or "metadata" in lowered
    if layout == "geometry":
        if is_geometry:
            return (0, lowered)
        if is_metadata:
            return (1, lowered)
        if any(token in lowered for token in ("clinical", "cellular")):
            return (3, lowered)
        return (2, lowered)
    if is_metadata:
        return (0, lowered)
    if any(token in lowered for token in ("clinical", "geometric", "geometry", "cellular")):
        return (1, lowered)
    return (2, lowered)


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as fh:
            shutil.copyfileobj(response, fh, length=1024 * 1024)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


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


def find_required_root(root: Path, *, layout: str = "full") -> Path:
    if layout not in {"geometry", "full"}:
        raise ValueError("layout must be 'geometry' or 'full'")

    candidates: list[Path] = [root]
    for name in GEOMETRY_DIRS:
        candidates.extend(path.parent for path in root.rglob(name) if path.is_dir())
    candidates.extend(
        path.parent for path in root.rglob("clinical_data") if path.is_dir()
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        has_geometry = any((candidate / item).is_dir() for item in GEOMETRY_DIRS)
        if not has_geometry:
            continue
        if layout == "geometry":
            return candidate
        if (
            (candidate / "cellular_data").is_dir()
            and (candidate / "clinical_data").is_dir()
        ):
            return candidate

    if layout == "geometry":
        raise RuntimeError(
            "Unable to locate the published CDT geometry layout. Expected "
            "geometric_data or geometric_data_ruben."
        )
    raise RuntimeError(
        "Unable to locate the published CDT full input layout. Expected "
        "clinical_data, cellular_data and geometric_data/geometric_data_ruben."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default="14034739")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--layout",
        choices=("geometry", "full"),
        default="full",
        help="Minimum published input layout required by the caller",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    api_url = RECORD_API.format(record_id=args.record)
    with urllib.request.urlopen(api_url, timeout=60) as response:
        record = json.loads(response.read().decode("utf-8"))

    (args.output / "zenodo_record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    files = record.get("files", [])
    if not files:
        raise RuntimeError(f"Zenodo record {args.record} contains no downloadable files")
    files = sorted(files, key=lambda item: _download_priority(item, args.layout))

    downloads = args.output / "downloads"
    extracts = args.output / "extracted"
    downloads.mkdir(exist_ok=True)
    extracts.mkdir(exist_ok=True)
    manifest = []
    reference_root: Path | None = None

    for item in files:
        key = item.get("key") or item.get("filename")
        links = item.get("links", {})
        url = links.get("content") or links.get("self")
        if not key or not url:
            raise RuntimeError(f"Malformed Zenodo file entry: {item}")
        destination = _safe_member_path(downloads, key)
        if not destination.exists():
            download(url, destination)
        actual = file_digest(destination)
        expected = item.get("checksum")
        if expected and expected.startswith("md5:"):
            md5 = file_digest(destination, "md5")  # nosec B303: checksum interoperability, not security
            if md5 != expected.removeprefix("md5:"):
                raise RuntimeError(f"Checksum mismatch for {key}")
        manifest.append(
            {
                "key": key,
                "size": destination.stat().st_size,
                "sha256": actual,
                "zenodo_checksum": expected,
                "url": url,
            }
        )
        maybe_extract(destination, extracts)

        # Stop as soon as the caller's minimum input layout is present. The
        # forward-equivalence gate needs geometry only; full personalization
        # explicitly requests the complete clinical/cellular/geometry layout.
        try:
            reference_root = find_required_root(extracts, layout=args.layout)
        except RuntimeError:
            reference_root = None
        if reference_root is not None:
            break

    (args.output / "download_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    if reference_root is None:
        reference_root = find_required_root(
            extracts if any(extracts.iterdir()) else downloads,
            layout=args.layout,
        )
    (args.output / "reference_root.txt").write_text(str(reference_root.resolve()), encoding="utf-8")
    print(reference_root.resolve())


if __name__ == "__main__":
    main()
