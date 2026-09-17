from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_registry
from .contracts import Observation, Provenance
from .orchestrator import HeartTwin
from .workflow import run_multimodal_workflow


def _demo_observation(entity_id: str, modality: str, values: dict) -> Observation:
    return Observation(
        observation_id=f"obs-{entity_id}-{modality}",
        modality=modality,
        values=values,
        provenance=Provenance(
            source_service="hearttwin-demo",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id="workflow-demo",
        ),
    )


def _demo_rows(n: int = 20) -> list[dict]:
    return [
        {
            "sample_id": f"S{i:03d}",
            "group_id": f"G{i:03d}",
            "study_id": "HT-DEMO",
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "gene_a": i / n,
            "gene_b": ((i * 7) % n) / n,
        }
        for i in range(n)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="hearttwin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("services")
    sub.add_parser("doctor")
    demo = sub.add_parser("demo")
    demo.add_argument("--output", default="outputs/demo-state.json")
    workflow = sub.add_parser("workflow-demo")
    workflow.add_argument("--output", default="outputs/workflow-demo.json")
    args = parser.parse_args()
    registry = load_registry()

    if args.cmd == "services":
        for service in registry.services():
            print(f"{service.name}\t{','.join(service.capabilities)}\t{service.repository}")
        return
    if args.cmd == "doctor":
        print(json.dumps(registry.doctor(), indent=2))
        return
    if args.cmd == "demo":
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        run = HeartTwin(registry).run("demo-entity", capabilities=[])
        Path(args.output).write_text(run.model_dump_json(indent=2), encoding="utf-8")
        print(args.output)
        return

    rows = _demo_rows()
    workflow_run = run_multimodal_workflow(
        registry,
        entity_id="HT-DEMO",
        observations=[
            _demo_observation("HT-DEMO", "molecular", {"gene_a": 0.4, "gene_b": 0.6}),
            _demo_observation("HT-DEMO", "structural", {"region": "left_ventricle", "zone": "IZ"}),
            _demo_observation("HT-DEMO", "clinical", {"condition": "research_fixture"}),
        ],
        benchmark_samples=[
            {
                "sample_id": row["sample_id"],
                "group_id": row["group_id"],
                "study_id": row["study_id"],
                "label": row["label"],
                "region": "IZ" if row["target"] else "remote",
            }
            for row in rows
        ],
        learning_data=rows,
        simulation={"preset": "mi", "n_cells": 16, "duration": 1.0, "dt": 0.25},
        seed=42,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(workflow_run.model_dump_json(indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
