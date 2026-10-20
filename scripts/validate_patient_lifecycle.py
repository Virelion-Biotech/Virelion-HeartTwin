"""Software/synthetic patient checks for a prepared Colab environment."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--components", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MPLBACKEND="Agg", PYTHONUNBUFFERED="1")
    env.pop("HEARTTWIN_WEB_TOKEN", None)
    env.pop("HEARTTWIN_PATIENT_CREDENTIALS", None)
    commands = [
        (
            "python-compile",
            [sys.executable, "-m", "compileall", "-q", "hearttwin", "tests"],
        ),
        (
            "patient-and-workbench-tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *[
                    str(p.relative_to(root))
                    for p in sorted((root / "tests").glob("test_patient*.py"))
                ],
                "tests/test_web_workbench.py",
                "--junitxml=" + str(output / "patient-tests.xml"),
            ],
        ),
        (
            "patient-lint",
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "hearttwin/patient_access.py",
                "hearttwin/patient_cli.py",
                "hearttwin/patient_domain.py",
                "hearttwin/patient_domain_forward.py",
                "hearttwin/patient_execution.py",
                "hearttwin/patient_integrity.py",
                "hearttwin/patient_observations.py",
                "hearttwin/patient_personalization.py",
                "hearttwin/patient_ingest.py",
                "hearttwin/patient_hospital.py",
                "hearttwin/patient_geometry.py",
                "hearttwin/patient_volume_conductor.py",
                "hearttwin/patient_spatial_ep.py",
                "hearttwin/patient_preparation.py",
                "tests/test_patient_native_preparation.py",
                "hearttwin/patient_recipes.py",
                "tests/test_patient_personalization.py",
                "hearttwin/patient_store.py",
                "hearttwin/patient_workspace.py",
                "hearttwin/web.py",
                "hearttwin/web_job.py",
                "tests/test_patient_lifecycle.py",
                "tests/test_patient_workspace_export.py",
            ],
        ),
    ]
    commands.append(("gateway-import-order", [sys.executable, "-m", "ruff", "check", "--select", "I", "hearttwin/web.py"]))
    if shutil.which("node"):
        commands.extend(
            [
                ("javascript-syntax", ["node", "--check", "hearttwin/webui/app.js"]),
                ("geometry-tests", ["node", "--test", "tests/webui_geometry.test.cjs"]),
            ]
        )
    else:
        raise RuntimeError(
            "Node is required for workbench checks; select a standard Colab CPU runtime"
        )
    result = {
        "scope": "software and synthetic infrastructure only",
        "clinical_qualification": "not_established",
        "components_requested": args.components,
        "checks": [],
    }
    for label, command in commands:
        started = time.monotonic()
        with (output / (label + ".log")).open("w") as log:
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=1200,
                )
                code = completed.returncode
            except subprocess.TimeoutExpired:
                code = 124
        result["checks"].append(
            {"check": label, "exit_code": code, "seconds": time.monotonic() - started}
        )
        print(label, "PASSED" if code == 0 else "FAILED", flush=True)
        (output / "STATUS.json").write_text(json.dumps(result, indent=2))
    result["status"] = (
        "SOFTWARE_PASSED"
        if all(x["exit_code"] == 0 for x in result["checks"])
        else "SOFTWARE_FAILED"
    )
    result["files"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.iterdir()
        if p.is_file() and p.name != "STATUS.json"
    }
    (output / "STATUS.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if result["status"] != "SOFTWARE_PASSED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
