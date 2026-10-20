"""Eight-compartment, four-chamber closed-loop research circulation."""

from __future__ import annotations

import numpy as np

from .patient_geometry import write_artifact

COMPARTMENTS = ["LA", "LV", "SA", "SV", "RA", "RV", "PA", "PV"]
VALVE_EDGES = {0, 1, 4, 5}


def hemodynamic_observables(time_s, volumes, pressures, flows):
    """Named projections for patient fitting; ranges are not labelled stroke volume."""
    v, p, q = (np.asarray(x, dtype=float) for x in (volumes, pressures, flows))
    scalars, units = {}, {}
    for i, name in enumerate(COMPARTMENTS):
        scalars[name + "_final_pressure"] = float(p[-1, i])
        scalars[name + "_volume_range"] = float(v[:, i].max() - v[:, i].min())
        units[name + "_final_pressure"] = "mmHg"
        units[name + "_volume_range"] = "mL"
    return {"time_s": list(time_s), "pressure_mmHg": {name: p[:, i].tolist() for i, name in enumerate(COMPARTMENTS)},
            "volume_mL": {name: v[:, i].tolist() for i, name in enumerate(COMPARTMENTS)},
            "flow_mL_per_s": {name + "_to_" + COMPARTMENTS[(i+1) % 8]: q[:, i].tolist() for i, name in enumerate(COMPARTMENTS)},
            "scalar_outputs": scalars, "units": units}


def simulate_closed_loop(payload):
    """Time-varying elastance chambers, compliant vessels, ideal diode valves.

    Each edge i connects compartment i to (i+1)%8. No volume source/sink.
    All physical parameters and initial conditions must be supplied explicitly.
    """
    from scipy.integrate import solve_ivp

    params = payload["compartments"]
    if set(params) != set(COMPARTMENTS):
        raise ValueError("Declare LA, LV, SA, SV, RA, RV, PA and PV compartments")
    cycle = float(payload["cycle_length_s"])
    times = np.asarray(payload["time_s"], dtype=float)
    resistance = np.asarray(payload["edge_resistance_mmHg_s_per_mL"], dtype=float)
    initial = np.asarray(payload["initial_volumes_mL"], dtype=float)
    if (not np.isfinite(cycle) or cycle <= 0 or times.ndim != 1 or not 2 <= len(times) <= 100000
            or not np.isfinite(times).all() or times[0] != 0 or np.any(np.diff(times) <= 0)
            or resistance.shape != (8,) or not np.isfinite(resistance).all() or np.any(resistance <= 0)
            or initial.shape != (8,) or not np.isfinite(initial).all() or np.any(initial < 0)):
        raise ValueError("Explicit finite cycle, times, eight positive resistances and nonnegative volumes required")
    for name, values in params.items():
        chamber = name in {"LA", "LV", "RA", "RV"}
        keys = {"unstressed_volume_mL", "external_pressure_mmHg"}
        keys |= {"emin_mmHg_per_mL", "emax_mmHg_per_mL", "activation_start_s", "activation_duration_s"} if chamber else {"compliance_mL_per_mmHg"}
        if set(values) != keys or not np.isfinite(list(values.values())).all() or values["unstressed_volume_mL"] < 0:
            raise ValueError("Declare exact finite chamber/vessel constitutive parameters")
        if chamber:
            if (not 0 < values["emin_mmHg_per_mL"] <= values["emax_mmHg_per_mL"]
                    or not 0 <= values["activation_start_s"] < cycle
                    or not 0 < values["activation_duration_s"] <= cycle):
                raise ValueError("Invalid explicit chamber elastance or activation timing")
        elif values["compliance_mL_per_mmHg"] <= 0:
            raise ValueError("Positive vessel compliance required")
    solver = payload["solver"]
    if (set(solver) != {"rtol", "atol_mL", "max_step_s"} or not np.isfinite(list(solver.values())).all()
            or not 0 < solver["rtol"] < 1 or solver["atol_mL"] <= 0
            or not 0 < solver["max_step_s"] <= cycle / 20):
        raise ValueError("Declare positive integration tolerances and max_step_s <= cycle/20")

    def pressure(t, volumes):
        result = []
        for i, name in enumerate(COMPARTMENTS):
            p = params[name]
            if name in {"LA", "LV", "RA", "RV"}:
                elapsed = (t - p["activation_start_s"]) % cycle
                activation = np.sin(np.pi * elapsed / p["activation_duration_s"]) ** 2 if elapsed < p["activation_duration_s"] else 0.
                elastance = p["emin_mmHg_per_mL"] + activation * (p["emax_mmHg_per_mL"] - p["emin_mmHg_per_mL"])
            else:
                elastance = 1 / p["compliance_mL_per_mmHg"]
            result.append(p["external_pressure_mmHg"] + elastance * (volumes[i] - p["unstressed_volume_mL"]))
        return np.asarray(result)

    def flows(t, volumes):
        p = pressure(t, volumes)
        q = (p - np.roll(p, -1)) / resistance
        for i in VALVE_EDGES:
            q[i] = max(q[i], 0.)
        return q

    def rhs(t, volumes):
        q = flows(t, volumes)
        return np.roll(q, 1) - q

    def negative_volume(t, volumes):
        return float(np.min(volumes))

    negative_volume.terminal = True
    negative_volume.direction = -1
    solution = solve_ivp(rhs, (times[0], times[-1]), initial, method="RK45", t_eval=times,
                         rtol=solver["rtol"], atol=solver["atol_mL"], max_step=solver["max_step_s"], events=negative_volume)
    if not solution.success or solution.y.shape != (8, len(times)) or not np.isfinite(solution.y).all() or np.any(solution.y < 0):
        raise ValueError("Closed-loop integration failed or produced nonphysical negative volume")
    volume = solution.y.T
    drift = float(np.max(np.abs(volume.sum(axis=1) - initial.sum())))
    if drift > 1e-8 * max(float(initial.sum()), 1):
        raise ValueError("Closed-loop blood-volume conservation failed")
    output = {"schema": "hearttwin-four-chamber-closed-loop-v1", "subject_id": payload["subject_id"],
              "compartment_names": COMPARTMENTS, "edge_order": [f"{a}->{b}" for a, b in zip(COMPARTMENTS, COMPARTMENTS[1:] + COMPARTMENTS[:1])],
              "time_s": times.tolist(), "volumes_mL": volume.tolist(),
              "pressures_mmHg": [pressure(t, v).tolist() for t, v in zip(times, volume)],
              "flows_mL_per_s": [flows(t, v).tolist() for t, v in zip(times, volume)],
              "maximum_volume_drift_mL": drift, "parameters": payload,
              "qualification": "Lumped elastance/compliance with ideal diode valves; no spatial wall mechanics, valve inertia, reflex control or clinical qualification"}
    return {"circulation": write_artifact(payload["output_dir"], "closed-loop", output), "maximum_volume_drift_mL": drift,
            **hemodynamic_observables(times.tolist(), volume, output["pressures_mmHg"], output["flows_mL_per_s"])}
