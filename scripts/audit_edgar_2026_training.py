"""Audit public EDGAR 2026 Bratislava training files without inventing metadata.

No challenge testing data, mesh repair, electrical solve, or raw redistribution.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.io import loadmat, whosmat

SOURCES = {
    "geometries": (
        "https://edgar.sci.utah.edu/datasets/2026_ECGI_Challenge/Training_Data/"
        "Bratislava_Data/Geometries/TrainingGeometries.zip",
        "dc112818adfcff90219a5f3886f8ad30f5a4adce542e21f3b89e8ae6845f6faf"),
    "signals": (
        "https://edgar.sci.utah.edu/datasets/2026_ECGI_Challenge/Training_Data/"
        "Bratislava_Data/Signals/AllTrainingSignals.zip",
        "824b4de2785fd8e29786854087f1a4143a1932aa9d71238abbe9be6060474462"),
}


def mesh_audit(record):
    nodes = np.asarray(record.get("node", record.get("pts")), dtype=float)
    faces = np.asarray(record.get("face", record.get("fac")))
    if nodes.ndim != 2 or faces.ndim != 2:
        raise ValueError("Two dimensional surface arrays required")
    if nodes.shape[1] != 3:
        nodes = nodes.T
    if faces.shape[1] != 3:
        faces = faces.T
    if nodes.shape[1] != 3 or faces.shape[1] != 3 or faces.dtype.kind not in "iu":
        raise ValueError("Unrecognized surface shape or face type")
    n = len(nodes)
    lo, hi = int(faces.min()), int(faces.max())
    if lo == 0 and hi < n:
        base = 0
    elif lo >= 1 and hi == n:
        base = 1
    else:
        base = None
    result = {"nodes": n, "faces": len(faces), "coordinate_units": None,
              "finite_coordinates": bool(np.isfinite(nodes).all()),
              "minimum_face_index": lo, "maximum_face_index": hi,
              "index_base_from_range": base}
    if base is None or not np.isfinite(nodes).all():
        result["locally_closed_oriented"] = False
        return result
    f = faces.astype(np.int64) - base
    if np.any(f < 0) or np.any(f >= n):
        raise ValueError("Surface index out of range")
    area2 = np.linalg.norm(np.cross(nodes[f[:, 1]] - nodes[f[:, 0]],
                                   nodes[f[:, 2]] - nodes[f[:, 0]]), axis=1)
    directed = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    edges = np.sort(directed, axis=1)
    unique, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    signs = np.where(directed[:, 0] < directed[:, 1], 1, -1)
    orientation = np.bincount(inverse, weights=signs, minlength=len(unique))
    result.update({
        "zero_area_faces": int(np.sum(area2 == 0)),
        "duplicate_faces": len(f) - len(np.unique(np.sort(f, axis=1), axis=0)),
        "exact_duplicate_vertices": n - len(np.unique(nodes, axis=0)),
        "boundary_edges": int(np.sum(counts == 1)),
        "overincident_edges": int(np.sum(counts > 2)),
        "maximum_edge_incidence": int(counts.max()),
        "two_face_orientation_conflicts": int(np.sum((counts == 2) & (orientation != 0))),
    })
    result["locally_closed_oriented"] = not any(result[k] for k in [
        "zero_area_faces", "duplicate_faces", "boundary_edges",
        "overincident_edges", "two_face_orientation_conflicts"])
    result["self_intersections_and_nesting_checked"] = False
    return result


def main(output):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "purpose": "Training source and integration-contract audit only",
        "challenge_test_data_downloaded": False, "electrical_solve_executed": False,
        "raw_data_redistributed": False,
        "coordinate_or_signal_units_invented": False, "archives": {}, "subjects": {},
        "limitations": [
            "Surface topology checks do not qualify a volume conductor",
            "MATLAB MCOS string notes need an authoritative decoder or provider metadata",
            "No sampling rate, amplitude unit, or reference inferred from numeric ranges",
            "No P034 acquisition identity inferred from the new challenge"],
    }
    archives = {}
    for kind, (url, expected) in SOURCES.items():
        path = out / (kind + ".zip")
        if not path.exists():
            with urllib.request.urlopen(url, timeout=120) as response, path.open("wb") as dest:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise ValueError("Archive exceeds 512 MiB budget")
                    dest.write(chunk)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            raise ValueError(f"Publisher archive changed: {kind}")
        archives[kind] = zipfile.ZipFile(io.BytesIO(raw))
        z = archives[kind]
        if sum(x.file_size for x in z.infolist()) > 1024 ** 3:
            raise ValueError("Archive uncompressed budget exceeded")
        entries = []
        for name in z.namelist():
            item = {"path": name, "bytes": z.getinfo(name).file_size}
            if name.endswith(".mat"):
                item["schema"] = [{"name": k, "shape": list(s), "class": c}
                                  for k, s, c in whosmat(io.BytesIO(z.read(name)))]
            entries.append(item)
        report["archives"][kind] = {"url": url, "sha256": digest, "bytes": len(raw),
                                    "entries": entries}
    subjects = sorted({re.match(r"(P\d+)", n).group(1)
                       for n in archives["signals"].namelist() if re.match(r"P\d+", n)})
    for subject in subjects:
        record = {"signals": {}, "surfaces": {}, "blockers": [], "electrical_solve_ready": False}
        geometry = {}
        for name in archives["geometries"].namelist():
            if not name.startswith(subject + "_"):
                continue
            loaded = loadmat(io.BytesIO(archives["geometries"].read(name)), simplify_cells=True)
            for key, value in loaded.items():
                if key.startswith("__"):
                    continue
                if key == "otherStructures":
                    for structure, surface in value.items():
                        record["surfaces"][structure] = mesh_audit(surface)
                else:
                    geometry[key] = value
                    record["surfaces"][key] = mesh_audit(value)
        for suffix in ["ts", "ecg"]:
            name = f"{subject}-{suffix}.mat"
            loaded = loadmat(io.BytesIO(archives["signals"].read(name)), simplify_cells=True)
            ts = loaded["ts"]
            values = np.asarray(ts["potvals"])
            labels = [str(x) for x in np.asarray(ts.get("leadlabels", [])).ravel()]
            note = np.asarray(ts.get("note", []))
            record["signals"][suffix] = {
                "channels": int(values.shape[0]), "samples": int(values.shape[1]),
                "finite_values": bool(np.isfinite(values).all()), "leadlabels": labels,
                "available_fields": list(ts), "sampling_rate_Hz": None,
                "amplitude_unit": None, "reference": None,
                "note_dtype": str(note.dtype),
                "note_has_opaque_MATLAB_string": bool(note.dtype.names and "s1" in note.dtype.names),
                "ablation_location_shape": list(np.asarray(ts["pacingLoc"]).shape),
            }
        links = np.asarray(geometry["torso"]["leadlinks"])
        n_torso = len(np.asarray(geometry["torso"]["node"]))
        minlink, maxlink = int(links.min()), int(links.max())
        candidates = [b for b in [0, 1] if minlink >= b and maxlink < n_torso + b]
        record["leadlinks"] = {
            "count": int(links.size), "integer": links.dtype.kind in "iu",
            "unique_count": len(np.unique(links)), "minimum": minlink, "maximum": maxlink,
            "range_compatible_index_bases": candidates,
            "signal_channel_count_matches": int(links.size) == record["signals"]["ts"]["channels"],
            "ordering_authority": "2026 challenge page; no independent per-electrode ordering verification",
        }
        record["blockers"] += [
            "Sampling rate, amplitude unit and reference need authoritative decoding/metadata",
            "Only surface meshes supplied; native reciprocal operator needs a qualified tetrahedral volume conductor in metres",
            "128 BSP channels cannot be relabelled as native nine electrodes or twelve ECG leads",
        ]
        if set(record["signals"]["ecg"]["leadlabels"]) != {
            "I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"}:
            record["blockers"].append("ECG file is not a complete standard 12-lead recording; preserve actual labels")
        if len(candidates) != 1:
            record["blockers"].append("leadlinks index base is range-ambiguous; do not guess from other face arrays")
        if not all(x["locally_closed_oriented"] for x in record["surfaces"].values()):
            record["blockers"].append("At least one original surface fails local closed/oriented predicate")
        report["subjects"][subject] = record
    for z in archives.values():
        z.close()
    report["summary"] = {
        "subject_count": len(subjects), "P034_present": "P034" in subjects,
        "BSP_channels": dict(Counter(str(r["signals"]["ts"]["channels"]) for r in report["subjects"].values())),
        "ECG_channels": dict(Counter(str(r["signals"]["ecg"]["channels"]) for r in report["subjects"].values())),
        "electrical_solve_ready_subjects": [],
    }
    (out / "training_contract_audit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))
    for subject, record in report["subjects"].items():
        print(subject, "mesh_failures", [k for k, v in record["surfaces"].items()
                                      if not v["locally_closed_oriented"]],
              "leadlink_bases", record["leadlinks"]["range_compatible_index_bases"])
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    main(parser.parse_args().output)
