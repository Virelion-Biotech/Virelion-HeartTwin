"""Diagnose KIT model error without selecting against the archived outer holdout."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

from hearttwin.config import load_registry
from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def independent_reference(request):
    """Separate vectorized pressure law, incidence matrix and DOP853 integrator."""
    names = ["LA", "LV", "SA", "SV", "RA", "RV", "PA", "PV"]
    params = [request["compartments"][c] for c in names]
    v0 = np.array([p["unstressed_volume_mL"] for p in params])
    ext = np.array([p["external_pressure_mmHg"] for p in params])
    resistance = np.array(request["edge_resistance_mmHg_s_per_mL"])
    incidence = np.zeros((8, 8))
    for edge in range(8):
        incidence[edge, edge] = -1
        incidence[(edge + 1) % 8, edge] = 1
    cycle = request["cycle_length_s"]

    def pressure(t, volume):
        elastance = np.array([p.get("emin_mmHg_per_mL", 0.) for p in params])
        for slot in (0, 1, 4, 5):
            p = params[slot]
            phase = np.remainder(t - p["activation_start_s"], cycle)
            if phase < p["activation_duration_s"]:
                elastance[slot] += (p["emax_mmHg_per_mL"] - p["emin_mmHg_per_mL"]) * np.sin(np.pi * phase / p["activation_duration_s"])**2
        for slot in (2, 3, 6, 7):
            elastance[slot] = 1. / params[slot]["compliance_mL_per_mmHg"]
        return ext + elastance * (volume - v0)

    def rhs(t, volume):
        p = pressure(t, volume)
        difference = -incidence.T @ p
        q = difference / resistance
        q[[0, 1, 4, 5]] = np.maximum(q[[0, 1, 4, 5]], 0.)
        return incidence @ q

    t = np.asarray(request["time_s"])
    result = solve_ivp(rhs, (t[0], t[-1]), request["initial_volumes_mL"], t_eval=t,
                       method="DOP853", rtol=1e-10, atol=1e-11, max_step=.0005)
    if not result.success:
        raise RuntimeError(result.message)
    return result.y.T


def solver_and_transport(base, output):
    request = json.loads((base / "fitted_model.json").read_text())
    request["output_dir"] = str(output / "artifacts")
    calls = {}
    def profile(frame, event, arg):
        if event == "call":
            filename = frame.f_code.co_filename.replace("\\", "/")
            for package in ("hearttwin", "cardiflow", "cardiinfer", "cardimech", "cardiep", "cardianatomy", "electrotrace"):
                if "/" + package + "/" in filename:
                    calls.setdefault(package, set()).add(frame.f_code.co_name)
    sys.setprofile(profile)
    try:
        direct = simulate_closed_loop(request)
    finally:
        sys.setprofile(None)
    reference = independent_reference(request)
    native = np.column_stack([direct["volume_mL"][c] for c in COMPARTMENTS])
    old = json.loads((base / "frozen_prediction.json").read_text())
    old_native = np.column_stack([old["volume_mL"][c] for c in COMPARTMENTS])
    adapter = load_registry().capability("patient.circulation.simulate")
    routed = adapter.invoke("patient.circulation.simulate", request)
    command = {"subject_id": request["subject_id"], "parameters": {},
               "context": {"patient_forward": {"capability": "patient.circulation.simulate", "template": request,
                                                "parameter_paths": {}, "sampling": []}}}
    env = dict(os.environ, HEARTTWIN_PAYLOAD=json.dumps(command))
    completed = subprocess.run([sys.executable, "-m", "hearttwin.patient_domain_forward"], env=env,
                               capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    forwarded = json.loads(completed.stdout)
    compare = lambda other: max(float(np.max(np.abs(np.asarray(other["volume_mL"][c]) - direct["volume_mL"][c]))) for c in COMPARTMENTS)
    p0 = np.array([direct["pressure_mmHg"][c][0] for c in COMPARTMENTS])
    q0 = np.array([direct["flow_mL_per_s"][c + "_to_" + COMPARTMENTS[(i + 1) % 8]][0] for i, c in enumerate(COMPARTMENTS)])
    train = json.loads((base / "observations/LV_calibration.json").read_text())
    measured_initial_rate = (train["values"][1] - train["values"][0]) / (train["time_s"][1] - train["time_s"][0])
    report = {
        "independent_DOP853_max_volume_error_mL": float(np.max(np.abs(reference - native))),
        "unit_metadata_patch_max_volume_change_mL": float(np.max(np.abs(native - old_native))),
        "registry_vs_direct_max_volume_error_mL": compare(routed),
        "command_forward_vs_direct_max_volume_error_mL": compare(forwarded),
        "direct_repository_call_trace": {k: sorted(v) for k, v in calls.items()},
        "source_file": inspect.getfile(simulate_closed_loop),
        "initial_compartment_pressures_mmHg": dict(zip(COMPARTMENTS, p0.tolist())),
        "initial_LV_net_flow_mL_per_s": float(q0[0] - q0[1]),
        "measured_first_interval_LV_rate_mL_per_s": float(measured_initial_rate),
        "initial_state_interpretation": "Both LV valves initially closed in assumed model; observed first interval loses volume. Test activation-phase and filling assumptions, not an observation time shift.",
        "scope": "Native 0D solver and two real HeartTwin transport paths. Other repository engines were not the numerical source of this KIT benchmark.",
    }
    save(output / "solver_transport_diagnosis.json", report)
    if report["independent_DOP853_max_volume_error_mL"] > .01:
        raise AssertionError("Native/reference mismatch exceeds 0.01 mL numerical diagnostic threshold")
    if max(report["registry_vs_direct_max_volume_error_mL"], report["command_forward_vs_direct_max_volume_error_mL"]) > 1e-8:
        raise AssertionError("Transport altered native trajectories")
    print("SOLVER_TRANSPORT", json.dumps(report), flush=True)
    return report


def metrics(observed, predicted):
    error = np.asarray(predicted) - np.asarray(observed)
    return {"n": len(error), "rmse_mL": float(np.sqrt(np.mean(error**2))),
            "mae_mL": float(np.mean(np.abs(error))), "maximum_absolute_error_mL": float(np.max(np.abs(error)))}


def model_assumptions(base, output):
    calibration = json.loads((base / "observations/LV_calibration.json").read_text())
    assumed = json.loads((base / "assumed_base_model.json").read_text())
    schedule = np.loadtxt(base / "schedule.txt")
    variants = [
        {"name": "original_three_parameter", "phase": False, "filling": False},
        {"name": "activation_phase", "phase": True, "filling": False},
        {"name": "initial_filling", "phase": False, "filling": True},
        {"name": "phase_and_filling", "phase": True, "filling": True},
    ]
    protocol = {
        "selection_data": "Only the 23 original calibration samples; first 17 fit, next 6 inner temporal validation",
        "outer_holdout": "Original 12 archived holdout samples are never used for selection or optimiser residuals; any final rescoring is retrospective development reassessment, not independent validation.",
        "variants": variants, "selection_metric": "Inner-validation RMSE, then parameter count on exact ties",
        "starts_per_variant": 2, "max_nfev_per_start": 45,
        "parameter_bounds": {"LV_emax": [.1, 8.], "LV_duration_s": [.25, .7], "mitral_resistance": [.001, .2],
                             "activation_phase_s": [-.2, .1], "initial_LA_volume_mL": [30., 120.], "initial_PV_volume_mL": [330., 500.]},
        "filling_constraint": "Initial SV volume compensates LA/PV changes so the same assumed total blood volume is preserved.",
        "phase_policy": "Change explicit model activation start modulo the fixed assumed 1.3 s cycle; never shift/warp measured observations.",
        "qualification": "Conditional model diagnosis; unfitted parameters remain assumptions and absolute pressure scale remains nonidentifiable.",
    }
    save(output / "model_diagnosis_protocol.json", protocol)
    def coordinates(variant):
        low, high = [.1, .25, .001], [8., .7, .2]
        starts = [[2., .5, .01], [.8, .4, .03]]
        if variant["phase"]:
            low += [-.2]; high += [.1]
            starts[0] += [-.06]; starts[1] += [-.12]
        if variant["filling"]:
            low += [30., 330.]; high += [120., 500.]
            starts[0] += [60., 400.]; starts[1] += [85., 450.]
        return low, high, starts

    def payload(x, variant, times, destination):
        p = deepcopy(assumed)
        p["compartments"]["LV"]["emax_mmHg_per_mL"] = float(x[0])
        p["compartments"]["LV"]["activation_duration_s"] = float(x[1])
        p["edge_resistance_mmHg_s_per_mL"][0] = float(x[2])
        cursor = 3
        if variant["phase"]:
            p["compartments"]["LV"]["activation_start_s"] = float(x[cursor] % p["cycle_length_s"])
            cursor += 1
        if variant["filling"]:
            la, pv = map(float, x[cursor:cursor+2])
            p["initial_volumes_mL"][0], p["initial_volumes_mL"][7] = la, pv
            p["initial_volumes_mL"][3] -= (la - assumed["initial_volumes_mL"][0]) + (pv - assumed["initial_volumes_mL"][7])
        p["time_s"] = list(map(float, times))
        p["output_dir"] = str(destination)
        return p

    def fit(variant, times, observations, destination):
        low, high, starts = coordinates(variant)
        trials = []
        for start in starts:
            def residual(x):
                result = simulate_closed_loop(payload(x, variant, times, destination))
                return np.asarray(result["volume_mL"]["LV"]) - observations
            selected = least_squares(residual, start, bounds=(low, high), max_nfev=45,
                                     diff_step=1e-3, ftol=1e-5, xtol=1e-5, gtol=1e-5, x_scale="jac")
            singular = np.linalg.svd(selected.jac * (np.array(high) - low)[None, :], compute_uv=False)
            trials.append({"x": selected.x.tolist(), "sse_mL2": float(2*selected.cost), "nfev": selected.nfev,
                           "termination_success": bool(selected.success), "termination": selected.message,
                           "bound_fraction": [float(min(v-a,b-v)/(b-a)) for v,a,b in zip(selected.x,low,high)],
                           "scaled_jacobian_singular_values": singular.tolist()})
        return min(trials, key=lambda x: x["sse_mL2"]), trials

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        inner_times, inner_values = calibration["time_s"][:17], calibration["values"][:17]
        for variant in variants:
            chosen, trials = fit(variant, inner_times, inner_values, tmp)
            prediction = simulate_closed_loop(payload(chosen["x"], variant, calibration["time_s"], tmp))
            scores = metrics(calibration["values"][17:], prediction["volume_mL"]["LV"][17:])
            row = {"variant": variant, "inner_validation": scores, "selected_fit": chosen, "trials": trials}
            rows.append(row)
            print("INNER_MODEL", json.dumps(row), flush=True)
        selected = min(rows, key=lambda r: (r["inner_validation"]["rmse_mL"], len(r["selected_fit"]["x"])))
        save(output / "inner_model_selection.json", {"candidates": rows, "selected": selected["variant"]})
        chosen, trials = fit(selected["variant"], calibration["time_s"], calibration["values"], tmp)
        final_request = payload(chosen["x"], selected["variant"], schedule, output / "artifacts")
        final_prediction = simulate_closed_loop(final_request)
    save(output / "development_candidate_request.json", final_request)
    save(output / "development_candidate_prediction.json", final_prediction)
    digest = hashlib.sha256((output / "development_candidate_prediction.json").read_bytes()).hexdigest()
    # Only after the calibration-only choice and prediction freeze, reopen archived outer values.
    outer = json.loads((base / "observations/LV_temporal_holdout.json").read_text())
    rv = json.loads((base / "observations/RV_volume_sparse.json").read_text())
    old_report = json.loads((base / "KIT_circulation_results.json").read_text())
    rv_pred = float(np.interp(rv["time_s"][1], schedule, final_prediction["volume_mL"]["RV"]))
    report = {"selected_variant": selected["variant"], "final_fit": chosen, "final_trials": trials,
              "prediction_sha256": digest,
              "calibration": metrics(calibration["values"], final_prediction["volume_mL"]["LV"][:23]),
              "archived_outer_development_reassessment": metrics(outer["values"], final_prediction["volume_mL"]["LV"][23:]),
              "original_outer_rmse_mL": old_report["temporal_holdout"]["RMSE_mL"],
              "persistence_outer_rmse_mL": old_report["persistence_baseline_holdout"]["RMSE_mL"],
              "RV_reassessment": {"observed_mL": rv["values"][1], "predicted_mL": rv_pred, "absolute_error_mL": abs(rv_pred-rv["values"][1])},
              "clinical_validation": "NOT_ESTABLISHED", "new_independent_patient_data": False,
              "interpretation": "Ablations test conditional assumptions on calibration data. Archived outer scores are development reassessment only, not a new validation pass."}
    save(output / "model_assumption_diagnosis.json", report)
    print("MODEL_DIAGNOSIS", json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output / "diagnostic_runner.json", {"sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                                "baseline_evidence": str(args.baseline_dir)})
    solver_and_transport(args.baseline_dir, args.output)
    model_assumptions(args.baseline_dir, args.output)


if __name__ == "__main__":
    main()
