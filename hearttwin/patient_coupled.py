"""Monolithic quasi-static spatial myocardium / closed-loop circulation research model.

Four cavity pressures are Lagrange multipliers. Cavity volumes equal circuit
chamber volumes; backward-Euler circuit balances share the same Newton solve.
No chamber elastance surrogate is used in this coupled path.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import warnings
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .patient_closed_loop import COMPARTMENTS, VALVE_EDGES
from .patient_geometry import write_artifact
from .patient_observations import verified_bytes

CHAMBERS = ["LA", "LV", "RA", "RV"]
CHAMBER_SLOTS = [0, 1, 4, 5]
VESSELS = ["SA", "SV", "PA", "PV"]
PA_PER_MMHG = 133.322387415


def finite(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(name + " must be a finite number")
    if positive and value <= 0:
        raise ValueError(name + " must be positive")
    return float(value)


def load_coupling(payload):
    from cardimech.fem_backend import skew
    from cardimech.fem_mesh import cavity_volume, load_mesh

    ref = payload["mesh"]
    verified_bytes(ref)
    mesh = load_mesh(SimpleNamespace(kind="tetra_mechanics_mesh", uri=ref["uri"], sha256=ref["sha256"], metadata={}), payload["subject_id"])
    if not mesh.directions_declared:
        raise ValueError("Spatial active mechanics requires explicit element fibre and sheet directions")
    description = json.loads(verified_bytes(payload["cavities"]))
    if (description["subject_id"] != payload["subject_id"] or description["mesh_sha256"] != ref["sha256"]
            or description["coordinate_frame"] != payload["coordinate_frame"] or not description.get("reviewer")
            or set(description["faces"]) != set(CHAMBERS)):
        raise ValueError("Four reviewed cavities must match the patient, mesh and frame")
    available = {tuple(sorted(face)) for face in mesh.cavity}
    groups, seen = [], set()
    for chamber in CHAMBERS:
        faces = np.asarray(description["faces"][chamber])
        if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in "iu" or not len(faces):
            raise ValueError("Each cavity needs nonempty integer triangle faces")
        keys = {tuple(sorted(f)) for f in faces}
        if len(keys) != len(faces) or not keys <= available or keys & seen:
            raise ValueError("Cavity groups must uniquely partition reviewed mesh cavity faces")
        # Preserve the orientation already verified by CardiMech, not just membership.
        oriented = {tuple(f) for face in mesh.cavity for f in (face, np.roll(face, 1), np.roll(face, 2))}
        if any(tuple(f) not in oriented for f in faces):
            raise ValueError("Cavity face orientation disagrees with the reviewed mechanics mesh")
        edges = Counter((int(a), int(b)) for f in faces for a, b in zip(f, np.roll(f, -1)))
        if any(c != 1 or edges[b, a] != 1 for (a, b), c in edges.items()) or cavity_volume(mesh.nodes, faces) <= 0:
            raise ValueError("Each cavity must be closed, consistently oriented and positive-volume")
        seen |= keys
        groups.append(faces)
    if seen != available:
        raise ValueError("Every mesh cavity face must belong to exactly one chamber")
    fixed = {}
    for bc in payload["fixed_boundaries"]:
        if set(bc) != {"node_ids", "components", "displacement_m"}:
            raise ValueError("Fixed boundaries require node_ids, components and displacement_m")
        displacement = bc["displacement_m"]
        if len(displacement) != 3 or not bc["node_ids"] or not bc["components"]:
            raise ValueError("Explicit nonempty displacement boundary required")
        displacement = [finite(v, "displacement_m") for v in displacement]
        for node in bc["node_ids"]:
            if type(node) is not int or not 0 <= node < len(mesh.nodes):
                raise ValueError("Fixed boundary node out of range")
            for component in bc["components"]:
                if type(component) is not int or not 0 <= component < 3:
                    raise ValueError("Fixed displacement component out of range")
                dof = 3 * node + component
                if dof in fixed and fixed[dof] != displacement[component]:
                    raise ValueError("Conflicting fixed displacement boundaries")
                fixed[dof] = displacement[component]
    indices = np.array(sorted(fixed), dtype=int)
    rigid = np.zeros((mesh.nodes.size, 6))
    for i, x in enumerate(mesh.nodes - mesh.nodes.mean(axis=0)):
        rigid[3*i:3*i+3, :3] = np.eye(3)
        rigid[3*i:3*i+3, 3:] = -skew(x)
    if len(indices) < 6 or np.linalg.matrix_rank(rigid[indices]) != 6:
        raise ValueError("Constraints must remove all six rigid body modes")
    free = np.setdiff1d(np.arange(mesh.nodes.size), indices)
    if len(free) < 4:
        raise ValueError("Four cavity volume degrees of freedom must remain unconstrained")
    return mesh, groups, indices, free, np.array([fixed[i] for i in indices])


def material_assembly(mesh, groups, u, pressures_mmHg, tension, passive, external):
    from scipy.sparse import coo_matrix
    from cardimech.fem_backend import pressure_load
    from cardimech.fem_materials import constitutive

    f = np.eye(3) + np.einsum("eai,eaj->eij", u[mesh.elements], mesh.gradients)
    energy, stress, tangent, j = constitutive(f, "neo_hookean", passive, mesh.fiber, mesh.sheet, 0.)
    stretched = np.einsum("eij,ej->ei", f, mesh.fiber)
    energy += 0.5 * tension * (np.sum(stretched**2, axis=1) - 1)
    stress += tension[:, None, None] * np.einsum("ei,ej->eij", stretched, mesh.fiber)
    tangent += tension[:, None, None, None, None] * np.einsum("ik,ej,el->eijkl", np.eye(3), mesh.fiber, mesh.fiber)
    local = np.einsum("e,eij,eaj->eai", mesh.volumes, stress, mesh.gradients)
    residual = np.zeros_like(u)
    np.add.at(residual, mesh.elements, local)
    element_k = np.einsum("e,eaj,eijkl,ebl->eaibk", mesh.volumes, mesh.gradients, tangent, mesh.gradients)
    dofs = (mesh.elements[:, :, None] * 3 + np.arange(3)).reshape(-1, 12)
    rows = np.broadcast_to(dofs[:, :, None], (len(dofs), 12, 12)).ravel()
    cols = np.broadcast_to(dofs[:, None, :], (len(dofs), 12, 12)).ravel()
    k = coo_matrix((element_k.reshape(-1), (rows, cols)), shape=(u.size, u.size)).tocsr()
    volume_gradients = []
    for i, faces in enumerate(groups):
        force, pressure_tangent = pressure_load(mesh.nodes + u, faces, 1.)
        load = (pressures_mmHg[i] - external[i]) * PA_PER_MMHG
        residual -= (load * force).reshape(u.shape)
        k -= load * pressure_tangent
        volume_gradients.append(force)
    return residual.ravel(), k, np.asarray(volume_gradients), (f, stress, j, energy)


def coupled_system(x, *, mesh, groups, free, fixed, prescribed, tension, passive,
                   external, vessels, resistance, previous_volume, dt, scales):
    """Residual/Jacobian in free displacement, four pressure and eight volume variables."""
    from scipy.sparse import bmat, csr_matrix
    from cardimech.fem_mesh import cavity_volume

    nf = len(free)
    u = np.zeros_like(mesh.nodes)
    u.ravel()[fixed] = prescribed
    u.ravel()[free] = x[:nf]
    p, v = x[nf:nf+4], x[nf+4:]
    r, k, gradients, fields = material_assembly(mesh, groups, u, p, tension, passive, external)
    cavity = np.array([cavity_volume(mesh.nodes + u, faces) * 1e6 for faces in groups])
    pressure = np.zeros(8)
    dpdv, dpdp = np.zeros((8, 8)), np.zeros((8, 4))
    for i, slot in enumerate(CHAMBER_SLOTS):
        pressure[slot] = p[i]
        dpdp[slot, i] = 1.
    for name in VESSELS:
        slot = COMPARTMENTS.index(name)
        param = vessels[name]
        compliance = param["compliance_mL_per_mmHg"]
        pressure[slot] = param["external_pressure_mmHg"] + (v[slot] - param["unstressed_volume_mL"]) / compliance
        dpdv[slot, slot] = 1 / compliance
    delta = pressure - np.roll(pressure, -1)
    conductance = 1 / resistance
    for i in VALVE_EDGES:
        if delta[i] <= 0:
            conductance[i] = 0.
    flow = conductance * delta
    difference = np.eye(8) - np.roll(np.eye(8), -1, axis=0)
    dqdp = conductance[:, None] * difference
    dnetdp = np.roll(dqdp, 1, axis=0) - dqdp
    net = np.roll(flow, 1) - flow
    selector = np.eye(8)[CHAMBER_SLOTS]
    force_scale, volume_scale = scales
    residual = np.concatenate([r[free] / force_scale, (cavity - v[CHAMBER_SLOTS]) / volume_scale,
                               (v - previous_volume - dt * net) / volume_scale])
    jacobian = bmat([
        [k[free][:, free] / force_scale, csr_matrix(-PA_PER_MMHG * gradients[:, free].T / force_scale), None],
        [csr_matrix(1e6 * gradients[:, free] / volume_scale), None, csr_matrix(-selector / volume_scale)],
        [None, csr_matrix(-dt * dnetdp @ dpdp / volume_scale), csr_matrix((np.eye(8) - dt * dnetdp @ dpdv) / volume_scale)],
    ], format="csr")
    state = {"u": u, "p": pressure, "v": v, "q": flow, "cavity": cavity, "fields": fields,
             "force_residual_N": float(np.max(np.abs(r[free]))),
             "volume_mismatch_mL": float(np.max(np.abs(cavity - v[CHAMBER_SLOTS]))),
             "balance_residual_mL": float(np.max(np.abs(v - previous_volume - dt * net)))}
    return residual, jacobian, state


def simulate_coupled(payload):
    from scipy.sparse.linalg import MatrixRankWarning, spsolve
    from cardimech import fem_backend, fem_materials, fem_mesh

    mesh, groups, fixed, free, prescribed = load_coupling(payload)
    voltage = json.loads(verified_bytes(payload["voltage"]))
    if (voltage["subject_id"] != payload["subject_id"] or voltage["mesh"]["sha256"] != payload["mesh"]["sha256"]
            or voltage["node_ids"] != list(range(len(mesh.nodes)))
            or voltage.get("coordinate_frame") != payload["coordinate_frame"]):
        raise ValueError("Voltage must match exact patient mesh, node ordering and frame")
    times = np.asarray(payload["time_s"], dtype=float)
    vt = np.asarray(voltage["time_s"], dtype=float)
    vv = np.asarray(voltage["voltage_V"], dtype=float)
    if (times.ndim != 1 or not 2 <= len(times) <= 1000 or not np.isfinite(times).all() or times[0] != 0
            or np.any(np.diff(times) <= 0) or vt.ndim != 1 or len(vt) < 2 or not np.isfinite(vt).all()
            or np.any(np.diff(vt) <= 0) or vv.shape != (len(vt), len(mesh.nodes)) or not np.isfinite(vv).all()
            or times[0] < vt[0] or times[-1] > vt[-1] or len(times) * len(mesh.elements) * 12 > 5000000):
        raise ValueError("Finite aligned voltage/time series required; extrapolation and excessive output forbidden")
    passive = {k: finite(v, k, positive=True) for k, v in payload["passive_Pa"].items()}
    if set(passive) != {"mu", "kappa"}:
        raise ValueError("Explicit neo-Hookean mu/kappa in Pa required")
    activation = payload["activation"]
    if set(activation) != {"voltage_threshold_V", "voltage_slope_V", "rise_tau_s", "decay_tau_s", "maximum_tension_Pa", "initial_activation"}:
        raise ValueError("Declare exact voltage-to-active-stress parameters")
    threshold = finite(activation["voltage_threshold_V"], "voltage_threshold_V")
    slope = finite(activation["voltage_slope_V"], "voltage_slope_V", positive=True)
    rise = finite(activation["rise_tau_s"], "rise_tau_s", positive=True)
    decay = finite(activation["decay_tau_s"], "decay_tau_s", positive=True)
    maximum = np.asarray(activation["maximum_tension_Pa"], dtype=float)
    active = np.asarray(activation["initial_activation"], dtype=float)
    if (maximum.shape != (len(mesh.elements),) or active.shape != maximum.shape
            or not np.isfinite(maximum).all() or not np.isfinite(active).all()
            or np.any(maximum < 0) or np.any(active < 0) or np.any(active > 1)):
        raise ValueError("Explicit nonnegative element tensions and initial activation in [0,1] required")
    vessels = payload["vessels"]
    if set(vessels) != set(VESSELS):
        raise ValueError("Declare systemic/pulmonary arterial/venous compartments")
    for param in vessels.values():
        if set(param) != {"compliance_mL_per_mmHg", "unstressed_volume_mL", "external_pressure_mmHg"}:
            raise ValueError("Vessels require compliance, unstressed volume and external pressure")
        for name, v in param.items():
            finite(v, name, positive=name == "compliance_mL_per_mmHg")
        if param["unstressed_volume_mL"] < 0:
            raise ValueError("Unstressed volume cannot be negative")
    resistance = np.asarray(payload["edge_resistance_mmHg_s_per_mL"], dtype=float)
    previous = np.asarray(payload["initial_volumes_mL"], dtype=float)
    guess = np.asarray(payload["initial_pressure_guess_mmHg"], dtype=float)
    external = np.asarray(payload["chamber_external_pressure_mmHg"], dtype=float)
    if (resistance.shape != (8,) or previous.shape != (8,) or guess.shape != (4,) or external.shape != (4,)
            or not all(np.isfinite(a).all() for a in (resistance, previous, guess, external))
            or np.any(resistance <= 0) or np.any(previous < 0)):
        raise ValueError("Explicit finite circuit resistances, volumes and pressure guesses required")
    solver = payload["solver"]
    if set(solver) != {"force_tolerance_N", "volume_tolerance_mL", "max_iterations"}:
        raise ValueError("Declare Newton force/volume tolerances and iteration limit")
    ftol = finite(solver["force_tolerance_N"], "force_tolerance_N", positive=True)
    vtol = finite(solver["volume_tolerance_mL"], "volume_tolerance_mL", positive=True)
    iterations = solver["max_iterations"]
    if ftol > 1e-3 or vtol > 1e-2 or type(iterations) is not int or not 1 <= iterations <= 100:
        raise ValueError("Unsupported loose tolerance or Newton iteration limit")
    if not payload.get("reference_state_rationale", "").strip():
        raise ValueError("Declare the stress-free/reference-configuration assumption")
    x = np.concatenate([np.zeros(len(free)), guess, previous])
    common = dict(mesh=mesh, groups=groups, free=free, fixed=fixed, prescribed=prescribed,
                  passive=passive, external=external, vessels=vessels, resistance=resistance, scales=(ftol, vtol))
    records = []
    for step, t in enumerate(times):
        dt = 0. if step == 0 else t - times[step-1]
        if step:
            nodal = np.array([np.interp(t, vt, vv[:, node]) for node in range(len(mesh.nodes))])
            vm = nodal[mesh.elements].mean(axis=1)
            target = 1 / (1 + np.exp(-np.clip((vm - threshold) / slope, -700, 700)))
            tau = np.where(target > active, rise, decay)
            active = target + (active - target) * np.exp(-dt / tau)
        tension = maximum * active
        state = None
        for iteration in range(iterations + 1):
            residual, jacobian, state = coupled_system(x, previous_volume=previous, dt=dt, tension=tension, **common)
            norm = float(np.max(np.abs(residual)))
            if norm <= 1:
                break
            if iteration == iterations:
                raise ValueError("Monolithic spatial/circulation Newton solve did not converge")
            with warnings.catch_warnings():
                warnings.simplefilter("error", MatrixRankWarning)
                increment = spsolve(jacobian, -residual)
            if not np.isfinite(increment).all():
                raise ValueError("Coupled system singular or nonfinite; review constraints/reference state")
            accepted = False
            for power in range(18):
                trial = x + increment * 2.**(-power)
                if np.any(trial[len(free)+4:] < 0):
                    continue
                try:
                    rr, _, _ = coupled_system(trial, previous_volume=previous, dt=dt, tension=tension, **common)
                except ValueError:
                    continue
                if np.linalg.norm(rr) < np.linalg.norm(residual) * (1 - 1e-4 * 2.**(-power)):
                    x = trial
                    accepted = True
                    break
            if not accepted:
                raise ValueError("Monolithic spatial/circulation line search failed")
        f, stress, j, _ = state["fields"]
        previous = state["v"].copy()
        records.append({"time_s": float(t), "displacement_m": state["u"].tolist(),
                        "pressures_mmHg": state["p"].tolist(), "volumes_mL": previous.tolist(),
                        "flows_mL_per_s": state["q"].tolist(), "activation": active.tolist(),
                        "active_tension_Pa": tension.tolist(), "element_J": j.tolist(),
                        "cauchy_stress_Pa": (np.einsum("eij,ekj->eik", stress, f) / j[:, None, None]).tolist(),
                        "newton_iterations": iteration, "scaled_residual": norm,
                        **{k: state[k] for k in ("force_residual_N", "volume_mismatch_mL", "balance_residual_mL")}})
    engine = {module.__name__: hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest()
              for module in (fem_backend, fem_materials, fem_mesh)}
    result = {"schema": "hearttwin-monolithic-four-cavity-v1", "subject_id": payload["subject_id"],
              "coordinate_frame": payload["coordinate_frame"], "mesh": payload["mesh"], "cavities": payload["cavities"],
              "voltage": payload["voltage"], "compartment_names": COMPARTMENTS, "time_s": times.tolist(),
              "states": records, "request": payload, "mechanics_engine_sha256": engine,
              "coupling": "monolithic equilibrium, four cavity-volume constraints and eight backward-Euler volume balances",
              "qualification": "Quasi-static compressible neo-Hookean P1 myocardium, prescribed fibre active stress and ideal diode valves; no patient/clinical qualification or fluid-field FSI"}
    return {"coupled": write_artifact(payload["output_dir"], "coupled", result)}
