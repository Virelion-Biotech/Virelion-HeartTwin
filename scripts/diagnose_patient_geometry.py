"""Diagnose publisher geometry versus reader/stack conventions; retain candidates separately."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import meshio
import numpy as np
from scipy.io import loadmat

from hearttwin.patient_geometry_audit import exact_surface_candidate, rounded_frame_candidate


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-repo", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    candidates = args.output / "candidates"
    candidates.mkdir(exist_ok=True)
    kit = module_from(args.baseline_repo / "scripts/benchmark_kit_edgar.py", "kit_data_contract")
    sources = kit.acquire(args.data_dir)
    save(args.output / "KIT_geometry_source_receipts.json", sources)
    reader = {"source_sha256": sources["files"]["M.vtu"]["sha256"]}
    try:
        original = meshio.read(args.data_dir / "M.vtu")
        reader["meshio_original"] = {"status": "read", "points": len(original.points)}
    except Exception as error:
        reader["meshio_original"] = {"status": "failed", "type": type(error).__name__, "message": str(error)}

    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader, vtkXMLUnstructuredGridWriter
    from vtkmodules.util.numpy_support import vtk_to_numpy

    vtk_reader = vtkXMLUnstructuredGridReader()
    vtk_reader.SetFileName(str(args.data_dir / "M.vtu"))
    vtk_reader.Update()
    grid = vtk_reader.GetOutput()
    if grid.GetNumberOfPoints() != 32505 or grid.GetNumberOfCells() != 128976:
        raise ValueError("Independent VTK reader did not recover the expected complete mesh")
    points = vtk_to_numpy(grid.GetPoints().GetData()).copy()
    connectivity = vtk_to_numpy(grid.GetCells().GetConnectivityArray()).reshape(-1, 4).copy()
    canonical = candidates / "KIT-canonical-ascii.vtu"
    writer = vtkXMLUnstructuredGridWriter()
    writer.SetFileName(str(canonical))
    writer.SetInputData(grid)
    writer.SetDataModeToAscii()
    if writer.Write() != 1:
        raise RuntimeError("Canonical VTK export failed")
    compatible = meshio.read(canonical)
    np.testing.assert_allclose(compatible.points, points, rtol=0, atol=1e-12)
    np.testing.assert_array_equal(compatible.cells_dict["tetra"], connectivity)
    with zipfile.ZipFile(args.data_dir / "TetGen.zip") as z:
        tet_nodes = np.loadtxt(io.BytesIO(z.read("H/heartT4.node")), skiprows=1)
        tet_elements = np.loadtxt(io.BytesIO(z.read("H/heartT4.ele")), skiprows=1)
        bases = np.loadtxt(io.BytesIO(z.read("H/heartT4.bases")), skiprows=1)
    reader.update({
        "VTK_reader": {"status": "read", "points": len(points), "tetrahedra": len(connectivity)},
        "canonical_meshio_read": "PASS; point values and tetrahedral connectivity preserved",
        "canonical_sha256": hashlib.sha256(canonical.read_bytes()).hexdigest(),
        "VTK_vs_TetGen_max_same_index_coordinate_error_mm": float(np.max(np.abs(points - tet_nodes[:, 1:4]))),
        "VTK_vs_TetGen_equal_tetrahedral_indices": bool(np.array_equal(connectivity, tet_elements[:, 1:5].astype(int) - 1)),
        "diagnosis": "Publisher data is readable by VTK; failure in meshio's compressed appended encoding path is an I/O compatibility problem, not proof of corrupt anatomy.",
        "remedy": "Use official TetGen or this lossless VTK canonical export for meshio; retain both source hashes. This does not bypass native solver size limits.",
    })
    save(args.output / "KIT_vtu_reader_diagnosis.json", reader)
    f, s, n = bases[:, 1:4], bases[:, 4:7], bases[:, 7:10]
    raw_sign = np.einsum("ij,ij->i", np.cross(f, s), n)
    frame = rounded_frame_candidate(f, s, normal=n, max_angular_correction_rad=2e-5)
    frame_file = candidates / "KIT-rounded-frame-candidate.json"
    save(frame_file, frame)
    frame_report = {k: v for k, v in frame.items() if k not in {"fiber", "sheet", "normal"}}
    frame_report.update({
        "candidate_sha256": hashlib.sha256(frame_file.read_bytes()).hexdigest(),
        "source_bases_sha256": hashlib.sha256(bases.tobytes()).hexdigest(),
        "original_normal_sign_counts": {"positive": int((raw_sign > 0).sum()), "negative": int((raw_sign < 0).sum()), "zero": int((raw_sign == 0).sum())},
        "diagnosis": "Published finite-precision directions do not exactly meet the native orthogonality contract. Fibre/sheet projection is bounded explicitly; normal handedness is preserved rather than forced to another convention.",
        "native_size_gate": {"nodes": 32505, "node_limit": 5000, "tetrahedra": 128976, "element_limit": 20000,
                             "status": "CAPACITY_LIMIT_UNCHANGED"},
        "scientific_blockers": ["reference-state and boundary qualification", "cavity/material mapping", "activation and patient circulation measurements", "mesh-convergence and scalable solve validation"],
    })
    save(args.output / "KIT_frame_diagnosis.json", frame_report)
    print("KIT_GEOMETRY", json.dumps({"reader": reader, "frame": frame_report}), flush=True)

    edgar = module_from(args.baseline_repo / "scripts/benchmark_edgar_p034.py", "edgar_data_contract")
    original_dir = args.output / "edgar_original"
    original_dir.mkdir(exist_ok=True)
    original = edgar.audit(args.data_dir / "edgar", original_dir)
    repairs = {}
    with zipfile.ZipFile(args.data_dir / "edgar/Geometries-7.zip") as z:
        for name in z.namelist():
            if not name.startswith("Geometries/") or not name.endswith(".mat"):
                continue
            payload = loadmat(io.BytesIO(z.read(name)), simplify_cells=True)
            for label, surface in payload.items():
                if label.startswith("__"):
                    continue
                result = exact_surface_candidate(np.asarray(surface["pts"]).T, np.asarray(surface["fac"]).T)
                path = candidates / ("EDGAR-" + label + "-candidate.json")
                save(path, result)
                repairs[label] = {k: v for k, v in result.items() if k not in {"points", "faces", "old_to_new_node", "old_to_new_face"}}
                repairs[label]["candidate_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        "status": "CANDIDATES_CREATED_REVIEW_PENDING", "surfaces": repairs,
        "all_candidates_closed_oriented_nondegenerate": all(r["after"]["closed_oriented_nondegenerate"] for r in repairs.values()),
        "original_source_modified": False, "waveform_records": original["electrical_data"]["downloaded_waveform_records"],
        "electrical_prediction": "NOT_RUN_MISSING_WAVEFORMS",
        "candidate_policy": "Exact coordinate welding and zero-area removal only. No displacement, tolerance welding, arbitrary nonzero face deletion, hole filling or normal flips.",
        "remaining": ["expert review", "self-intersection/nesting checks", "coordinate units/frame", "volume conductor and conductivity qualification", "missing Run1/Run2 waveforms and reference metadata"],
        "redistribution": "Raw and derived coordinate candidates stay in the user's Colab; retain only audit statistics and hashes in the report.",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    save(args.output / "EDGAR_repair_diagnosis.json", report)
    print("EDGAR_REPAIR", json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
