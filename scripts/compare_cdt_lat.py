#!/usr/bin/env python3
"""Compare Virelion native activation times against an upstream CDT fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-abs-tol", type=float, default=0.0)
    parser.add_argument("--rmse-tol", type=float, default=0.0)
    parser.add_argument("--mismatch-fraction-tol", type=float, default=0.0)
    args = parser.parse_args()

    ref = np.asarray(np.load(args.reference)["activation_time_ms"], dtype=float)
    native = np.asarray(np.load(args.native)["activation_time_ms"], dtype=float)
    if ref.shape != native.shape:
        report = {"status": "FAIL", "reason": "shape_mismatch", "reference_shape": list(ref.shape), "native_shape": list(native.shape)}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise SystemExit(1)

    diff = native - ref
    abs_diff = np.abs(diff)
    max_abs = float(np.max(abs_diff))
    mae = float(np.mean(abs_diff))
    rmse = float(np.sqrt(np.mean(diff**2)))
    mismatch = abs_diff > args.max_abs_tol
    mismatch_fraction = float(np.mean(mismatch))
    correlation = float(np.corrcoef(ref, native)[0, 1]) if len(ref) > 1 and np.std(ref) and np.std(native) else 1.0
    passed = max_abs <= args.max_abs_tol and rmse <= args.rmse_tol and mismatch_fraction <= args.mismatch_fraction_tol
    report = {
        "status": "PASS" if passed else "FAIL",
        "n_nodes": int(len(ref)),
        "max_abs_ms": max_abs,
        "mae_ms": mae,
        "rmse_ms": rmse,
        "mismatch_fraction": mismatch_fraction,
        "correlation": correlation,
        "tolerances": {
            "max_abs_ms": args.max_abs_tol,
            "rmse_ms": args.rmse_tol,
            "mismatch_fraction": args.mismatch_fraction_tol,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
