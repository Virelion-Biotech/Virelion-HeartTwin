"""P1 tetrahedral reciprocal volume conductor; native operator, not openCARP data."""

from __future__ import annotations

import json
from collections import Counter
from itertools import combinations

import numpy as np

from .patient_geometry import write_artifact
from .patient_observations import verified_bytes

LEADS = ["I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]
ELECTRODES = ["RA", "LA", "LL", "V1", "V2", "V3", "V4", "V5", "V6"]


def tetra_operators(mesh, tensors):
    """Return stiffness, lumped volume mass, geometry; coordinates m, tensors S/m."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    nodes = np.asarray(mesh["nodes"], dtype=float)
    cells = np.asarray(mesh["tetrahedra"])
    if (mesh.get("units") != "m" or nodes.ndim != 2 or nodes.shape[1] != 3
            or not np.isfinite(nodes).all() or len(nodes) < 4):
        raise ValueError("Finite mesh nodes in metres required")
    if (cells.ndim != 2 or cells.shape[1] != 4 or cells.dtype.kind not in "iu" or not len(cells)
            or np.any(cells < 0) or np.any(cells >= len(nodes))
            or any(len(set(row)) != 4 for row in cells)
            or len({tuple(sorted(row)) for row in cells}) != len(cells)
            or set(cells.ravel()) != set(range(len(nodes)))):
        raise ValueError("Invalid tetrahedral connectivity")
    d = (nodes[cells[:, 1:]] - nodes[cells[:, :1]]).transpose(0, 2, 1)
    determinant = np.linalg.det(d)
    scale = np.linalg.norm(d, axis=(1, 2)) ** 3
    if np.any(determinant <= scale * 1e-12):
        raise ValueError("Tetrahedra must have positive, nondegenerate orientation")
    sigma = np.asarray(tensors, dtype=float)
    if (sigma.shape != (len(cells), 3, 3) or not np.isfinite(sigma).all()
            or not np.allclose(sigma, sigma.transpose(0, 2, 1), rtol=1e-12, atol=1e-14)
            or np.any(np.linalg.eigvalsh(sigma) <= 0)):
        raise ValueError("One symmetric positive-definite conductivity tensor per element required")
    gradients = np.empty((len(cells), 4, 3))
    gradients[:, 1:] = np.linalg.inv(d)
    gradients[:, 0] = -gradients[:, 1:].sum(axis=1)
    volume = determinant / 6
    local = np.einsum("e,eai,eij,ebj->eab", volume, gradients, sigma, gradients)
    rows = np.broadcast_to(cells[:, :, None], local.shape).ravel()
    cols = np.broadcast_to(cells[:, None, :], local.shape).ravel()
    stiffness = coo_matrix((local.ravel(), (rows, cols)), shape=(len(nodes), len(nodes))).tocsr()
    # Connectivity uses mesh edges, independently of tensor-induced zero entries.
    edges = np.array([(int(a), int(b)) for tet in cells for a, b in combinations(tet, 2)])
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=stiffness.shape)
    if connected_components(graph, directed=False, return_labels=False) != 1:
        raise ValueError("Volume conductor must be connected")
    mass = np.zeros(len(nodes))
    np.add.at(mass, cells.ravel(), np.repeat(volume / 4, 4))
    return stiffness, mass, (nodes, cells, gradients, volume)


def lead_matrix(electrodes, n):
    c = np.zeros((12, n))
    ra, la, ll = (electrodes[x] for x in ("RA", "LA", "LL"))
    c[0, [la, ra]] = [1, -1]
    c[1, [ll, ra]] = [1, -1]
    c[2, [ll, la]] = [1, -1]
    for row, positive, negatives in [(3, ra, [la, ll]), (4, la, [ra, ll]), (5, ll, [ra, la])]:
        c[row, positive] = 1
        c[row, negatives] = -0.5
    for row, name in enumerate(ELECTRODES[3:], 6):
        c[row, electrodes[name]] = 1
        c[row, [ra, la, ll]] = -1 / 3
    return c


def observation_matrix(record, n):
    """Reviewed linear electrode observations; every row is gauge invariant."""
    electrodes = record["node_ids"]
    if not isinstance(electrodes, dict):
        raise TypeError("Named electrode node mapping required")
    declared = record.get("observations")
    if declared is None:
        if (set(electrodes) != set(ELECTRODES)
                or any(type(x) is not int or not 0 <= x < n for x in electrodes.values())
                or len(set(electrodes.values())) != 9):
            raise ValueError("Nine distinct recorded electrode node IDs required")
        return lead_matrix(electrodes, n), list(LEADS), None
    if (not isinstance(electrodes, dict) or not 2 <= len(electrodes) <= 1024
            or any(not isinstance(k, str) or not k.strip() for k in electrodes)
            or any(type(x) is not int or not 0 <= x < n for x in electrodes.values())
            or len(set(electrodes.values())) != len(electrodes)):
        raise ValueError("Distinct named recorded electrode nodes required")
    if (not isinstance(declared, dict)
            or set(declared) != {"channel_names", "weights", "reference", "unit"}
            or declared["unit"] != "V"
            or not isinstance(declared["reference"], str) or not declared["reference"].strip()):
        raise ValueError("Explicit channel names, weights, reference and V units required")
    names, weights = declared["channel_names"], declared["weights"]
    if (not isinstance(names, list) or not 1 <= len(names) <= 512
            or any(not isinstance(x, str) or not x.strip() for x in names)
            or len(set(names)) != len(names) or not isinstance(weights, list)
            or len(weights) != len(names) or len(names) * n > 10000000):
        raise ValueError("Unique named channels within the research operator limit required")
    c = np.zeros((len(names), n))
    for row, terms in enumerate(weights):
        if (not isinstance(terms, dict) or not terms or set(terms) - set(electrodes)
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not np.isfinite(x) for x in terms.values())):
            raise ValueError("Finite weights must name recorded electrodes")
        values = np.asarray(list(terms.values()), dtype=float)
        norm = float(np.abs(values).sum())
        if not 0 < norm <= 2 + 1e-12 or abs(float(values.sum())) > 1e-12 * norm:
            raise ValueError("Observation weights must be nonzero, balanced and have L1 norm <=2")
        for name, value in terms.items():
            c[row, electrodes[name]] = value
    return c, list(names), declared


def generate_leadfield(payload):
    from scipy.sparse.linalg import splu

    mesh_ref, electrode_ref = payload["mesh"], payload["electrodes"]
    mesh = json.loads(verified_bytes(mesh_ref))
    record = json.loads(verified_bytes(electrode_ref))
    if (mesh["subject_id"] != payload["subject_id"] or record["subject_id"] != payload["subject_id"]
            or record["mesh_sha256"] != mesh_ref["sha256"]
            or record["coordinate_frame"] != payload["coordinate_frame"]
            or not record.get("reviewer")):
        raise ValueError("Reviewed electrodes must match patient, exact mesh hash and coordinate frame")
    k, _, geometry = tetra_operators(mesh, payload["total_conductivity_S_per_m"])
    nodes, cells, gradients, volume = geometry
    electrodes = record["node_ids"]
    c, lead_names, observations = observation_matrix(record, len(nodes))
    faces = Counter(tuple(sorted(face)) for tet in cells for face in combinations(tet, 3))
    if any(count > 2 for count in faces.values()):
        raise ValueError("Nonmanifold volume conductor")
    boundary = {n for face, count in faces.items() if count == 1 for n in face}
    if not set(electrodes.values()) <= boundary:
        raise ValueError("Electrodes must lie on the volume-conductor boundary")
    heart = np.asarray(payload["myocardial_element_ids"])
    intracellular = np.asarray(payload["intracellular_conductivity_S_per_m"], dtype=float)
    if (heart.ndim != 1 or heart.dtype.kind not in "iu" or not len(heart)
            or len(set(heart)) != len(heart) or np.any(heart < 0) or np.any(heart >= len(cells))):
        raise ValueError("Explicit unique myocardial element IDs required")
    if (intracellular.shape != (len(heart), 3, 3) or not np.isfinite(intracellular).all()
            or not np.allclose(intracellular, intracellular.transpose(0, 2, 1))
            or np.any(np.linalg.eigvalsh(intracellular) <= 0)
            or np.any(np.linalg.eigvalsh(np.asarray(payload["total_conductivity_S_per_m"])[heart] - intracellular) <= 0)):
        raise ValueError("Intracellular and remaining extracellular conductivities must be positive definite")
    gauge = payload["gauge_node"]
    if type(gauge) is not int or not 0 <= gauge < len(nodes):
        raise ValueError("Explicit valid gauge node required")
    free = np.delete(np.arange(len(nodes)), gauge)
    lu = splu(k[free][:, free].tocsc())
    z = np.zeros((len(nodes), len(lead_names)))
    z[free] = lu.solve(c[:, free].T)
    # Reciprocity: K phi = -Ki Vm; C phi = -Z.T Ki Vm.
    local_ki = np.einsum("e,eai,eij,ebj->eab", volume[heart], gradients[heart], intracellular, gradients[heart])
    operator = np.zeros((len(lead_names), len(nodes)))
    # Bound the temporary electrode-by-element operator, including 128-channel BSP.
    for start in range(0, len(heart), 2048):
        selected = cells[heart[start:start + 2048]]
        for first in range(0, len(lead_names), 32):
            stop = min(first + 32, len(lead_names))
            local_operator = -np.einsum(
                "eal,eab->leb", z[selected, first:stop], local_ki[start:start + 2048])
            for lead in range(first, stop):
                np.add.at(operator[lead], selected.ravel(), local_operator[lead - first].ravel())
    residual = float(np.linalg.norm(k @ z - c.T) / max(np.linalg.norm(c), 1e-30))
    if not np.isfinite(operator).all() or residual > 1e-7:
        raise ValueError("Lead-field solve failed the reciprocal residual tolerance")
    result = {"schema": "hearttwin-reciprocal-p1-v1", "subject_id": payload["subject_id"],
              "mesh": mesh_ref, "electrodes": electrode_ref, "coordinate_frame": payload["coordinate_frame"],
              "lead_names": lead_names, "node_ids": list(range(len(nodes))), "operator_V_per_V": operator.tolist(),
              "myocardial_element_ids": heart.tolist(), "gauge_node": gauge,
              "total_conductivity_S_per_m": payload["total_conductivity_S_per_m"],
              "intracellular_conductivity_S_per_m": intracellular.tolist(), "reciprocal_residual": residual,
              "boundary_condition": "insulated outer torso; point electrode reciprocal currents",
              "qualification": "research discretization; no clinical accuracy established"}
    if observations is not None:
        result.update(schema="hearttwin-reciprocal-p1-v2", observations=observations,
                      measurement_reference=observations["reference"],
                      signal_kind="body_surface_potentials", signal_unit="V")
    return {"leadfield": write_artifact(payload["output_dir"], "leadfield", result), "reciprocal_residual": residual}
