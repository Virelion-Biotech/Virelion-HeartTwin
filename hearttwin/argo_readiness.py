"""Resumable ARGO ingestion audit. This is not LAT or patient-twin validation."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import time
import traceback
import urllib.request
from pathlib import Path, PurePosixPath

import numpy as np

from . import argo_validation as native

BASE = "https://physionet.org/files/argo/1.0.0/"
MANIFEST_SHA256 = "0bc4fd8d597951b992249a1be080365ecefb41582f87108dc933d3bade808141"
EXTENSIONS = (
    "hea",
    "dat",
    "annotation_ann1",
    "annotation_ann2",
    "annotation_ann3",
    "annotation_consensus",
)
METADATA = (
    "XYZmesh.txt",
    "ConnectivityList.txt",
    "MESHcoloring.txt",
    "POS_POINTS.txt",
    "AblationPoints.txt",
)
GLOBAL_FILES = ("ANNOTATORS", "Additional_subject_data.csv", "RECORDS", "README.txt")
PAGE_SIZE = 16


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(exist_ok=True, parents=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(canonical(value) + b"\n")
    os.replace(temp, path)


class Paused(Exception):
    pass


class Budget:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + max(0, seconds)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise Paused(
                "Session budget reached; reuse this data/output folder on the next runtime"
            )

    def timeout(self):
        self.check()
        return min(30, max(1, self.deadline - time.monotonic()))


def download(path, expected, budget, url=None):
    """Verify cached bytes, resume aligned ranges, and retain partial bytes on interruption."""
    budget.check()
    if path.exists():
        if sha(path) != expected:
            raise ValueError(f"Cached input checksum mismatch: {path.name}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    if part.exists() and sha(part) == expected:
        os.replace(part, path)
        return path
    for attempt in range(4):
        budget.check()
        try:
            offset = part.stat().st_size if part.exists() else 0
            request = urllib.request.Request(
                url, headers={"Range": f"bytes={offset}-"} if offset else {}
            )
            with urllib.request.urlopen(request, timeout=budget.timeout()) as response:
                if response.status == 206:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)",
                        response.headers.get("Content-Range", ""),
                    )
                    if not match or int(match[1]) != offset:
                        raise ValueError("Misaligned HTTP Range response")
                    mode = "ab"
                elif response.status == 200:
                    mode = "wb"
                else:
                    raise ValueError("Unexpected HTTP response status")
                with part.open(mode) as handle:
                    while True:
                        budget.check()
                        block = response.read(65536)
                        if not block:
                            break
                        handle.write(block)
                        handle.flush()
            if sha(part) != expected:
                raise ValueError(f"Downloaded input checksum mismatch: {path.name}")
            os.replace(part, path)
            return path
        except Paused:
            raise
        except Exception:
            if attempt == 3:
                raise
            budget.check()
            time.sleep(min(2**attempt, max(0, budget.deadline - time.monotonic())))
    raise AssertionError("Unreachable")


def manifest_lookup(path):
    if sha(path) != MANIFEST_SHA256:
        raise ValueError("Publisher manifest identity mismatch")
    lookup = {}
    for line in path.read_text().splitlines():
        digest, name = line.split()
        name = name.lstrip("*")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid publisher checksum")
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or name in lookup:
            raise ValueError("Unsafe or duplicate publisher path")
        lookup[name] = digest
    return lookup


def record_inventory(data):
    records = (data / "RECORDS").read_text().split()
    if len(records) != 1962 or len(set(records)) != 1962:
        raise ValueError("Expected 1,962 distinct official records")
    if any(
        not re.fullmatch(r"ARGODataset_Folder/Pt[1-9]/P\d+", name) for name in records
    ):
        raise ValueError("Unexpected record path")
    counts = collections.Counter(PurePosixPath(n).parent.name for n in records)
    if dict(counts) != native._EXPECTED_POINT_COUNTS:
        raise ValueError("Official per-patient record counts differ")
    return records


def annotation_result(ann):
    symbols, notes = list(ann.symbol), list(ann.aux_note)
    samples = np.asarray(ann.sample)
    if samples.ndim != 1 or not np.issubdtype(samples.dtype, np.integer):
        raise ValueError("Annotation samples must be a one-dimensional integer array")
    if len(notes) != len(symbols) or len(samples) != len(symbols):
        raise ValueError("Unexpected ARGO annotation layout")
    labels = set(notes)
    if len(labels) != 1 or not labels.issubset({"A", "P", "U"}):
        raise ValueError("Unknown/inconsistent ARGO class encoding")
    label = next(iter(labels))
    if label == "A":
        if symbols != ["(", ")"] or len(samples) != 2:
            raise ValueError("Unexpected AVP marker encoding")
        on, off = map(int, samples)
        if not 0 <= on < off < 2500:
            raise ValueError("Invalid AVP boundaries")
        bounds = [on, off]
    else:
        # Publisher page specifies one NOTE, not two waveform boundaries.
        # Python WFDB decodes the pinned P71/P1003 note at 9998; the text
        # documents 9999. Preserve either observed/documented sentinel verbatim;
        # neither is a real sample or an activation-time measurement.
        if symbols != ['"'] or len(samples) != 1 or int(samples[0]) not in {9998, 9999}:
            raise ValueError("Unexpected non-AVP sentinel encoding")
        bounds = None
    return {
        "class": label,
        "raw_samples": samples.tolist(),
        "symbols": symbols,
        "avp_bounds_samples": bounds,
        "bounds_applicable": label == "A",
        "independent_LAT_truth": False,
    }


def validate_record(base):
    import wfdb

    direct = native.read_argo_record(base)
    record = wfdb.rdrecord(str(base))
    if record.p_signal.shape != (2500, 15) or record.fs != 1000:
        raise ValueError("Unexpected signal dimensions/sampling")
    if not np.isfinite(record.p_signal).all() or record.units != ["mV"] * 15:
        raise ValueError("Invalid physical signal values/units")
    if list(direct.channel_names) != record.sig_name:
        raise ValueError("Native/WFDB channel identity mismatch")
    error = float(np.max(np.abs(direct.values_mV - record.p_signal)))
    if not np.allclose(direct.values_mV, record.p_signal, atol=1e-12, rtol=1e-12):
        raise ValueError("Native/WFDB physical scaling mismatch")
    anns = {
        ext: annotation_result(wfdb.rdann(str(base), ext)) for ext in EXTENSIONS[2:]
    }
    return {
        "patient": base.parent.name,
        "record": base.name,
        "shape": [2500, 15],
        "fs_hz": 1000,
        "units": "mV",
        "channel_names": record.sig_name,
        "native_wfdb_max_abs_error_mV": error,
        "annotations": anns,
        "reliable_signal_scope": "Only last beat per publisher documentation",
    }


def validate_geometry(root, expected_ids):
    vertices = native._read_xyzmesh(root / "XYZmesh.txt")
    triangles = native._read_connectivity(root / "ConnectivityList.txt", len(vertices))
    voltage, lat = native._read_coloring(root / "MESHcoloring.txt", len(vertices))
    ids, xyz = native._read_positions(root / "POS_POINTS.txt")
    ablations = native._read_csv_rows(root / "AblationPoints.txt", ("X", "Y", "Z"))
    if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError("Mapping-point/record inventory mismatch")
    for row in ablations:
        if not np.isfinite([float(row[x]) for x in ("X", "Y", "Z")]).all():
            raise ValueError("Nonfinite ablation coordinates")
    return {
        "patient": root.name,
        "n_vertices": len(vertices),
        "n_triangles": len(triangles),
        "n_mapping_points": len(xyz),
        "n_ablation_points": len(ablations),
        "n_map_voltage_missing": int(np.isnan(voltage).sum()),
        "n_map_LAT_missing": int(np.isnan(lat).sum()),
        "coordinate_unit": "CARTO_native_UNCONFIRMED",
        "map_LAT_is_interpolated_secondary_target": True,
    }


def save_page(path, identity, rows):
    atomic_json(
        path,
        {
            "identity": identity,
            "records": rows,
            "records_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        },
    )


def load_page(path, identity):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if (
        value["identity"] != identity
        or value["records_sha256"]
        != hashlib.sha256(canonical(value["records"])).hexdigest()
    ):
        raise ValueError("Record checkpoint integrity/identity mismatch")
    return value["records"]


def run(data, output, seconds=6600, max_records=None):
    import wfdb

    budget = Budget(seconds)
    source_files = [Path(__file__), Path(native.__file__)]
    protocol = {
        "schema": "argo-readiness-recovery-v2",
        "manifest_sha256": MANIFEST_SHA256,
        "source_sha256": {p.name: sha(p) for p in source_files},
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "wfdb": wfdb.__version__,
        },
        "scope": "Ingestion, annotation and geometry readiness only; not empirical LAT or twin validation",
    }
    identity = hashlib.sha256(canonical(protocol)).hexdigest()
    old = output / "protocol.json"
    if old.exists() and json.loads(old.read_text()) != protocol:
        raise ValueError(
            "Output protocol/environment differs; choose a new output directory"
        )
    atomic_json(old, protocol)
    rows, geometry = [], {}
    status, error = "RUNNING", None
    try:
        download(
            data / "SHA256SUMS.txt", MANIFEST_SHA256, budget, BASE + "SHA256SUMS.txt"
        )
        lookup = manifest_lookup(data / "SHA256SUMS.txt")

        def fetch(name):
            return download(data / name, lookup[name], budget, BASE + name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch, GLOBAL_FILES))
        records = record_inventory(data)
        expected = set(GLOBAL_FILES)
        expected.update(name + "." + ext for name in records for ext in EXTENSIONS)
        expected.update(
            f"ARGODataset_Folder/{patient}/{name}"
            for patient in native._EXPECTED_POINT_COUNTS
            for name in METADATA
        )
        selected = {
            n
            for n in lookup
            if re.match(r"ARGODataset_Folder/Pt[1-9]/", n) or n in GLOBAL_FILES
        }
        if selected != expected or len(expected) != 11821:
            raise ValueError(
                "Selected publisher files do not match the complete required inventory"
            )
        page_rows, page_path = {}, None
        per_patient_index = collections.Counter()
        for name in records:
            budget.check()
            if max_records is not None and len(rows) >= max_records:
                raise Paused(
                    "Requested smoke-test record limit reached; full audit incomplete"
                )
            patient, record_id = PurePosixPath(name).parts[-2:]
            if patient not in geometry:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    list(
                        pool.map(
                            fetch,
                            [f"ARGODataset_Folder/{patient}/{n}" for n in METADATA],
                        )
                    )
                ids = [
                    PurePosixPath(n).name
                    for n in records
                    if PurePosixPath(n).parent.name == patient
                ]
                geometry[patient] = validate_geometry(
                    data / "ARGODataset_Folder" / patient, ids
                )
                atomic_json(
                    output / "geometry" / (patient + ".json"), geometry[patient]
                )
            inputs = {name + "." + ext: lookup[name + "." + ext] for ext in EXTENSIONS}
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(fetch, inputs))
            target = (
                output
                / "patients"
                / patient
                / f"page-{per_patient_index[patient] // PAGE_SIZE:03d}.json"
            )
            per_patient_index[patient] += 1
            if target != page_path:
                page_rows, page_path = load_page(target, identity), target
            if record_id in page_rows:
                result = page_rows[record_id]
                if (
                    result["input_sha256"] != inputs
                    or result["record"] != record_id
                    or result["patient"] != patient
                ):
                    raise ValueError("Checkpoint record/input binding mismatch")
            else:
                result = validate_record(data / name)
                result["input_sha256"] = inputs
                page_rows[record_id] = result
                save_page(target, identity, page_rows)
            rows.append(result)
            if len(rows) % 10 == 0:
                print(f"Records verified/checkpointed: {len(rows)}/1962", flush=True)
        consensus = collections.Counter(
            r["annotations"]["annotation_consensus"]["class"] for r in rows
        )
        if len(rows) != 1962 or dict(consensus) != {"A": 940, "P": 776, "U": 246}:
            raise ValueError("Official total/consensus distribution mismatch")
        status = "READINESS_ONLY"
    except Paused as paused:
        status, error = "PAUSED", str(paused)
    except Exception:  # noqa: BLE001 -- persist any unexpected failure for Colab ZIP diagnosis
        status, error = "FAILED", traceback.format_exc()
    summary = {
        "status": status,
        "error": error,
        "records_verified": len(rows),
        "patients": dict(collections.Counter(r["patient"] for r in rows)),
        "consensus_class_counts": dict(
            collections.Counter(
                r["annotations"]["annotation_consensus"]["class"] for r in rows
            )
        ),
        "coordinate_unit_confirmed": False,
        "empirical_LAT_accuracy_evaluated": False,
        "paired_twin_qualified": False,
        "clinical_validation": False,
        "limits": [
            "AVP onset/end are not LAT ground truth; U is not physiological.",
            "Only the final beat is documented reliable; coordinate units remain unconfirmed.",
            "Missing paired volumetric scar/fibers, mechanics/hemodynamics and outcome qualification.",
        ],
    }
    atomic_json(output / "summary.json", summary)
    atomic_json(output / "RUN_STATUS.json", summary)
    print(
        json.dumps(
            {k: summary[k] for k in ("status", "error", "records_verified", "patients")}
        ),
        flush=True,
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget-seconds", type=float, default=6600)
    parser.add_argument("--max-records", type=int)
    args = parser.parse_args()
    result = run(args.data, args.output, args.budget_seconds, args.max_records)
    if result["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
