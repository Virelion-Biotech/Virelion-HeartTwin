"""Real-data KIT audit and exploratory native circulation benchmark.

Run in Colab. EDGAR requires an independently authenticated download; this runner
does not access private sessions, create an account, or substitute synthetic data.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import inspect
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.request
import zipfile

import numpy as np
from scipy.optimize import least_squares

from hearttwin import patient_closed_loop
from hearttwin.patient_observations import normalize_observation

RECORD = "https://zenodo.org/records/10526554"
FILES = {
    "Data.zip": "d36dd94650477432e2f1ee19c50b189a",
    "LabelIDs.txt": "9cf108dcd68fc97e76f51eaf86212a9a",
    "M.vtu": "d2c9a077ef355b985d6dcb3740eb493a",
    "Surfaces.zip": "c9fa25053693429ffe9f6d315f300a08",
    "TetGen.zip": "19275432fa12eacbca43a2d8053be98e",
}
SUBJECT = "KIT-Gerach-10526554-volunteer"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch(url, dest):
    if not dest.exists():
        with urllib.request.urlopen(url, timeout=180) as response:
            raw = response.read(30_000_001)
        if len(raw) > 30_000_000:
            raise ValueError("Unexpectedly large public download")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return dest.read_bytes()


def acquire(root):
    raw = fetch("https://zenodo.org/api/records/10526554", root / "zenodo_record.json")
    metadata = json.loads(raw)
    if metadata["id"] != 10526554:
        raise ValueError("Unexpected Zenodo record")
    inventory = {f["key"]: f for f in metadata["files"]}
    receipts = {}
    for name, md5 in FILES.items():
        item = inventory[name]
        if item["checksum"] != "md5:" + md5:
            raise ValueError("Pinned publisher checksum changed: " + name)
        url = item["links"]["self"]
        if not url.startswith("https://zenodo.org/api/records/10526554/files/"):
            raise ValueError("Unexpected download destination")
        data = fetch(url, root / name)
        if len(data) != item["size"] or hashlib.md5(data).hexdigest() != md5:
            raise ValueError("Publisher checksum/size mismatch: " + name)
        receipts[name] = {"url": url, "bytes": len(data), "publisher_md5": md5, "sha256": sha(data)}
    return {"record": RECORD, "version": metadata["metadata"].get("version"),
            "license": metadata["metadata"].get("license"), "metadata_sha256": sha(raw), "files": receipts,
            "attribution": "Gerach, Schuler, Wachter and Loewe; Zenodo 10526554; Gerach et al., Mathematics 2021, 9, 1247; Gerach and Loewe, J Physiol 2024, JP285022",
            "not_downloaded": ["EP.vtu (640 MB); not needed for this mechanics/circulation audit"],
            "redistribution": "Source files retained in Colab, not redistributed in HeartTwin; CC BY-NC 4.0 is not a blanket commercial-use permission."}


def tables(root, output):
    with zipfile.ZipFile(root / "Data.zip") as z:
        raw = z.read("Data/Volume_LV_RV.txt")
        if raw.decode().splitlines()[0] != "Time[ms]\tEndovol[ml]\tEpivol[ml]\tRVEndovol[ml]\tRVEpivol[ml]":
            raise ValueError("Unexpected KIT volume table header")
        data = np.loadtxt(io.BytesIO(raw), skiprows=1)
        avpd = [line.split("\t") for line in z.read("Data/AVPD.txt").decode().splitlines()[1:] if line.strip()]
        avpv = [line.split("\t") for line in z.read("Data/AVPV.txt").decode().splitlines()[1:] if line.strip()]
        curves = {
            "LV_volume": (data[:, 0] / 1000, data[:, 1], "mL"),
            "LV_epicardial_enclosed_volume": (data[:, 0] / 1000, data[:, 2], "mL"),
            "LV_AVPD": (np.array([float(r[6]) for r in avpd]), np.array([float(r[5]) for r in avpd]), "mm"),
            "RV_AVPD": (np.array([float(r[14]) for r in avpd]), np.array([float(r[13]) for r in avpd]), "mm"),
            "LV_AVPV": (np.array([float(r[2]) for r in avpv]), np.array([float(r[1]) for r in avpv]), "mm/s"),
            "RV_AVPV": (np.array([float(r[6]) for r in avpv]), np.array([float(r[5]) for r in avpv]), "mm/s"),
        }
        for chamber in ["LV", "RV"]:
            if not np.array_equal(curves[chamber + "_AVPD"][0], curves[chamber + "_AVPV"][0]):
                raise ValueError("AVPD/AVPV clocks unexpectedly differ")
        for key, (t, v, unit) in curves.items():
            obs = normalize_observation(json.dumps({"time_s": t.tolist(), "values": v.tolist(), "unit": unit}).encode(), format="json", unit=unit)
            save(output / "observations" / (key + ".json"), obs)
        for name, unit in [("17AHA_WT", "mm"), ("17AHA_fractionalWT", "%")]:
            rows = z.read("Data/" + name + ".txt").decode().splitlines()
            labels = [x.strip() for x in rows[1].split("\t") if x.strip()]
            values = [float(x) for x in rows[2].split("\t") if x.strip()]
            obs = normalize_observation(json.dumps({"labels": labels, "values": values, "unit": unit}).encode(), format="json", unit=unit)
            save(output / "observations" / (name + ".json"), obs)
    if data.shape != (35, 5) or not np.isfinite(data[:, :3]).all():
        raise ValueError("Unexpected KIT finite volume samples")
    rv_mask = np.isfinite(data[:, 3])
    if np.flatnonzero(rv_mask).tolist() != [0, 10] or np.isfinite(data[:, 4]).any():
        raise ValueError("Unexpected RV missingness pattern")
    save(output / "observations/RV_volume_sparse.json", {"time_s": (data[rv_mask, 0] / 1000).tolist(), "values": data[rv_mask, 3].tolist(), "unit": "mL"})
    t, v = curves["LV_volume"][:2]
    train = np.arange(23)
    hold = np.arange(23, 35)
    for name, indices in [("LV_calibration", train), ("LV_temporal_holdout", hold)]:
        save(output / "observations" / (name + ".json"), {"time_s": t[indices].tolist(), "values": v[indices].tolist(), "unit": "mL"})
    return {"sample_counts": {k: len(v[0]) for k, v in curves.items()}, "RV_volume_samples": int(rv_mask.sum()),
            "RV_epicardial_volume_samples": 0, "time_windows_s": {k: [float(v[0][0]), float(v[0][-1])] for k, v in curves.items()},
            "LV_observed_range_mL": [float(v.min()), float(v.max())],
            "LV_first_frame_volume_mL": float(v[0]), "RV_first_frame_volume_mL": float(data[0, 3]),
            "clock_policy": "Preserve independent measured time grids; no phase shifting or time warping. Same volunteer does not establish simultaneous acquisition.",
            "independence": "AVPV is a related motion derivative; WT and fractional WT share source measurements. They are not independent validation experiments."}


def geometry_audit(root, output):
    import meshio
    from cardimech import fem_mesh
    with zipfile.ZipFile(root / "TetGen.zip") as z:
        node = np.loadtxt(io.BytesIO(z.read("H/heartT4.node")), skiprows=1)
        ele = np.loadtxt(io.BytesIO(z.read("H/heartT4.ele")), skiprows=1)
        bases = np.loadtxt(io.BytesIO(z.read("H/heartT4.bases")), skiprows=1)
    for array in [node, ele, bases]:
        if not np.array_equal(array[:, 0], np.arange(1, len(array) + 1)):
            raise ValueError("TetGen IDs are not consecutive one-based rows")
    points = node[:, 1:4] / 1000
    tet = ele[:, 1:5].astype(int) - 1
    if not np.array_equal(ele[:, 1:5], tet + 1) or tet.min() < 0 or tet.max() >= len(points):
        raise ValueError("Invalid TetGen connectivity")
    det = np.linalg.det((points[tet[:, 1:]] - points[tet[:, 0, None]]).transpose(0, 2, 1))
    f, s = bases[:, 1:4], bases[:, 4:7]
    report = {"source": "TetGen.zip/H/heartT4.*; source millimetres converted explicitly to metres",
              "nodes": len(points), "tetrahedra": len(tet), "positive_tetrahedra": int((det > 0).sum()),
              "nonpositive_tetrahedra": int((det <= 0).sum()), "minimum_determinant_m3": float(det.min()),
              "absolute_tetrahedral_volume_mL": float(np.abs(det).sum() / 6 * 1e6),
              "material_counts": {str(int(k)): int(v) for k, v in zip(*np.unique(ele[:, 5], return_counts=True))},
              "fiber_norm_range": [float(np.linalg.norm(f, axis=1).min()), float(np.linalg.norm(f, axis=1).max())],
              "sheet_norm_range": [float(np.linalg.norm(s, axis=1).min()), float(np.linalg.norm(s, axis=1).max())],
              "max_abs_normalized_fiber_sheet_dot": float(np.max(np.abs(np.einsum("ij,ij->i", f, s) / np.linalg.norm(f, axis=1) / np.linalg.norm(s, axis=1)))),
              "surfaces": {}, "meshio_vtu": "meshio 5.3.5 failed reading this publisher M.vtu with a compressed-base64 error; use the accompanying official TetGen export. No source geometry repairs performed."}
    with zipfile.ZipFile(root / "Surfaces.zip") as z, tempfile.TemporaryDirectory() as temp:
        for chamber in ["LA", "LV", "RA", "RV"]:
            path = Path(temp) / (chamber + ".stl")
            path.write_bytes(z.read("Surfaces/cavity" + chamber + ".stl"))
            mesh = meshio.read(path)
            faces = mesh.cells_dict["triangle"]
            xyz = mesh.points
            edges = Counter(tuple(sorted((int(a), int(b)))) for face in faces for a, b in zip(face, np.roll(face, -1)))
            directed = Counter((int(a), int(b)) for face in faces for a, b in zip(face, np.roll(face, -1)))
            tri = xyz[faces] - xyz.mean(axis=0)
            volume = float(np.einsum("fi,fi->f", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6000)
            report["surfaces"][chamber] = {"nodes": len(xyz), "triangles": len(faces), "signed_volume_mL": volume,
                "edges_without_two_faces": sum(n != 2 for n in edges.values()),
                "inconsistent_directed_edges": sum(n != 1 or directed[b, a] != 1 for (a, b), n in directed.items()),
                "qualification": "Geometric audit only; no cavity-to-wall registration or expert review asserted."}
    # Exercise the installed mechanics contract rather than merely assuming its limits.
    with tempfile.TemporaryDirectory() as temp:
        candidate = Path(temp) / "source-size-preflight.json"
        candidate.write_text(json.dumps({"subject_id": SUBJECT, "units": "m", "nodes": points.tolist(),
                                       "tetrahedra": tet.tolist(), "cavity_faces": []}))
        try:
            fem_mesh.load_mesh(SimpleNamespace(kind="tetra_mechanics_mesh", uri=candidate.as_uri(), sha256=sha(candidate.read_bytes()), metadata={}), SUBJECT)
        except ValueError as error:
            report["native_mesh_preflight"] = {"status": "REJECTED", "error": str(error),
                "scope": "Source-size preflight only, empty cavity placeholder; not a complete four-cavity conversion."}
        else:
            raise RuntimeError("Expected current native mesh size guard was not exercised")
    report["full_spatial_benchmark"] = {"status": "NOT_RUN", "blockers": [
        "Published mesh exceeds native 5000-node/20000-tetrahedron limits; guarded loader rejects it.",
        "Need reviewed cavity/wall/material/coordinate mapping, fibre/sheet qualification, constitutive parameters, loaded reference state and boundary conditions.",
        "No measured patient voltage field, pressures or complete circulation initial state supplied.",
        "No mesh coarsening, fibre orthogonalisation or physiology defaults silently substituted."]}
    report["native_loader_sha256"] = sha(Path(inspect.getfile(fem_mesh)).read_bytes())
    save(output / "KIT_geometry_audit.json", report)
    return report


def initial_model(audit, output):
    compartments = {}
    for name, emin, emax, start, duration, v0 in [
        ("LA", .15, .35, 1.05, .25, 10.), ("LV", .05, 2., 0., .5, 10.),
        ("RA", .10, .25, 1.05, .25, 10.), ("RV", .03, .5, 0., .5, 10.)]:
        compartments[name] = {"unstressed_volume_mL": v0, "external_pressure_mmHg": 0., "emin_mmHg_per_mL": emin,
                              "emax_mmHg_per_mL": emax, "activation_start_s": start, "activation_duration_s": duration}
    for name, compliance, v0 in [("SA", 1.5, 10.), ("SV", 30., 2600.), ("PA", 5., 10.), ("PV", 10., 300.)]:
        compartments[name] = {"unstressed_volume_mL": v0, "external_pressure_mmHg": 0., "compliance_mL_per_mmHg": compliance}
    return {"subject_id": SUBJECT, "compartments": compartments, "cycle_length_s": 1.3,
            "initial_volumes_mL": [60., audit["LV_first_frame_volume_mL"], 150., 2800., 60., audit["RV_first_frame_volume_mL"], 150., 400.],
            "edge_resistance_mmHg_s_per_mL": [.01, .01, 1., .05, .01, .01, .1, .02],
            "time_s": [0., .1], "solver": {"rtol": 1e-6, "atol_mL": 1e-7, "max_step_s": .003}, "output_dir": str(output)}


def score(y, prediction):
    error = np.asarray(prediction) - np.asarray(y)
    return {"n": len(error), "RMSE_mL": float(np.sqrt(np.mean(error**2))), "MAE_mL": float(np.mean(np.abs(error))), "maximum_absolute_error_mL": float(np.abs(error).max()), "bias_mL": float(error.mean())}


def circulation_benchmark(output, audit):
    train = json.loads((output / "observations/LV_calibration.json").read_text())
    # Holdout values are not opened here until frozen predictions have been saved.
    times = np.loadtxt(output / "schedule.txt")
    protocol = {"status": "FROZEN_BEFORE_OPTIMIZATION", "kind": "Exploratory conditional native 0D fit; not spatial mechanics validation",
        "calibration_indices": list(range(23)), "temporal_holdout_indices": list(range(23, 35)),
        "additional_target": "RV volume at 0.360882353 s; first RV volume is an initial condition, not validation",
        "parameters": ["LV_emax_mmHg_per_mL", "LV_activation_duration_s", "LA_to_LV_resistance_mmHg_s_per_mL"],
        "bounds": [[.1, .25, .001], [8., .7, .2]], "starts": [[2., .5, .01], [.8, .4, .03]],
        "selection": "lowest calibration SSE only; no selection or retuning on holdout",
        "max_nfev_per_start": 45, "clinical_acceptance_threshold": None,
        "disclosure": "Retrospective split after raw source inspection; not preregistered or prospectively blinded. Samples share one volunteer/acquisition and are correlated.",
        "assumptions": "All base model values except initial LV/RV volume and the three fitted parameters are analyst assumptions, not patient measurements. Cycle length 1.3 s is an assumption. No burn-in or steady-state assertion. No measured pressure/flow/ECG used.",
        "leakage_scope": "Standalone exploratory runner, not a passed PatientCase evidence gate: train/holdout derive from one publisher archive, which strict shared-source gates would reject without an explicit reviewed research partition."}
    save(output / "protocol.json", protocol)
    base = initial_model(audit, output)
    save(output / "assumed_base_model.json", base)
    def forward(x, schedule, destination, tight=False):
        p = deepcopy(base)
        p["compartments"]["LV"]["emax_mmHg_per_mL"] = float(x[0])
        p["compartments"]["LV"]["activation_duration_s"] = float(x[1])
        p["edge_resistance_mmHg_s_per_mL"][0] = float(x[2])
        p["time_s"] = list(map(float, schedule))
        p["output_dir"] = str(destination)
        if tight:
            p["solver"] = {"rtol": 1e-8, "atol_mL": 1e-9, "max_step_s": .0015}
        return patient_closed_loop.simulate_closed_loop(p), p
    runs = []
    with tempfile.TemporaryDirectory() as temp:
        for start in protocol["starts"]:
            fit = least_squares(lambda x: np.asarray(forward(x, train["time_s"], temp)[0]["volume_mL"]["LV"]) - train["values"],
                                start, bounds=protocol["bounds"], max_nfev=45, diff_step=1e-3,
                                ftol=1e-5, xtol=1e-5, gtol=1e-5, x_scale="jac")
            runs.append({"start": start, "x": fit.x.tolist(), "calibration_SSE_mL2": float(2 * fit.cost), "nfev": fit.nfev,
                         "converged": bool(fit.success), "message": fit.message, "optimality": float(fit.optimality)})
            print("FIT", json.dumps(runs[-1]), flush=True)
    selected = min(runs, key=lambda r: r["calibration_SSE_mL2"])
    save(output / "fit_selection.json", {"runs": runs, "selected": selected})
    prediction, fitted = forward(selected["x"], times, output / "native_artifacts")
    save(output / "frozen_prediction.json", prediction)
    prediction_sha = sha((output / "frozen_prediction.json").read_bytes())
    save(output / "fitted_model.json", fitted)
    holdout = json.loads((output / "observations/LV_temporal_holdout.json").read_text())
    rv = json.loads((output / "observations/RV_volume_sparse.json").read_text())
    pred = np.asarray(prediction["volume_mL"]["LV"])
    train_count = len(train["values"])
    rv_prediction = float(np.interp(rv["time_s"][1], prediction["time_s"], prediction["volume_mL"]["RV"]))
    checks = []
    for scale in [.5, 2.]:
        p = deepcopy(fitted)
        p["output_dir"] = str(output / "native_artifacts")
        p["edge_resistance_mmHg_s_per_mL"] = (np.asarray(p["edge_resistance_mmHg_s_per_mL"]) * scale).tolist()
        for part in p["compartments"].values():
            part["external_pressure_mmHg"] *= scale
            if "emin_mmHg_per_mL" in part:
                part["emin_mmHg_per_mL"] *= scale
                part["emax_mmHg_per_mL"] *= scale
            else:
                part["compliance_mL_per_mmHg"] /= scale
        alternative = patient_closed_loop.simulate_closed_loop(p)
        maximum = max(float(np.max(np.abs(np.asarray(alternative["volume_mL"][c]) - prediction["volume_mL"][c]))) for c in patient_closed_loop.COMPARTMENTS)
        pressure_error = max(float(np.max(np.abs(np.asarray(alternative["pressure_mmHg"][c]) - scale * np.asarray(prediction["pressure_mmHg"][c])))) for c in patient_closed_loop.COMPARTMENTS)
        checks.append({"pressure_scale": scale, "max_volume_change_mL": maximum, "max_pressure_scaling_error_mmHg": pressure_error,
                       "LV_peak_pressure_mmHg": float(max(alternative["pressure_mmHg"]["LV"]))})
    tight, _ = forward(selected["x"], times, output / "native_artifacts", tight=True)
    report = {"status": "COMPLETED_EXPLORATORY", "frozen_prediction_sha256": prediction_sha,
        "calibration": score(train["values"], pred[:train_count]), "temporal_holdout": score(holdout["values"], pred[train_count:]),
        "persistence_baseline_holdout": score(holdout["values"], [train["values"][-1]] * len(holdout["values"])),
        "RV_ES_holdout": {"time_s": rv["time_s"][1], "observed_mL": rv["values"][1], "predicted_mL": rv_prediction, "absolute_error_mL": abs(rv_prediction - rv["values"][1])},
        "volume_conservation_drift_mL": prediction["maximum_volume_drift_mL"], "pressure_scaling_nonidentifiability": checks,
        "tolerance_refinement_max_LV_difference_mL": float(np.max(np.abs(np.asarray(tight["volume_mL"]["LV"]) - pred))),
        "interpretation": "Volume-only fitting cannot identify absolute pressure, resistance and elastance scale. Conditional fit quality does not validate those parameters or a whole-heart twin.",
        "not_predicted": ["AVPD/AVPV", "regional wall thickening", "ECG", "patient pressures", "spatial stress or displacement"],
        "clinical_qualification": "NOT_ESTABLISHED", "mesh_convergence": "NOT_RUN; no spatial solve", "population_uncertainty": "NOT_ESTIMATED; one volunteer, no validated noise model"}
    save(output / "KIT_circulation_results.json", report)
    rows = ["time_s,observed_LV_mL,predicted_LV_mL,partition"]
    all_values = train["values"] + holdout["values"]
    rows += [f"{t},{y},{p},{'calibration' if i < train_count else 'temporal_holdout'}" for i, (t, y, p) in enumerate(zip(times, all_values, pred))]
    (output / "LV_predictions.csv").write_text("\n".join(rows) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    save(args.output / "runner_provenance.json", {"runner_sha256": sha(Path(__file__).read_bytes()),
        "python": sys.version, "platform": platform.platform(), "native_circulation_sha256": sha(Path(inspect.getfile(patient_closed_loop)).read_bytes()),
        "execution": "User Edge Colab; source/benchmark pins recorded by notebook"})
    (args.output / "environment.txt").write_text(subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True))
    acquisition = acquire(args.data_dir)
    save(args.output / "KIT_source_receipts.json", acquisition)
    audit = tables(args.data_dir, args.output)
    save(args.output / "KIT_data_audit.json", audit)
    with zipfile.ZipFile(args.data_dir / "Data.zip") as z:
        schedule = np.loadtxt(io.BytesIO(z.read("Data/Volume_LV_RV.txt")), skiprows=1)[:, 0] / 1000
    np.savetxt(args.output / "schedule.txt", schedule)
    geometry = geometry_audit(args.data_dir, args.output)
    result = circulation_benchmark(args.output, audit)
    save(args.output / "RUN_STATUS.json", {"KIT": result["status"], "KIT_spatial": geometry["full_spatial_benchmark"]["status"],
        "EDGAR": "RUN_SEPARATELY_WITH_benchmark_edgar_p034.py", "seconds": time.monotonic() - started, "clinical_qualification": "NOT_ESTABLISHED"})
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
