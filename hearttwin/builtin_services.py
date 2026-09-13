"""Native HeartTwin service implementations."""
from __future__ import annotations
from typing import Any
import numpy as np
from .cardiac_twin import CardiacDigitalTwin, ConductionNetwork, EPParameters, MeshGeometry, ScarMap

def cardiac_digital_twin(payload: dict[str, Any]) -> dict[str, Any]:
    geometry_payload = payload.get("geometry")
    if not isinstance(geometry_payload, dict):
        raise ValueError("simulation.cardiac_twin requires a geometry object")
    scar = None
    if geometry_payload.get("scar_labels") is not None:
        scar = ScarMap(np.asarray(geometry_payload["scar_labels"], dtype=int))
    geometry = MeshGeometry(
        np.asarray(geometry_payload["node_xyz"], dtype=float),
        np.asarray(geometry_payload["tetrahedra"], dtype=int),
        fibre=None if geometry_payload.get("fibre") is None else np.asarray(geometry_payload["fibre"], dtype=float),
        scar=scar,
    )
    roots = tuple(int(x) for x in payload.get("root_nodes", []))
    if not roots:
        raise ValueError("root_nodes must contain at least one mesh node")
    params = EPParameters(**payload.get("parameters", {}))
    twin = CardiacDigitalTwin(geometry, ConductionNetwork(roots), params=params)
    sim = twin.simulate(with_ecg=bool(payload.get("with_ecg", True)))
    result: dict[str, Any] = {
        "backend": "virelion-cdt-compatible",
        "activation_ms": sim.activation.tolist(),
        "apd_ms": sim.apd.tolist(),
        "repolarization_ms": sim.repolarization.tolist(),
        "parameters": sim.parameters.__dict__,
        "root_nodes": list(roots),
    }
    if sim.ecg is not None:
        result["ecg"] = {"lead_names": list(sim.ecg.lead_names), "sample_rate_hz": sim.ecg.sample_rate_hz, "values": sim.ecg.values.tolist()}
    return result
