"""Audit the actual P034 files exposed by EDGAR; never invent missing signals."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
from scipy.io import loadmat
from scipy.sparse import csr_matrix

PAGE = "https://edgar.sci.utah.edu/2025/07/10/bratislava_2020_p034/"
FILES = {
    "Documentation-7.zip": "e882818ed0c43361ca1abda6ba7ccf274569eafe51d3a250760cebd77f4bc333",
    "Geometries-7.zip": "c30713723520f18ca74c7fe8b56d9b3fb51ea49a2b575ec02ebfad663aeef3b8",
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def audit(root, output):
    root.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    receipts, archive_lists, geometry = {}, {}, {}
    for name, digest in FILES.items():
        url = "https://edgar.sci.utah.edu/wp-content/uploads/2025/07/" + name
        path = root / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=120) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError("Unexpected EDGAR archive size")
            path.write_bytes(raw)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("EDGAR archive changed: " + name)
        receipts[name] = {"url": url, "sha256": digest, "bytes": len(raw)}
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            files = [f for f in z.infolist() if not f.is_dir() and not f.filename.startswith("__MACOSX/")]
            archive_lists[name] = [{"path": f.filename, "bytes": f.file_size} for f in files]
            if name.startswith("Documentation"):
                readme = z.read("Documentation/P034_read_me2.txt").decode(errors="replace")
                if "CMS (common mode sense)" not in readme or "ZERO not ONE" not in readme:
                    raise ValueError("P034 reference/indexing documentation changed")
                continue
            for item in files:
                if not item.filename.endswith(".mat"):
                    continue
                m = loadmat(io.BytesIO(z.read(item)), simplify_cells=True)
                for label, structure in m.items():
                    if label.startswith("__"):
                        continue
                    if not isinstance(structure, dict) or set(structure) != {"pts", "fac"}:
                        raise ValueError("Unexpected geometry MAT structure")
                    pts = np.asarray(structure["pts"]).T
                    fac = np.asarray(structure["fac"]).T
                    if (pts.ndim != 2 or pts.shape[1] != 3 or not np.isfinite(pts).all()
                            or fac.ndim != 2 or fac.shape[1] != 3 or fac.dtype.kind not in "iu"
                            or fac.min() < 0 or fac.max() >= len(pts)):
                        raise ValueError("Invalid explicitly zero-based P034 surface")
                    geometry[label] = (pts, fac.astype(int))
            electrodes = np.loadtxt(io.BytesIO(z.read("Geometries/electrodesPositions.pts")))
            numbered = np.loadtxt(io.BytesIO(z.read("Geometries/electrodesPos_Numbered.pts")))
            mapping = np.loadtxt(io.BytesIO(z.read("Geometries/electrodesMapping.txt")))
            pacing = np.loadtxt(io.BytesIO(z.read("Geometries/pacingSitePosition.pts")))
    surfaces = {}
    for label, (pts, fac) in geometry.items():
        directed = Counter((int(a), int(b)) for face in fac for a, b in zip(face, np.roll(face, -1)))
        edges = Counter(tuple(sorted((int(a), int(b)))) for face in fac for a, b in zip(face, np.roll(face, -1)))
        tri = pts[fac] - pts.mean(axis=0)
        surfaces[label] = {"vertices": len(pts), "triangles": len(fac), "minimum_face_index": int(fac.min()), "maximum_face_index": int(fac.max()),
            "edges_without_two_faces": sum(n != 2 for n in edges.values()),
            "inconsistent_directed_edges": sum(n != 1 or directed[b, a] != 1 for (a, b), n in directed.items()),
            "duplicate_triangles": len(fac) - len({tuple(sorted(f)) for f in fac}),
            "degenerate_triangles": int((np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) == 0).sum()),
            "signed_volume_native_units_cubed": float(np.einsum("fi,fi->f", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6)}
    if electrodes.shape != (128, 3) or mapping.shape != (128, 3) or numbered.shape != (128, 4) or pacing.shape != (3,):
        raise ValueError("Unexpected P034 electrode/mapping/site shapes")
    if not np.array_equal(numbered[:, 0], np.arange(1, 129)) or not np.allclose(numbered[:, 1:], electrodes, rtol=0, atol=1e-10):
        raise ValueError("Numbered electrode rows disagree with position file")
    pts, fac = geometry["Torso"]
    triangles = mapping[:, 0].astype(int)
    if not np.array_equal(mapping[:, 0], triangles) or triangles.min() < 0 or triangles.max() >= len(fac):
        raise ValueError("P034 triangle mapping is not valid zero-based indexing")
    a, b = mapping[:, 1], mapping[:, 2]
    # Publisher's electrodesMappingPrinciple.jpg uses p2 as vector origin:
    # absolute position = p2 + a*(p1-p2) + b*(p3-p2).
    weights = np.column_stack((a, 1-a-b, b))
    matrix = csr_matrix((weights.ravel(), (np.repeat(np.arange(128), 3), fac[triangles].ravel())), shape=(128, len(pts)))
    residual = np.linalg.norm(matrix @ pts - electrodes, axis=1)
    constant_error = float(np.max(np.abs(matrix @ np.ones(len(pts)) - 1)))
    mapping_report = {"electrodes": 128, "torso_vertices": len(pts), "matrix_shape": list(matrix.shape),
        "formula": "p2 + a*(p1-p2) + b*(p3-p2); weights [a,1-a-b,b] in published triangle order",
        "basis": "Publisher electrodesMappingPrinciple.jpg inspected in user Edge Colab; no fitted permutation or registration",
        "triangle_indexing": "zero-based, explicitly documented", "electrode_labels": "1..128 in numbered positions; no standard V1..V6 correspondence asserted",
        "minimum_barycentric_weight": float(weights.min()), "maximum_barycentric_weight": float(weights.max()),
        "rows_with_negative_weights": int((weights.min(axis=1) < 0).sum()),
        "constant_field_preservation_max_error": constant_error,
        "coordinate_residual_RMS_native_units": float(np.sqrt(np.mean(residual**2))),
        "coordinate_residual_max_native_units": float(residual.max()),
        "coordinate_units": "Unconfirmed in supplied P034 MAT/readme metadata; retain native units, do not silently label mm or convert to metres.",
        "qualification": "Interpolation/geometry consistency, not ECG prediction accuracy."}
    save(output / "EDGAR_source_receipts.json", {"page": PAGE, "files": receipts, "archive_inventory": archive_lists,
        "attribution": "Institute of Measurement Science, Slovak Academy of Sciences and National Institute for Cardiovascular Diseases, Bratislava; Aras et al. 2015, doi:10.1016/j.jelectrocard.2015.08.008; Svehlikova et al. CinC 2019 doi:10.22489/CinC.2019.303; full acknowledgements and additional citations on source page/readme.",
        "rights": "Source files are not redistributed. No explicit licence identifier was supplied by the inspected P034 archives/page; free registration/download is not proof of unrestricted redistribution."})
    report = {"status": "GEOMETRY_AUDITED_SIGNALS_NOT_AVAILABLE", "subject_id": "EDGAR-Bratislava-P034", "surfaces": surfaces,
        "electrode_mapping": mapping_report, "pacing_site_coordinates_supplied": True,
        "electrical_data": {"documented_channels": 131, "documented_torso_channels": 128, "documented_limb_signals": ["R", "L", "F"],
            "documented_sampling_Hz": 1000, "documented_voltage_unit": "mV", "documented_reference": "CMS (common mode sense), explicitly NOT WCT",
            "documented_runs": ["RV apex pacing at 100 bpm", "Spontaneous rhythm/PVCs"],
            "downloaded_waveform_records": 0, "electrical_prediction_metrics": None},
        "blockers": ["Signed-in P034 page lists only Documentation and Meshes; both archives were downloaded and contain no Run1/Run2 waveform arrays.",
            "No tetrahedral torso/myocardium conductivity model: supplied MAT files contain surface pts/fac only. They are not directly compatible with native reciprocal P1 volume-conductor FEM.",
            "Native coordinate unit, channel-to-electrode alignment, CMS-to-required reference handling and physiological parameters need explicit qualification before an electrical run.",
            "CT identifies the pacing site according to readme; do not assume all geometry originated from MRI based on the broader catalogue description.",
            "Run2 PVC ground truth is verbal description and a CARTO picture, not a precise numeric ablation target; do not manufacture a localisation-distance metric.",
            "No cine motion, chamber pressures, individual ventricular fibre field, or paired mechanics reference was present in the two downloaded archives."],
        "native_EP_validation": "NOT_RUN", "clinical_qualification": "NOT_ESTABLISHED",
        "next": "Obtain the missing Run1/Run2 waveform files from maintainers (do not infer unlisted download URLs), confirm units and montage, then freeze beat/site/channel splits before fitting.",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    save(output / "EDGAR_status.json", report)
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.data_dir, args.output)


if __name__ == "__main__":
    main()
