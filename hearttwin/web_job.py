"""Isolated execution entrypoint for trusted-owner web jobs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import load_registry
from .contracts import Observation


def serialize(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def resolve_steps(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$step"}:
            parts = str(value["$step"]).split(".")
            output = results[parts.pop(0)]
            for part in parts:
                output = output[int(part)] if isinstance(output, list) else output[part]
            return output
        return {key: resolve_steps(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_steps(item, results) for item in value]
    return value


def execute(request: dict[str, Any], registry=None) -> Any:
    registry = registry or load_registry()
    kind, payload = request["kind"], request["payload"]
    if kind == "patient_ep":
        from .patient_execution import execute_patient_ep

        return execute_patient_ep(payload, registry)
    if kind == "patient_domain":
        from .patient_domain import execute_patient_domain

        return execute_patient_domain(payload, registry)
    if kind == "capability":
        adapter = registry.capability(request["capability"])
        if adapter is None or not adapter.available():
            raise ValueError("Requested service is unavailable on this worker")
        return adapter.invoke(request["capability"], payload)
    if kind == "sequence":
        results = {}
        Path("steps").mkdir(exist_ok=True)
        for step in payload["steps"]:
            print("Starting step:", step["id"], step["capability"], flush=True)
            result = execute(
                {
                    "kind": "capability",
                    "capability": step["capability"],
                    "payload": resolve_steps(step["payload"], results),
                },
                registry,
            )
            results[step["id"]] = serialize(result)
            Path("steps", step["id"] + ".json").write_text(
                json.dumps(results[step["id"]], allow_nan=False, indent=2),
                encoding="utf-8",
            )
        return {
            "steps": results,
            "scope": "Explicit sequential handoffs; no implicit biological mapping",
        }
    if "observations" in payload:
        payload = {
            **payload,
            "observations": [
                Observation.model_validate(x) for x in payload["observations"]
            ],
        }
    if kind == "mechanistic":
        from .mechanistic_workflow import run_mechanistic_twin_workflow

        return serialize(run_mechanistic_twin_workflow(registry, **payload))
    raise ValueError("Unsupported workflow kind")


def main() -> None:
    request = json.load(sys.stdin)
    result = serialize(execute(request))
    temporary = Path("result.json.tmp")
    temporary.write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace("result.json")
    print("Execution completed; scientific qualification is unchanged.", flush=True)


if __name__ == "__main__":
    main()
