"""Calibration-only stiffness profiling with a stable pressure-reference coordinate."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
from scipy.optimize import least_squares
from hearttwin.patient_closed_loop import simulate_closed_loop


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def reference_elastance(pressure, exponent, reference_volume, unloaded_volume):
    delta = reference_volume-unloaded_volume
    if pressure <= 0 or exponent < 0 or delta <= 0:
        raise ValueError("Positive conditional reference pressure/volume span and nonnegative exponent required")
    return pressure/delta if exponent == 0 else pressure*exponent/np.expm1(exponent*delta)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    out=args.output
    out.mkdir(parents=True,exist_ok=True)
    base=json.loads((args.baseline/"assumed_base_model.json").read_text())
    train=json.loads((args.baseline/"observations/LV_calibration.json").read_text())
    schedule=np.loadtxt(args.baseline/"schedule.txt")
    grid=[.005,.0125,.025,.05,.075,.1]
    low,high=[.2,.25,.001,.5,.25,-.15],[8.,.7,.2,40.,.75,.05]
    starts=[[1.77,.48,.006,10.,.58,0.],[2.,.5,.01,15.,.5,-.04]]
    protocol={"kind":"Exploratory calibration-only stiffness profile; not new independent validation",
        "prior_outer_values_seen":True,"selection":"Fit first 17 calibration points; choose k by lowest RMSE on next six calibration points, then refit six remaining coordinates on all 23",
        "k_grid_per_mL":grid,"coordinates":["LV_emax","activation_duration","mitral_R","conditional_passive_pressure_at_initial_measured_LV_volume","peak_fraction","activation_start"],
        "bounds":[low,high],"starts":starts,"max_nfev":55,
        "parameterization":"emin=P_ref*k/expm1(k*(V_ref-V0)); k=0 uses P_ref/(V_ref-V0). Algebraically identical exponential passive law.",
        "reference_volume_mL":train["values"][0],"reference_pressure_is_measured":False,
        "identifiability":"Pressure scale and physiology remain unvalidated. k is a development-selected shape, not an identified patient property.",
        "motivation":"Prior calibration-only fits hit arbitrary k bounds; original emin/k coordinates had poor scaling. No selection against archived outer error."}
    save(out/"protocol.json",protocol)
    def payload(x,k,times,destination):
        p=deepcopy(base)
        lv=p["compartments"]["LV"]
        lv["emax_mmHg_per_mL"]=float(x[0])
        lv["activation_duration_s"]=float(x[1])
        p["edge_resistance_mmHg_s_per_mL"][0]=float(x[2])
        lv["emin_mmHg_per_mL"]=float(reference_elastance(x[3],k,train["values"][0],lv["unstressed_volume_mL"]))
        lv["passive_exponent_per_mL"]=k
        lv["activation_peak_fraction"]=float(x[4])
        lv["activation_start_s"]=float(x[5]%p["cycle_length_s"])
        p["time_s"]=list(map(float,times))
        p["output_dir"]=str(destination)
        return p
    def fit(k,times,values,dest):
        trials=[]
        for start in starts:
            def residual(x):
                r=simulate_closed_loop(payload(x,k,times,dest))
                return np.asarray(r["volume_mL"]["LV"])-values
            result=least_squares(residual,start,bounds=(low,high),max_nfev=55,diff_step=1e-3,
                                 ftol=1e-5,xtol=1e-5,gtol=1e-5,x_scale="jac")
            trials.append({"x":result.x.tolist(),"SSE":float(2*result.cost),"success":bool(result.success),
                "message":result.message,"nfev":result.nfev,
                "scaled_jacobian_singular_values":np.linalg.svd(result.jac*(np.array(high)-low),compute_uv=False).tolist()})
        return min(trials,key=lambda r:r["SSE"]),trials
    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        for k in grid:
            best,trials=fit(k,train["time_s"][:17],train["values"][:17],temp)
            pred=simulate_closed_loop(payload(best["x"],k,train["time_s"],temp))
            rmse=float(np.sqrt(np.mean((np.asarray(pred["volume_mL"]["LV"])[17:]-train["values"][17:])**2)))
            row={"k":k,"inner_rmse_mL":rmse,"best":best,"trials":trials}
            rows.append(row)
            save(out/"profile_progress.json",rows)
            print("PROFILE",json.dumps(row),flush=True)
        selected=min(rows,key=lambda r:(r["inner_rmse_mL"],r["k"]))
        save(out/"selection.json",selected)
        best,trials=fit(selected["k"],train["time_s"],train["values"],temp)
        request=payload(best["x"],selected["k"],schedule,out/"artifacts")
        prediction=simulate_closed_loop(request)
    save(out/"frozen_request.json",request)
    save(out/"frozen_prediction.json",prediction)
    digest=hashlib.sha256((out/"frozen_prediction.json").read_bytes()).hexdigest()
    outer=json.loads((args.baseline/"observations/LV_temporal_holdout.json").read_text())
    report={"selected":selected,"final_fit":best,"final_trials":trials,"prediction_sha256":digest,
        "calibration_rmse_mL":float(np.sqrt(np.mean((np.asarray(prediction["volume_mL"]["LV"])[:23]-train["values"])**2))),
        "archived_outer_development_rmse_mL":float(np.sqrt(np.mean((np.asarray(prediction["volume_mL"]["LV"])[23:]-outer["values"])**2))),
        "original_outer_rmse_mL":22.86854649759186,"persistence_outer_rmse_mL":8.061338997774605,
        "clinical_validation":"NOT_ESTABLISHED","independent_validation":False,
        "interpretation":"k chosen from calibration-only profile; original failed benchmark retained. No clinical pressure/parameter or new validation claim."}
    save(out/"results.json",report)
    print("RESULT",json.dumps(report),flush=True)


if __name__=="__main__":
    main()
