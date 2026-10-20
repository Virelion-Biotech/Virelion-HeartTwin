"""Predeclared development ablation: passive filling and activation shape."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
from scipy.optimize import least_squares

from hearttwin.patient_closed_loop import simulate_closed_loop, validate_activation_support


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    base = json.loads((args.baseline/"assumed_base_model.json").read_text())
    train = json.loads((args.baseline/"observations/LV_calibration.json").read_text())
    schedule = np.loadtxt(args.baseline/"schedule.txt")
    variants = [(False, False), (True, False), (False, True), (True, True)]
    save(out/"protocol.json", {"selection":"First 17 original calibration points fit, next six inner temporal validation; lowest inner RMSE",
        "variants":[{"nonlinear_passive":n,"asymmetric_activation":a} for n,a in variants],
        "development_only":True,"original_outer_previously_seen":True,
        "max_nfev":65,"starts":2,"no_new_independent_validation":True,
        "constitutive_law":"P=external+(1-a)*emin*expm1(k*(V-V0))/k+a*emax*(V-V0); k=0 retains linear model",
        "pressure_scale":"Not identified from volumes; conditional parameters only",
        "bounds":{"emax":[.2,8.],"duration":[.25,.7],"mitral_R":[.001,.2],"emin":[.001,.1],"k":[0.,.025],"peak_fraction":[.25,.75]}})
    def payload(x, variant, times, destination):
        p = deepcopy(base)
        lv = p["compartments"]["LV"]
        lv["emax_mmHg_per_mL"],lv["activation_duration_s"] = map(float,x[:2])
        p["edge_resistance_mmHg_s_per_mL"][0] = float(x[2])
        cursor = 3
        if variant[0]:
            lv["emin_mmHg_per_mL"] = float(x[cursor])
            lv["passive_exponent_per_mL"] = float(x[cursor+1])
            cursor += 2
        if variant[1]:
            lv["activation_peak_fraction"] = float(x[cursor])
        p["time_s"] = list(map(float,times))
        p["output_dir"] = str(destination)
        return p
    def fit(variant, times, values, dest):
        low,high = [.2,.25,.001],[8.,.7,.2]
        starts = [[2.,.5,.01],[1.,.45,.005]]
        if variant[0]:
            low += [.001,0.]
            high += [.1,.025]
            starts[0] += [.01,.015]
            starts[1] += [.02,.008]
        if variant[1]:
            low += [.25]
            high += [.75]
            starts[0] += [.4]
            starts[1] += [.6]
        trials = []
        for start in starts:
            def residual(x):
                r = simulate_closed_loop(payload(x,variant,times,dest))
                return np.asarray(r["volume_mL"]["LV"])-values
            result = least_squares(residual,start,bounds=(low,high),max_nfev=65,diff_step=1e-3,
                                   ftol=1e-5,xtol=1e-5,gtol=1e-5,x_scale="jac")
            trials.append({"x":result.x.tolist(),"SSE":float(2*result.cost),
                "success":bool(result.success),"message":result.message,"nfev":result.nfev,
                "scaled_jacobian_singular_values":np.linalg.svd(result.jac*(np.array(high)-low),compute_uv=False).tolist()})
        return min(trials,key=lambda r:r["SSE"]),trials
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for variant in variants:
            best,trials = fit(variant,train["time_s"][:17],train["values"][:17],tmp)
            pred = simulate_closed_loop(payload(best["x"],variant,train["time_s"],tmp))
            rmse = float(np.sqrt(np.mean((np.asarray(pred["volume_mL"]["LV"])[17:]-train["values"][17:])**2)))
            row = {"variant":variant,"inner_rmse_mL":rmse,"best":best,"trials":trials}
            rows.append(row)
            save(out/"inner_progress.json",rows)
            print("INNER",json.dumps(row),flush=True)
        selected = min(rows,key=lambda r:(r["inner_rmse_mL"],len(r["best"]["x"])))
        save(out/"selection.json",selected)
        best,trials = fit(selected["variant"],train["time_s"],train["values"],tmp)
        request = payload(best["x"],selected["variant"],schedule,out/"artifacts")
        pred = simulate_closed_loop(request)
    save(out/"frozen_request.json",request)
    save(out/"frozen_prediction.json",pred)
    # Freeze before reading previously seen archived outer values.
    digest = hashlib.sha256((out/"frozen_prediction.json").read_bytes()).hexdigest()
    outer = json.loads((args.baseline/"observations/LV_temporal_holdout.json").read_text())
    error = np.asarray(pred["volume_mL"]["LV"])[23:]-outer["values"]
    # Quantify an unfitted atrial parameter which has no activation in calibration.
    support = {}
    try:
        validate_activation_support(base,{"LA_emax":"compartments.LA.emax_mmHg_per_mL"},train["time_s"][-1])
    except ValueError as exc:
        support["structural_screen"] = str(exc)
    alternative = deepcopy(request)
    alternative["compartments"]["LA"]["emax_mmHg_per_mL"] *= 1.5
    alt = simulate_closed_loop(alternative)
    support["LA_emax_50pct_change_max_calibration_LV_delta_mL"] = float(np.max(np.abs(np.asarray(alt["volume_mL"]["LV"])[:23]-pred["volume_mL"]["LV"][:23])))
    support["LA_emax_50pct_change_max_outer_LV_delta_mL"] = float(np.max(np.abs(np.asarray(alt["volume_mL"]["LV"])[23:]-pred["volume_mL"]["LV"][23:])))
    report = {"selected":selected,"final_fit":best,"final_trials":trials,"prediction_sha256":digest,
        "calibration_rmse_mL":float(np.sqrt(np.mean((np.asarray(pred["volume_mL"]["LV"])[:23]-train["values"])**2))),
        "archived_outer_development_rmse_mL":float(np.sqrt(np.mean(error**2))),
        "original_outer_rmse_mL":22.86854649759186,"persistence_outer_rmse_mL":8.061338997774605,
        "atrial_support":support,"clinical_validation":"NOT_ESTABLISHED","independent_validation":False,
        "interpretation":"Development constitutive-law comparison; no outer tuning and no pressure identification."}
    save(out/"results.json",report)
    print("RESULT",json.dumps(report),flush=True)


if __name__ == "__main__":
    main()
