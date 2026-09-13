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
        node_xyz=np.asarray(geometry_payload["node_xyz"], dtype=float),
        tetrahedra=np.asarray(geometry_payload["tetrahedra"], dtype=int),
        fibre=None if geometry_payload.get("fibre") is None else np.asarray(geometry_payload["fibre"], dtype=float),
        sheet=None if geometry_payload.get("sheet") is None else np.asarray(geometry_payload["sheet"], dtype=float),
        normal=None if geometry_payload.get("normal") is None else np.asarray(geometry_payload["normal"], dtype=float),
        edge_nodes=None if geometry_payload.get("edge_nodes") is None else np.asarray(geometry_payload["edge_nodes"], dtype=int),
        edge_fibre_sheet_normal=None if geometry_payload.get("edge_fibre_sheet_normal") is None else np.asarray(geometry_payload["edge_fibre_sheet_normal"], dtype=float),
        scar=scar,
    )

    roots = tuple(int(x) for x in payload.get("root_nodes", []))
    if not roots:
        raise ValueError("root_nodes must contain at least one mesh node")

    root_activation_payload = payload.get("root_activation_ms")
    root_activation = None if root_activation_payload is None else {
        int(k): float(v) for k, v in root_activation_payload.items()
    }

    raw_params = dict(payload.get("parameters", {}))
    if "endo_speed" in raw_params:
        raw_params.setdefault("endo_dense_speed", raw_params["endo_speed"])
        raw_params.setdefault("endo_sparse_speed", raw_params["endo_speed"])
        raw_params.pop("endo_speed")

    params = EPParameters(**raw_params)
    twin = CardiacDigitalTwin(
        geometry,
        ConductionNetwork(roots, root_activation_ms=root_activation),
        params=params,
        legacy_output=bool(payload.get("legacy_output", False)),
    )
    sim = twin.simulate(
        with_ecg=bool(payload.get("with_ecg", True)),
        duration_ms=int(payload.get("duration_ms", 600)),
    )

    result: dict[str, Any] = {
        "backend": "virelion-cdt-compatible",
        "activation_ms": sim.activation.tolist(),
        "apd_ms": sim.apd.tolist(),
        "repolarization_ms": sim.repolarization.tolist(),
        "parameters": sim.parameters.__dict__,
        "root_nodes": list(roots),
        "units": {"activation": "ms", "apd": "ms", "speed": "cm/ms"},
    }
    if sim.ecg is not None:
        result["ecg"] = {
            "lead_names": list(sim.ecg.lead_names),
            "sample_rate_hz": sim.ecg.sample_rate_hz,
            "values": sim.ecg.values.tolist(),
        }
    return result
