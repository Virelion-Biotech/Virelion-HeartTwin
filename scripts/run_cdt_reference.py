#!/usr/bin/env python3
"""Run the pinned upstream Cardiac-Digital-Twin workflow reproducibly."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

UPSTREAM_REPO = "https://github.com/juliacamps/Cardiac-Digital-Twin.git"
UPSTREAM_COMMIT = "816d51fab0837cfe9e20c7d3a318429e9acf0733"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 3600) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, timeout=timeout)


def latest_result(root: Path, pattern: str) -> Path:
    matches = sorted(root.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        raise RuntimeError(f"Reference workflow produced no file matching {pattern}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--subject", default="DTI004")
    parser.add_argument("--commit", default=UPSTREAM_COMMIT)
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    upstream = args.workdir / "Cardiac-Digital-Twin"
    results = args.workdir / "results"
    if not upstream.exists():
        run(["git", "clone", UPSTREAM_REPO, str(upstream)], timeout=600)
    run(["git", "fetch", "--depth", "1", "origin", args.commit], cwd=upstream, timeout=600)
    run(["git", "checkout", "--detach", args.commit], cwd=upstream, timeout=120)

    required = [args.data_root / name for name in ("clinical_data", "geometric_data", "cellular_data")]
    missing = [str(p) for p in required if not p.is_dir()]
    if missing:
        raise RuntimeError("Published CDT dataset is incomplete; missing: " + ", ".join(missing))

    if not args.skip_install:
        run([
            sys.executable, "-m", "pip", "install", "numpy", "scipy", "pandas", "numba",
            "pymp-pypi", "SALib", "pyDOE", "matplotlib", "plotly"
        ], timeout=1800)

    custom = upstream / ".custom_config"
    custom.mkdir(exist_ok=True)
    mapping = {
        "data_path": str(args.data_root.resolve()) + os.sep,
        "results_path": str(results.resolve()) + os.sep,
    }
    (custom / ".your_path_mapping.txt").write_text(json.dumps(mapping), encoding="utf-8")
    results.mkdir(parents=True, exist_ok=True)

    run([sys.executable, "src/personalise_to_QT.py", args.subject], cwd=upstream, timeout=6 * 3600)

    parameter_file = latest_result(results, "*_parameter_population.csv")
    theta_file = latest_result(results, "*_theta_population.csv")
    manifest = {
        "repository": "juliacamps/Cardiac-Digital-Twin",
        "commit": args.commit,
        "subject": args.subject,
        "parameter_population": str(parameter_file.resolve()),
        "theta_population": str(theta_file.resolve()),
    }
    (args.workdir / "reference_run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
