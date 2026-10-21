"""Conditional chamber-law challenge using external murine pressure-volume recordings.

Run numerics in Colab. This is NOT a circulation solve or human validation.
Source: Colebank & Chesler 2023, doi:10.1007/s10439-023-03293-z.
"""
from pathlib import Path
import hashlib
import json
import platform
import urllib.request

import numpy as np
import scipy
from scipy.io import loadmat
from scipy.optimize import least_squares

SOURCE_PIN = "2da0cac9b13b1d3b6eb41e7a00ffde3c78e9c8a4"
HEARTTWIN_PIN = "3df859867660079430fafca84e55fe71d4e6c8cb"
EXPECTED = {
    "m1_timeseries_final.mat": "bfc57850915d37043aa63165b3ffc699c9dea4b2",
    "m1_mean_var.mat": "267aba9c71ee616cc98ba5eacbfd2b09c66f2199",
    "m2_timeseries_final.mat": "a67d4e28bc50556178d3e3df0fb3f2e313800460",
    "m2_mean_var.mat": "56dc550c72e5fed1a9dbb422b3b2a6dec6eab8df",
    "m3_timeseries_final.mat": "85e850422c0570a035107a39761817217b66052b",
    "m3_mean_var.mat": "1f54e8451aa80cba4d88857261620761803a0f5d",
}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def fetch_sources(out):
    base = f"https://raw.githubusercontent.com/mjcolebank/Colebank_2023_AcuteIschemia/{SOURCE_PIN}/"
    receipts = {}
    for name, expected in EXPECTED.items():
        raw = urllib.request.urlopen(base + "MainCode/matfiles_to_load/" + name, timeout=60).read()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != expected:
            raise ValueError(f"Source hash mismatch: {name}")
        (out / name).write_bytes(raw)
        receipts[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "git_blob": blob}
    for name in ["LICENSE", "MainCode/get_pars_NEW.m", "MainCode/run_mouse.m"]:
        raw = urllib.request.urlopen(base + name, timeout=60).read()
        (out / Path(name).name).write_bytes(raw)
    dump(out / "source_receipts.json", receipts)


def main(out, chamber_pressure):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    protocol = {
        "source_pin": SOURCE_PIN, "hearttwin_pin": HEARTTWIN_PIN,
        "purpose": "Measured-volume-conditioned chamber pressure challenge, NOT closed-loop prediction",
        "species": "mouse", "development_subject": "mouse1",
        "reserved_subjects": ["mouse2", "mouse3"], "reserved_values_opened": False,
        "LV_RV_acquisition": "sequential, not simultaneous",
        "axis": "50 publisher-normalized phases linspace(0,1,50), not raw timestamps",
        "cycle_s": 0.1034, "cycle_source": "get_pars_NEW.m",
        "volume_conversion": "publisher uL / 1000 -> mL", "pressure_unit": "mmHg",
        "calibration_columns_zero_based": [0, 29], "inner_columns_zero_based": [30, 39],
        "refit_columns_zero_based": [0, 39], "outer_columns_zero_based": [40, 49],
        "split_limitation": "Correlated processed beats of one acquisition; not independent patient validation",
        "publisher_mean_used_for_fitting": False,
        "families": ["symmetric_linear", "asymmetric_kVref_0", "asymmetric_kVref_1",
                     "asymmetric_kVref_3", "asymmetric_kVref_6"],
        "selection": "Pressure RMSE on inner beat columns, per chamber",
        "bounds": {"emin": [0.001, 5000], "emax_increment": [0.001, 20000],
                   "duration_fraction": [0.2, 1.0], "phase_fraction": [-0.2, 0.2],
                   "peak_fraction": [0.1, 0.9]},
        "fixed": {"unstressed_volume_mL": 0, "external_pressure_mmHg": 0},
        "starts": 2, "max_nfev": 250, "measurement_noise_model": None,
        "flow_measured": False, "KIT_changed": False,
        "clinical_acceptance_threshold": None,
    }
    dump(out / "protocol.json", protocol)  # Before numerical data are opened.
    fetch_sources(out)
    data = loadmat(out / "m1_timeseries_final.mat")
    dump(out / "development_schema.json", {
        k: {"shape": list(v.shape), "dtype": str(v.dtype)}
        for k, v in data.items() if not k.startswith("__")})
    cycle = protocol["cycle_s"]
    times = np.linspace(0, cycle, 50)
    fitted = {}
    frozen = {}
    for chamber in ["LV", "RV"]:
        pressure = np.asarray(data["P" + chamber + "_stack"], dtype=float)
        volume = np.asarray(data["V" + chamber + "_stack"], dtype=float) / 1000
        if pressure.shape != (50, 50) or volume.shape != pressure.shape:
            raise ValueError(f"Unexpected {chamber} processed-beat shape")
        if not np.isfinite(pressure).all() or not np.isfinite(volume).all() or np.any(volume <= 0):
            raise ValueError(f"Nonfinite/nonpositive {chamber} source")
        vref = float(np.max(volume[:, :30]))  # Calibration only.
        calibration_p = pressure[:, :30].mean(axis=1)
        calibration_v = volume[:, :30].mean(axis=1)

        def parameters(x, stiffness, asymmetric):
            p = {"unstressed_volume_mL": 0., "external_pressure_mmHg": 0.,
                 "emin_mmHg_per_mL": float(x[0]), "emax_mmHg_per_mL": float(x[0] + x[1]),
                 "activation_duration_s": float(x[2] * cycle),
                 "activation_start_s": float((x[3] * cycle) % cycle)}
            if asymmetric:
                p["activation_peak_fraction"] = float(x[4])
            if stiffness:
                p["passive_exponent_per_mL"] = float(stiffness / vref)
            return p

        def predict(x, stiffness, asymmetric, volumes):
            p = parameters(x, stiffness, asymmetric)
            return np.array([[chamber_pressure(t, v, p, cycle) for v in row]
                             for t, row in zip(times, np.atleast_2d(volumes.T).T, strict=True)])

        def fit(stiffness, asymmetric, v, p):
            lo = [0.001, 0.001, 0.2, -0.2] + ([0.1] if asymmetric else [])
            hi = [5000, 20000, 1., 0.2] + ([0.9] if asymmetric else [])
            trials = []
            for duration, phase in [(0.55, 0.), (0.8, -0.1)]:
                start = [10., 500., duration, phase] + ([0.5] if asymmetric else [])
                result = least_squares(
                    lambda x: predict(x, stiffness, asymmetric, v[:, None]).ravel() - p,
                    start, bounds=(lo, hi), x_scale="jac", max_nfev=250,
                    ftol=1e-9, xtol=1e-9, gtol=1e-9)
                trials.append({"x": result.x.tolist(), "sse": float(2 * result.cost),
                               "success": bool(result.success), "status": int(result.status),
                               "message": result.message, "nfev": result.nfev,
                               "active_mask": result.active_mask.tolist()})
            converged = [x for x in trials if x["success"]]
            if not converged:
                raise ValueError(f"No converged {chamber} fit")
            return min(converged, key=lambda x: x["sse"]), trials

        families = []
        for stiffness, asymmetric in [(0, False), (0, True), (1, True), (3, True), (6, True)]:
            chosen, trials = fit(stiffness, asymmetric, calibration_v, calibration_p)
            inner_prediction = predict(chosen["x"], stiffness, asymmetric, volume[:, 30:40])
            rmse = float(np.sqrt(np.mean((inner_prediction - pressure[:, 30:40]) ** 2)))
            families.append({"stiffness_times_vref": stiffness, "asymmetric": asymmetric,
                             "inner_pressure_rmse_mmHg": rmse, "calibration_fit": chosen,
                             "calibration_trials": trials})
        selected_index = min(range(len(families)), key=lambda i: families[i]["inner_pressure_rmse_mmHg"])
        selected = families[selected_index]
        stiffness, asymmetric = selected["stiffness_times_vref"], selected["asymmetric"]
        chosen, trials = fit(stiffness, asymmetric, volume[:, :40].mean(axis=1),
                             pressure[:, :40].mean(axis=1))
        model = parameters(chosen["x"], stiffness, asymmetric)
        fitted[chamber] = {"families": families, "selected_index": selected_index,
                           "vref_mL": vref, "refit": chosen, "refit_trials": trials,
                           "parameters": model}
        frozen[chamber] = predict(chosen["x"], stiffness, asymmetric, volume[:, 40:50])
    # Freeze both chamber models before scoring any outer pressure values.
    dump(out / "frozen_models.json", fitted)
    freeze_sha = hashlib.sha256((out / "frozen_models.json").read_bytes()).hexdigest()
    dump(out / "frozen_outer_predictions.json", {k: v.tolist() for k, v in frozen.items()})
    scores = {}
    for chamber, prediction in frozen.items():
        observed = data["P" + chamber + "_stack"][:, 40:50]
        error = prediction - observed
        baseline = np.broadcast_to(data["P" + chamber + "_stack"][:, :40].mean(axis=1)[:, None],
                                   observed.shape)
        scores[chamber] = {
            "conditional_pressure_rmse_mmHg": float(np.sqrt(np.mean(error ** 2))),
            "conditional_pressure_mae_mmHg": float(np.mean(np.abs(error))),
            "conditional_pressure_bias_mmHg": float(np.mean(error)),
            "training_mean_waveform_baseline_rmse_mmHg": float(np.sqrt(np.mean((baseline-observed)**2))),
            "outer_points": int(error.size), "outer_processed_beats": 10,
            "clinical_pass": None}
    report = {"frozen_models_sha256": freeze_sha, "scores": scores,
              "reserved_values_opened": False, "hearttwin_circulation_executed": False,
              "environment": {"python": platform.python_version(),
                              "numpy": np.__version__, "scipy": scipy.__version__}}
    dump(out / "results.json", report)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    import argparse
    from hearttwin.patient_closed_loop import chamber_pressure
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    main(args.output, chamber_pressure)
