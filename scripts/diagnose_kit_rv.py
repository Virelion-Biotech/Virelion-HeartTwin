"""Diagnose sparse KIT RV mismatch without claiming a new validation pass.

Run in Edge Colab. Existing requests and original benchmark evidence are read-only.
Retrospective constraint fits are optional and never alter the benchmark request.
"""
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

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from scipy.special import exprel

from hearttwin.config import load_registry
from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(request):
    """Separate incidence, cosine activation, exprel pressure and DOP853 solve."""
    names = ["LA", "LV", "SA", "SV", "RA", "RV", "PA", "PV"]
    incidence = np.zeros((8, 8))
    for edge in range(8):
        incidence[edge, edge] = -1.
        incidence[(edge + 1) % 8, edge] = 1.
    resistance = np.asarray(request["edge_resistance_mmHg_s_per_mL"])
    cycle = request["cycle_length_s"]

    def pressure(t, volumes):
        result = []
        for name, volume in zip(names, volumes):
            part = request["compartments"][name]
            delta = volume - part["unstressed_volume_mL"]
            if name in {"LA", "LV", "RA", "RV"}:
                elapsed = (t-part["activation_start_s"]) % cycle
                duration = part["activation_duration_s"]
                active = 0.
                if elapsed < duration:
                    peak = duration*part.get("activation_peak_fraction", .5)
                    ratio = elapsed/peak if elapsed <= peak else (duration-elapsed)/(duration-peak)
                    active = .5*(1.-np.cos(np.pi*ratio))
                exponent = part.get("passive_exponent_per_mL", 0.)
                if exponent*delta > 600:
                    raise ValueError("Reference passive exponent exceeds supported range")
                passive = part["emin_mmHg_per_mL"]*delta*exprel(exponent*delta)
                value = (1.-active)*passive+active*part["emax_mmHg_per_mL"]*delta
            else:
                value = delta/part["compliance_mL_per_mmHg"]
            result.append(part["external_pressure_mmHg"]+value)
        return np.asarray(result)

    def rhs(t, volumes):
        flow = (-incidence.T@pressure(t, volumes))/resistance
        flow[[0, 1, 4, 5]] = np.maximum(flow[[0, 1, 4, 5]], 0.)
        return incidence@flow

    times = np.asarray(request["time_s"])
    result = solve_ivp(rhs, (0., times[-1]), request["initial_volumes_mL"],
        t_eval=times, method="DOP853", rtol=1e-10, atol=1e-11, max_step=.0005)
    if not result.success or result.y.shape != (8, len(times)) or not np.isfinite(result.y).all():
        raise RuntimeError("Reference integration failed: "+result.message)
    if np.any(result.y < 0):
        raise RuntimeError("Reference integration produced negative volume")
    return result.y.T


def compare(first, second):
    return max(float(np.max(np.abs(np.asarray(first["volume_mL"][name])-
        second["volume_mL"][name]))) for name in COMPARTMENTS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--constraint-fit", action="store_true",
        help="Use the already seen RV point as a development constraint, never validation")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    request_path = args.request or args.baseline_dir/"fitted_model.json"
    rv_path = args.baseline_dir/"observations/RV_volume_sparse.json"
    request = json.loads(request_path.read_text())
    observation = json.loads(rv_path.read_text())
    times = np.asarray(observation["time_s"], dtype=float)
    values = np.asarray(observation["values"], dtype=float)
    if (observation["unit"] != "mL" or times.shape != (2,) or values.shape != (2,)
            or not np.isfinite(times).all() or not np.isfinite(values).all()
            or times[0] != 0 or times[1] <= 0 or np.any(values < 0)):
        raise ValueError("Expected exactly the two finite KIT RV volume samples in mL")
    target_time, target = float(times[1]), float(values[1])
    original_times = np.asarray(request["time_s"], dtype=float)
    if target_time > original_times[-1]:
        raise ValueError("RV target lies outside request time support")
    rv_slot = COMPARTMENTS.index("RV")
    if not np.isclose(request["initial_volumes_mL"][rv_slot], values[0], rtol=0, atol=1e-6):
        raise ValueError("Initial RV volume differs from the measured initial condition")
    protocol = {
        "kind": "Sparse-RV diagnosis; previously seen data; development only",
        "request_path": str(request_path), "request_sha256": digest(request_path),
        "RV_observation_sha256": digest(rv_path),
        "runner_sha256": digest(Path(__file__)),
        "native_module_sha256": digest(Path(inspect.getfile(simulate_closed_loop))),
        "constraint_fit_requested": args.constraint_fit,
        "selection_policy": "No selection or replacement of the original or selected LV model",
        "data_limit": "One initial condition and one subsequent RV point, not a waveform",
        "identification_limit": "One scalar residual has Jacobian rank at most one; simultaneous RV/loading parameter identification is impossible from this target alone",
        "clinical_validation": "NOT_ESTABLISHED", "independent_patient_validation": False,
        "no_steady_state_claim": True,
        "output_time_policy": "Measured timestamps retained; exact RV target and dense diagnostic output points added without changing physiology"}
    save(output/"protocol.json", protocol)
    (output/"environment.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True))
    # Keep original request on disk unchanged. New artifacts use a separate directory.
    request = deepcopy(request)
    request["output_dir"] = str(output/"artifacts/base")
    request["time_s"] = np.unique(np.r_[original_times, target_time,
        np.linspace(0., original_times[-1], 700)]).tolist()
    direct = simulate_closed_loop(request)
    save(output/"diagnostic_request.json", request)
    save(output/"diagnostic_prediction.json", direct)
    sample_times = np.asarray(direct["time_s"])
    rv_values = np.asarray(direct["volume_mL"]["RV"])
    def endpoint(prediction):
        return float(np.interp(target_time, prediction["time_s"],
            prediction["volume_mL"]["RV"]))
    reference_values = reference(request)
    native_values = np.column_stack([direct["volume_mL"][name] for name in COMPARTMENTS])
    routed = load_registry().capability("patient.circulation.simulate").invoke(
        "patient.circulation.simulate", request)
    command = {"subject_id": request["subject_id"], "parameters": {},
        "context": {"patient_forward": {"capability": "patient.circulation.simulate",
            "template": request, "parameter_paths": {}, "sampling": []}}}
    completed = subprocess.run([sys.executable, "-m", "hearttwin.patient_domain_forward"],
        env=dict(os.environ, HEARTTWIN_PAYLOAD=json.dumps(command)), capture_output=True,
        text=True, timeout=120)
    (output/"command-forward.stderr.log").write_text(completed.stderr)
    if completed.returncode:
        raise RuntimeError("Command forward failed; inspect retained stderr log")
    forwarded = json.loads(completed.stdout)
    check = {
        "RV_target_time_s": target_time, "RV_observed_mL": target,
        "RV_predicted_mL": endpoint(direct),
        "RV_signed_error_mL": endpoint(direct)-target,
        "target_is_exact_output_sample": bool(np.any(sample_times == target_time)),
        "dense_RV_minimum_mL": float(rv_values.min()),
        "dense_RV_minimum_time_s": float(sample_times[np.argmin(rv_values)]),
        "native_vs_DOP853_max_all_volume_difference_mL":
            float(np.max(np.abs(native_values-reference_values))),
        "registry_vs_direct_max_volume_difference_mL": compare(direct, routed),
        "command_vs_direct_max_volume_difference_mL": compare(direct, forwarded),
        "mass_drift_mL": direct["maximum_volume_drift_mL"],
        "scope": "HeartTwin native solver, registry and command routes; other numerical engines are not silently substituted"}
    save(output/"solver_transport_checks.json", check)
    checks_ok = (check["native_vs_DOP853_max_all_volume_difference_mL"] < .02
        and check["registry_vs_direct_max_volume_difference_mL"] < 1e-8
        and check["command_vs_direct_max_volume_difference_mL"] < 1e-8)
    if not checks_ok:
        save(output/"STATUS.json", {"status": "NUMERICAL_OR_TRANSPORT_MISMATCH",
            "clinical_validation": "NOT_ESTABLISHED"})
        raise RuntimeError("Numerical/transport mismatch; do not attribute error to physiology")

    def evaluate(change, name):
        changed = deepcopy(request)
        change(changed)
        changed["output_dir"] = str(output/"artifacts"/name)
        return simulate_closed_loop(changed)

    def set_parameter(p, name, value):
        if name == "pulmonary_resistance":
            p["edge_resistance_mmHg_s_per_mL"][6] = value
        elif name == "RV_outflow_resistance":
            p["edge_resistance_mmHg_s_per_mL"][5] = value
        elif name == "PA_compliance":
            p["compartments"]["PA"]["compliance_mL_per_mmHg"] = value
        else:
            p["compartments"]["RV"][name] = value

    coordinates = {
        "emax_mmHg_per_mL": request["compartments"]["RV"]["emax_mmHg_per_mL"],
        "activation_duration_s": request["compartments"]["RV"]["activation_duration_s"],
        "emin_mmHg_per_mL": request["compartments"]["RV"]["emin_mmHg_per_mL"],
        "pulmonary_resistance": request["edge_resistance_mmHg_s_per_mL"][6],
        "RV_outflow_resistance": request["edge_resistance_mmHg_s_per_mL"][5],
        "PA_compliance": request["compartments"]["PA"]["compliance_mL_per_mmHg"]}
    sweeps = []
    for name, base_value in coordinates.items():
        row = {"parameter": name, "base_value": base_value, "trials": []}
        for factor in [.8, 1.2]:
            try:
                prediction = evaluate(
                    lambda p, n=name, v=base_value*factor: set_parameter(p,n,v),
                    "sensitivity-"+name+"-"+str(factor))
                row["trials"].append({"factor": factor, "RV_endpoint_mL": endpoint(prediction),
                    "RV_endpoint_delta_mL": endpoint(prediction)-endpoint(direct),
                    "max_LV_trajectory_delta_mL": float(np.max(np.abs(
                        np.asarray(prediction["volume_mL"]["LV"])-direct["volume_mL"]["LV"])))})
            except (ValueError, RuntimeError) as error:
                row["trials"].append({"factor": factor, "status": "FAILED", "error": str(error)})
        sweeps.append(row)
        save(output/"sensitivity.json", sweeps)

    constraints = []
    if args.constraint_fit:
        # Several RV strength/loading combinations may fit this one scalar.
        # These use the former assessment point explicitly as a fitting target.
        for factor in [.5, 1., 2.]:
            trial = {"RV_outflow_resistance_factor": factor,
                "assessment_status": "DEVELOPMENT_FIT_TO_PREVIOUSLY_SEEN_POINT"}
            def residual(strength):
                def change(p):
                    set_parameter(p,"RV_outflow_resistance",coordinates["RV_outflow_resistance"]*factor)
                    set_parameter(p,"emax_mmHg_per_mL",strength)
                return endpoint(evaluate(change,"constraint-working"))-target
            low = max(.05, request["compartments"]["RV"]["emin_mmHg_per_mL"]*1.01)
            high = max(3., low*2)
            try:
                lower, upper = residual(low), residual(high)
                trial["bracket"] = [low, high]
                trial["bracket_residuals_mL"] = [lower, upper]
                if lower*upper > 0:
                    trial["status"] = "NO_BRACKETED_SOLUTION"
                else:
                    strength = float(brentq(residual, low, high, xtol=1e-7))
                    constrained = deepcopy(request)
                    set_parameter(constrained,"RV_outflow_resistance",
                        coordinates["RV_outflow_resistance"]*factor)
                    set_parameter(constrained,"emax_mmHg_per_mL",strength)
                    constrained["output_dir"] = str(output/"artifacts"/("constraint-"+str(factor)))
                    prediction = simulate_closed_loop(constrained)
                    trial.update(status="CONDITIONAL_SOLUTION", RV_emax_mmHg_per_mL=strength,
                        RV_endpoint_mL=endpoint(prediction),
                        RV_peak_pressure_mmHg=float(max(prediction["pressure_mmHg"]["RV"])),
                        max_LV_trajectory_delta_mL=float(np.max(np.abs(
                            np.asarray(prediction["volume_mL"]["LV"])-direct["volume_mL"]["LV"]))))
                    save(output/("constraint-"+str(factor)+"-request.json"), constrained)
                    save(output/("constraint-"+str(factor)+"-prediction.json"), prediction)
            except (ValueError, RuntimeError) as error:
                trial.update(status="FAILED", error=str(error))
            constraints.append(trial)
            save(output/"development_constraints.json", constraints)
    save(output/"STATUS.json", {"status":"DIAGNOSIS_COMPLETED",
        "software_checks_passed":checks_ok, "RV_error_repaired":False,
        "clinical_validation":"NOT_ESTABLISHED", "independent_patient_validation":False,
        "source_request_unchanged":digest(request_path)==protocol["request_sha256"],
        "interpretation":"Sensitivity and conditional solutions are diagnoses, not identified patient parameters or a validated RV repair"})
    print(json.dumps({"checks":check,"constraints":constraints},indent=2),flush=True)


if __name__ == "__main__":
    main()
