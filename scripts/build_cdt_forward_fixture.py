#!/usr/bin/env python3
"""Build a deterministic upstream Cardiac-Digital-Twin forward fixture.

This is intentionally a forward-model gate, not the stochastic SMC-ABC
personalization workflow. It captures the exact mesh edge ordering, fibre
basis, root activation times and LAT output needed for a fair native comparison.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import numpy as np

UPSTREAM_COMMIT = "816d51fab0837cfe9e20c7d3a318429e9acf0733"


def _import_upstream(src: Path):
    sys.path.insert(0, str(src))
    for name in ("geometry_functions", "conduction_system", "propagation_models", "cellular_models"):
        sys.modules.pop(name, None)
    return {
        "geometry": importlib.import_module("geometry_functions"),
        "conduction": importlib.import_module("conduction_system"),
        "propagation": importlib.import_module("propagation_models"),
        "cellular": importlib.import_module("cellular_models"),
    }


def _find_geometry_root(data_root: Path) -> Path:
    for name in ("geometric_data", "geometric_data_ruben"):
        candidate = data_root / name
        if candidate.is_dir():
            return candidate
    raise RuntimeError("No geometric_data or geometric_data_ruben directory found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject", default="DTI004")
    parser.add_argument("--purkinje-max-path", type=float, default=200.0)
    parser.add_argument("--lv-root-distance", type=float, default=1.5)
    parser.add_argument("--rv-root-distance", type=float, default=1.5)
    args = parser.parse_args()

    if not args.upstream.exists():
        raise RuntimeError(f"Upstream checkout does not exist: {args.upstream}")
    geometry_root = _find_geometry_root(args.data_root)
    modules = _import_upstream(args.upstream / "src")

    geometry_mod = modules["geometry"]
    conduction_mod = modules["conduction"]
    propagation_mod = modules["propagation"]
    cellular_mod = modules["cellular"]

    cellular = cellular_mod.StepFunctionUpstrokeEP(0.0, 1.0, verbose=False)
    geometry = geometry_mod.EikonalGeometry(
        cellular_model=cellular,
        celltype_vc_info={},
        conduction_system=conduction_mod.EmptyConductionSystem(verbose=False),
        geometric_data_dir=str(geometry_root) + os.sep,
        resolution="coarse",
        subject_name=args.subject,
        vc_name_list=["ab", "tm", "rt", "tv"],
        verbose=False,
    )
    conduction = conduction_mod.PurkinjeSystemVC(
        approx_djikstra_purkinje_max_path_len=args.purkinje_max_path,
        geometry=geometry,
        lv_inter_root_node_distance=args.lv_root_distance,
        rv_inter_root_node_distance=args.rv_root_distance,
        verbose=False,
    )
    geometry.set_conduction_system(conduction)

    names = ["fibre_speed", "sheet_speed", "normal_speed", "endo_dense_speed", "endo_sparse_speed", "purkinje_speed"]
    values = np.asarray([0.065, 0.051, 0.048, 0.065, 0.060, 0.300], dtype=float)
    root_count = geometry.get_nb_candidate_root_node()
    parameter_vector = np.concatenate((values, np.ones(root_count, dtype=float)))

    propagation = propagation_mod.EikonalDjikstraTet(
        endo_dense_speed_name="endo_dense_speed",
        endo_sparse_speed_name="endo_sparse_speed",
        fibre_speed_name="fibre_speed",
        geometry=geometry,
        module_name="propagation_module",
        nb_speed_parameters=len(values),
        normal_speed_name="normal_speed",
        parameter_name_list_in_order=names + [f"r{i}" for i in range(root_count)],
        purkinje_speed_name="purkinje_speed",
        sheet_speed_name="sheet_speed",
        verbose=False,
    )
    activation = np.asarray(propagation.simulate_lat(parameter_vector), dtype=np.float64)
    root_nodes = np.asarray(geometry.get_candidate_root_node_index(), dtype=np.int64)
    root_times = np.asarray(geometry.get_candidate_root_node_time(purkinje_speed=0.300), dtype=np.float64)
    edges = np.asarray(geometry.edge, dtype=np.int64)
    basis = np.asarray(geometry.edge_fibre_sheet_normal, dtype=np.float64)

    if edges.ndim != 2 or edges.shape[1] != 2:
        raise RuntimeError(f"Unexpected upstream edge shape: {edges.shape}")
    if basis.ndim != 3 or basis.shape[1:] != (3, 3) or len(basis) != len(edges):
        raise RuntimeError(f"Unexpected upstream edge basis shape: {basis.shape}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        activation_time_ms=activation,
        node_xyz=np.asarray(geometry.get_node_xyz(), dtype=np.float64),
        tetrahedra=np.asarray(geometry.get_tetra(), dtype=np.int64),
        edge_nodes=edges,
        edge_fibre_sheet_normal=basis,
        root_nodes=root_nodes,
        root_activation_ms=root_times,
        parameter_values=values,
    )
    metadata = {
        "repository": "juliacamps/Cardiac-Digital-Twin",
        "commit": UPSTREAM_COMMIT,
        "subject": args.subject,
        "geometry_root": str(geometry_root.resolve()),
        "parameter_names": names,
        "parameter_values": values.tolist(),
        "root_count": int(root_count),
        "node_count": int(len(geometry.get_node_xyz())),
        "edge_count": int(len(edges)),
        "activation_shape": list(activation.shape),
        "activation_min_ms": float(activation.min()),
        "activation_max_ms": float(activation.max()),
    }
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
