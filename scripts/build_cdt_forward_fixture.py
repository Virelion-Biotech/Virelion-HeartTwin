#!/usr/bin/env python3
"""Build a deterministic reference fixture by executing upstream CDT modules.

This intentionally avoids the stochastic SMC-ABC personalization loop. It uses the
published DTI004 geometry/data, the pinned upstream classes, a fixed propagation
parameter vector, and the upstream Eikonal/Dijkstra implementation. The resulting
NPZ is the numerical reference for CardiSim's native backend.
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
    for name in (
        "geometry_functions",
        "conduction_system",
        "propagation_models",
        "cellular_models",
    ):
        sys.modules.pop(name, None)
    return {
        "geometry": importlib.import_module("geometry_functions"),
        "conduction": importlib.import_module("conduction_system"),
        "propagation": importlib.import_module("propagation_models"),
        "cellular": importlib.import_module("cellular_models"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject", default="DTI004")
    args = parser.parse_args()

    if not args.upstream.exists():
        raise RuntimeError(f"Upstream checkout does not exist: {args.upstream}")
    if not args.data_root.exists():
        raise RuntimeError(f"Reference data root does not exist: {args.data_root}")

    src = args.upstream / "src"
    modules = _import_upstream(src)
    geometry_mod = modules["geometry"]
    conduction_mod = modules["conduction"]
    propagation_mod = modules["propagation"]
    cellular_mod = modules["cellular"]

    geometry_dir = args.data_root / "geometric_data"
    if not geometry_dir.is_dir():
        alternate = args.data_root / "geometric_data_ruben"
        if alternate.is_dir():
            geometry_dir = alternate
        else:
            raise RuntimeError("No upstream geometric_data directory found")

    cellular = cellular_mod.StepFunctionUpstrokeEP(
        resting_vm_value=0.0,
        upstroke_vm_value=1.0,
        verbose=False,
    )
    geometry = geometry_mod.EikonalGeometry(
        cellular_model=cellular,
        celltype_vc_info={},
        conduction_system=conduction_mod.EmptyConductionSystem(verbose=False),
        geometric_data_dir=str(geometry_dir) + os.sep,
        resolution="coarse",
        subject_name=args.subject,
        vc_name_list=["ab", "tm", "rt", "tv"],
        verbose=False,
    )

    conduction = conduction_mod.PurkinjeSystemVC(
        approx_djikstra_purkinje_max_path_len=200,
        geometry=geometry,
        lv_inter_root_node_distance=1.5,
        rv_inter_root_node_distance=1.5,
        verbose=False,
    )
    geometry.set_conduction_system(conduction)

    parameter_names = [
        "fibre_speed",
        "sheet_speed",
        "normal_speed",
        "endo_dense_speed",
        "endo_sparse_speed",
        "purkinje_speed",
    ]
    parameter_values = np.asarray([0.065, 0.051, 0.048, 0.065, 0.060, 0.300], dtype=float)
    root_count = geometry.get_nb_candidate_root_node()
    parameter_vector = np.concatenate((parameter_values, np.ones(root_count, dtype=float)))

    propagation = propagation_mod.EikonalDjikstraTet(
        endo_dense_speed_name="endo_dense_speed",
        endo_sparse_speed_name="endo_sparse_speed",
        fibre_speed_name="fibre_speed",
        geometry=geometry,
        module_name="propagation_module",
        nb_speed_parameters=len(parameter_values),
        normal_speed_name="normal_speed",
        parameter_name_list_in_order=parameter_names + [f"r{i}" for i in range(root_count)],
        purkinje_speed_name="purkinje_speed",
        sheet_speed_name="sheet_speed",
        verbose=False,
    )

    activation = np.asarray(propagation.simulate_lat(parameter_vector), dtype=np.float64)
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        activation_time_ms=activation,
        node_xyz=np.asarray(geometry.get_node_xyz(), dtype=np.float64),
        tetrahedra=np.asarray(geometry.get_tetra(), dtype=np.int64),
        root_nodes=np.asarray(geometry.get_candidate_root_node_index(), dtype=np.int64),
        parameter_values=parameter_values,
    )
    metadata = {
        "repository": "juliacamps/Cardiac-Digital-Twin",
        "commit": UPSTREAM_COMMIT,
        "subject": args.subject,
        "geometry_root": str(geometry_dir.resolve()),
        "parameter_names": parameter_names,
        "parameter_values": parameter_values.tolist(),
        "root_count": int(root_count),
        "activation_shape": list(activation.shape),
        "activation_min_ms": float(activation.min()),
        "activation_max_ms": float(activation.max()),
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
