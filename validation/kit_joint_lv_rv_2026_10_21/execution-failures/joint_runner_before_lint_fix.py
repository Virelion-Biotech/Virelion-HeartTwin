"""Conditional joint LV/RV development calibration; previously seen KIT data."""
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


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    out=args.output
    out.mkdir(parents=True, exist_ok=True)
    base=json.loads(args.candidate.read_text())
    cal=json.loads((args.baseline/"observations/LV_calibration.json").read_text())
    rv=json.loads((args.baseline/"observations/RV_volume_sparse.json").read_text())
    if rv["unit"] != "mL" or len(rv["values"]) != 2 or rv["time_s"][0] != 0:
        raise ValueError("Expected original sparse KIT RV volumes in mL")
    if base["initial_volumes_mL"][5] != rv["values"][0]:
        raise ValueError("Measured RV initial condition must remain unchanged")
    lv=base["compartments"]["LV"]
    k=lv["passive_exponent_per_mL"]
    reference_volume=float(cal["values"][0])
    delta=reference_volume-lv["unstressed_volume_mL"]
    reference_pressure=lv["emin_mmHg_per_mL"]*np.expm1(k*delta)/k if k else lv["emin_mmHg_per_mL"]*delta
    phase=lv["activation_start_s"]
    if phase > base["cycle_length_s"]/2:
        phase-=base["cycle_length_s"]
    center=[lv["emax_mmHg_per_mL"],lv["activation_duration_s"],
        base["edge_resistance_mmHg_s_per_mL"][0],reference_pressure,
        lv["activation_peak_fraction"],phase,.7457]
    second=np.array(center)*np.array([1.04,.98,1.1,.9,.98,1.,1.05])
    low=np.array([.2,.25,.001,.5,.25,-.15,.1])
    high=np.array([8.,.7,.2,40.,.75,.05,3.])
    starts=[center,np.clip(second,low+1e-8,high-1e-8).tolist()]
    factors=[.5,1.,2.]
    protocol={"scope":"Conditional joint LV/RV development fit, not independent validation",
        "source_candidate_sha256":hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
        "runner_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "LV_calibration_count":23,"LV_inner_fit_count":17,"LV_inner_assessment_count":6,
        "RV_subsequent_point_role":"CALIBRATION; previously assessed point now consumed for fitting",
        "RV_initial_point_role":"Measured initial condition, not validation",
        "RV_outflow_resistance_factors":factors,"fixed_k_per_mL":k,
        "coordinates":["LV_emax","LV_duration","mitral_R","conditional_LV_passive_reference_pressure",
            "LV_peak_fraction","LV_phase","RV_emax"],
        "bounds":[low.tolist(),high.tolist()],"starts":starts,"max_nfev":45,
        "objective":"LV volume residuals plus one RV volume residual, both in mL; unweighted developmental SSE, not a validated noise likelihood",
        "selection":"Lowest RMSE on six inner LV samples; RV always calibration, outer LV never selection",
        "pressure_scale":"Unidentified; all pressures/strengths conditional on assumed loading and other fixed parameters",
        "prior_outer_values_seen":True,"independent_patient_validation":False,
        "clinical_validation":"NOT_ESTABLISHED"}
    save(out/"protocol.json",protocol)

    def payload(x,factor,times,destination):
        p=deepcopy(base)
        part=p["compartments"]["LV"]
        part["emax_mmHg_per_mL"]=float(x[0])
        part["activation_duration_s"]=float(x[1])
        p["edge_resistance_mmHg_s_per_mL"][0]=float(x[2])
        part["emin_mmHg_per_mL"]=float(x[3]/delta if k==0 else x[3]*k/np.expm1(k*delta))
        part["activation_peak_fraction"]=float(x[4])
        part["activation_start_s"]=float(x[5]%p["cycle_length_s"])
        p["compartments"]["RV"]["emax_mmHg_per_mL"]=float(x[6])
        p["edge_resistance_mmHg_s_per_mL"][5]=base["edge_resistance_mmHg_s_per_mL"][5]*factor
        p["time_s"]=np.unique(np.r_[times,rv["time_s"][1]]).tolist()
        p["output_dir"]=str(destination)
        return p

    def projection(prediction,times):
        return np.interp(times,prediction["time_s"],prediction["volume_mL"]["LV"])

    def rv_value(prediction):
        return float(np.interp(rv["time_s"][1],prediction["time_s"],prediction["volume_mL"]["RV"]))

    def fit(factor,times,values,dest):
        trials=[]
        for start in starts:
            def residual(x):
                pred=simulate_closed_loop(payload(x,factor,times,dest))
                return np.r_[projection(pred,times)-values,rv_value(pred)-rv["values"][1]]
            result=least_squares(residual,start,bounds=(low,high),max_nfev=45,diff_step=1e-3,
                ftol=1e-5,xtol=1e-5,gtol=1e-5,x_scale="jac")
            trials.append({"x":result.x.tolist(),"SSE_mL2":float(2*result.cost),
                "success":bool(result.success),"message":result.message,"nfev":result.nfev,
                "scaled_jacobian_singular_values":np.linalg.svd(result.jac*(high-low),compute_uv=False).tolist()})
        return min(trials,key=lambda r:r["SSE_mL2"]),trials

    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        for factor in factors:
            best,trials=fit(factor,cal["time_s"][:17],cal["values"][:17],temp)
            pred=simulate_closed_loop(payload(best["x"],factor,cal["time_s"],temp))
            row={"RV_outflow_resistance_factor":factor,"inner_LV_RMSE_mL":float(np.sqrt(
                np.mean((projection(pred,cal["time_s"][17:])-cal["values"][17:])**2))),
                "RV_calibration_error_mL":rv_value(pred)-rv["values"][1],"best":best,"trials":trials}
            rows.append(row)
            save(out/"inner_progress.json",rows)
            print("INNER",json.dumps(row),flush=True)
        selected=min(rows,key=lambda r:(r["inner_LV_RMSE_mL"],r["RV_outflow_resistance_factor"]))
        save(out/"selection.json",selected)
        # Retain full-data refits for all loading controls; never select by outer score.
        schedule=np.loadtxt(args.baseline/"schedule.txt")
        pending=[]
        for row in rows:
            factor=row["RV_outflow_resistance_factor"]
            best,trials=fit(factor,cal["time_s"],cal["values"],temp)
            folder=out/("loading-"+str(factor));folder.mkdir(exist_ok=True)
            request=payload(best["x"],factor,schedule,folder/"artifacts")
            pred=simulate_closed_loop(request)
            save(folder/"frozen_request.json",request)
            save(folder/"frozen_prediction.json",pred)
            pending.append((row,best,trials,folder,pred))
        # All requests/predictions frozen before opening archived outer values.
        outer=json.loads((args.baseline/"observations/LV_temporal_holdout.json").read_text())
        reports=[]
        for row,best,trials,folder,pred in pending:
            report={"RV_outflow_resistance_factor":row["RV_outflow_resistance_factor"],
                "selected_by_inner":row is selected,"inner":row,"final_fit":best,"final_trials":trials,
                "prediction_sha256":hashlib.sha256((folder/"frozen_prediction.json").read_bytes()).hexdigest(),
                "LV_calibration_RMSE_mL":float(np.sqrt(np.mean(
                    (projection(pred,cal["time_s"])-cal["values"])**2))),
                "RV_calibration_error_mL":rv_value(pred)-rv["values"][1],
                "LV_outer_development_RMSE_mL":float(np.sqrt(np.mean(
                    (projection(pred,outer["time_s"])-outer["values"])**2))),
                "RV_independent_validation":False,"clinical_validation":"NOT_ESTABLISHED"}
            reports.append(report)
            save(folder/"results.json",report)
            print("FULL",json.dumps(report),flush=True)
        save(out/"results.json",{"selected_loading_factor":selected["RV_outflow_resistance_factor"],
            "families":reports,"RV_point_consumed_for_calibration":True,
            "independent_patient_validation":False,"clinical_validation":"NOT_ESTABLISHED"})
    print("JOINT_DEVELOPMENT_FIT_COMPLETE",flush=True)


if __name__=="__main__":
    main()
