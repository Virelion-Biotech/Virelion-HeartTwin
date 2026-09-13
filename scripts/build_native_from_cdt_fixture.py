#!/usr/bin/env python3
"""Run the Virelion native backend on an upstream-generated fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from hearttwin.cardiac_twin import CardiacDigitalTwin, ConductionNetwork, EPParameters, MeshGeometry


PARAMETER_NAMES = (
    "fibre_speed",
    "sheet_speed",
    "normal_speed",
    "endo_dense_speed",
    "endo_sparse_speed",
    "purkinje_speed",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ref = np.load(args.reference)
    values = np.asarray(ref["parameter_values"], dtype=float)
    params = EPParameters(**dict(zip(PARAMETER_NAMES, values)))
    root_nodes = tuple(int(x) for x in ref["root_nodes"].tolist())
    root_times = np.asarray(ref["root_activation_ms"], dtype=float)
    root_map = dict(zip(root_nodes, root_times.tolist()))

    geometry = MeshGeometry(
        node_xyz=ref["node_xyz"],
        tetrahedra=ref["tetrahedra"],
        edge_nodes=ref["edge_nodes"],
        edge_fibre_sheet_normal=ref["edge_fibre_sheet_normal"],
    )
    twin = CardiacDigitalTwin(
        geometry=geometry,
        conduction=ConductionNetwork(root_nodes, root_activation_ms=root_map),
        params=params,
        legacy_output=True,
    )
    simulation = twin.simulate(with_ecg=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, activation_time_ms=simulation.activation)
    metadata = {
        "backend": "virelion-native",
        "source_fixture": str(args.reference.resolve()),
        "node_count": int(len(simulation.activation)),
        "activation_min_ms": float(simulation.activation.min()),
        "activation_max_ms": float(simulation.activation.max()),
    }
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
