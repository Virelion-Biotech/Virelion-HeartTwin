"""Explicitly parameterized phenomenological reaction/diffusion research backend."""

from __future__ import annotations

import json

import numpy as np

from .patient_geometry import write_artifact
from .patient_observations import verified_bytes
from .patient_volume_conductor import tetra_operators


def simulate_spatial_ep(payload):
    """Mass-lumped P1 diffusion, backward Euler; explicit cubic excitation/recovery.

    du/dt = div(D grad u) + (u*(u-a)*(1-u)-w+I)/tau_u
    dw/dt = (u-b*w)/tau_w. Insulated boundary, dimensionless u,w,I.
    This is a declared phenomenological model, not a human ion-channel model.
    """
    from scipy.sparse import diags
    from scipy.sparse.linalg import splu

    mesh_ref = payload["mesh"]
    mesh = json.loads(verified_bytes(mesh_ref))
    if mesh["subject_id"] != payload["subject_id"]:
        raise ValueError("Spatial model mesh belongs to a different patient")
    k, mass, geometry = tetra_operators(mesh, payload["diffusion_tensor_m2_per_s"])
    n = len(geometry[0])
    u, w = (np.asarray(payload[key], dtype=float) for key in ("initial_u", "initial_w"))
    if any(x.shape != (n,) or not np.isfinite(x).all() for x in (u, w)):
        raise ValueError("Finite initial excitation and recovery at every mesh node required")
    p = payload["parameters"]
    if set(p) != {"a", "b", "tau_u_s", "tau_w_s", "rest_V", "amplitude_V"}:
        raise ValueError("All cubic-model and voltage-scaling parameters must be explicit")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) for x in p.values()):
        raise ValueError("Finite model parameters required")
    dt, duration = payload["dt_s"], payload["duration_s"]
    if (not np.isfinite([dt, duration]).all() or not 0 < p["a"] < 1 or p["b"] <= 0
            or min(p["tau_u_s"], p["tau_w_s"], p["amplitude_V"]) <= 0
            or dt <= 0 or duration <= 0 or dt > 0.05 * min(p["tau_u_s"], p["tau_w_s"])):
        raise ValueError("Positive parameters and a conservative explicit reaction timestep required")
    steps = round(duration / dt)
    if steps < 1 or steps > 100000 or not np.isclose(steps * dt, duration) or (steps + 1) * n > 10000000:
        raise ValueError("Duration must be an integer timestep count within the research output limit")
    stimuli = payload["stimuli"]
    for stim in stimuli:
        ids = stim["node_ids"]
        if (not ids or len(set(ids)) != len(ids) or any(type(i) is not int or not 0 <= i < n for i in ids)
                or not np.isfinite([stim["start_s"], stim["end_s"], stim["amplitude"]]).all()
                or not 0 <= stim["start_s"] < stim["end_s"] <= duration
                or not all(np.isclose(t / dt, round(t / dt)) for t in [stim["start_s"], stim["end_s"]])):
            raise ValueError("Stimuli require unique nodes and timestep-aligned finite intervals")
    lu = splu((diags(mass) + dt * k).tocsc())
    history = [u.copy()]
    for step in range(steps):
        current = np.zeros(n)
        for stim in stimuli:
            if round(stim["start_s"] / dt) <= step < round(stim["end_s"] / dt):
                current[stim["node_ids"]] += stim["amplitude"]
        du = (u * (u - p["a"]) * (1 - u) - w + current) / p["tau_u_s"]
        recovery = w + dt * (u - p["b"] * w) / p["tau_w_s"]
        u = lu.solve(mass * (u + dt * du))
        w = recovery
        if not np.isfinite(u).all() or not np.isfinite(w).all() or max(np.abs(u).max(), np.abs(w).max()) > 10:
            raise ValueError("Excitation/recovery became unstable; refine parameters/timestep")
        history.append(u.copy())
    times = np.arange(steps + 1) * dt
    voltage = p["rest_V"] + p["amplitude_V"] * np.asarray(history)
    output = {"schema": "hearttwin-cubic-excitable-v1", "subject_id": payload["subject_id"], "mesh": mesh_ref,
              "time_s": times.tolist(), "node_ids": list(range(n)), "voltage_V": voltage.tolist(),
              "model": "du=div(D grad u)+(u(u-a)(1-u)-w+I)/tau_u; dw=(u-bw)/tau_w",
              "parameters": p, "stimuli": stimuli, "initial_u": payload["initial_u"], "initial_w": payload["initial_w"],
              "diffusion_tensor_m2_per_s": payload["diffusion_tensor_m2_per_s"],
              "qualification": "Phenomenological excitation only; no human ion-channel or clinical qualification"}
    if payload.get("leadfield"):
        lf = json.loads(verified_bytes(payload["leadfield"]))
        if (lf["schema"] != "hearttwin-reciprocal-p1-v1" or lf["subject_id"] != payload["subject_id"]
                or lf["coordinate_frame"] != payload["coordinate_frame"]):
            raise ValueError("Lead-field patient and coordinate frame must match exactly")
        torso = json.loads(verified_bytes(lf["mesh"]))
        mapping = payload["leadfield_node_ids"]
        if (len(mapping) != n or len(set(mapping)) != n
                or any(type(i) is not int or not 0 <= i < len(torso["nodes"]) for i in mapping)
                or not np.array_equal(np.asarray(torso["nodes"])[mapping], np.asarray(mesh["nodes"]))):
            raise ValueError("Explicit exact shared heart-to-torso node correspondence required")
        mapped_cells = {tuple(sorted(mapping[i] for i in cell)) for cell in mesh["tetrahedra"]}
        heart_cells = {tuple(sorted(torso["tetrahedra"][e])) for e in lf["myocardial_element_ids"]}
        if mapped_cells != heart_cells:
            raise ValueError("Propagation mesh must equal the conductor's myocardial elements; torso propagation is forbidden")
        operator = np.asarray(lf["operator_V_per_V"], dtype=float)
        if operator.shape != (12, len(torso["nodes"])) or not np.isfinite(operator).all():
            raise ValueError("Invalid native lead-field operator")
        output.update(leadfield=payload["leadfield"], leadfield_node_ids=mapping,
                      lead_names=lf["lead_names"], ecg_V=(voltage @ operator[:, mapping].T).tolist())
    return {"spatial_ep": write_artifact(payload["output_dir"], "spatial-ep", output)}
