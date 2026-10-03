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
    rehearsal = sub.add_parser(
        "operational-rehearsal",
        help="Run the distributed production-topology rehearsal",
    )
    rehearsal.add_argument(
        "--workdir",
        default="outputs/operational-rehearsal",
    )
    rehearsal.add_argument(
        "--case-id",
        default="HEARTTWIN-OPERATIONAL-REHEARSAL",
    )
    argo_validate = sub.add_parser(
        "argo-validate",
        help="Validate one ARGO v1 patient directory and raw WFDB/EAM contracts",
    )
    argo_validate.add_argument("patient_dir")
    argo_validate.add_argument(
        "--allow-nonofficial-count",
        action="store_true",
        help="Skip the published ARGO v1 patient/count checks (fixtures only)",
    )
    argo_prepare = sub.add_parser(
        "argo-prepare",
        help="Create a blinded ARGO calibration/holdout split",
    )
    argo_prepare.add_argument("patient_dir")
    argo_prepare.add_argument("output_dir")
    argo_prepare.add_argument("--holdout-fraction", type=float, default=0.2)
    argo_prepare.add_argument("--seed", type=int, default=42)
    argo_prepare.add_argument(
        "--allow-nonofficial-count",
        action="store_true",
        help="Skip the published ARGO v1 patient/count checks (fixtures only)",
    )
    argo_surface_cohort = sub.add_parser(
        "argo-surface-cohort",
        help="Run the CardiEP surface baseline independently across an ARGO cohort",
    )
    argo_surface_cohort.add_argument("cohort_manifest")
    argo_surface_cohort.add_argument("output_root")
    argo_surface_cohort.add_argument(
        "--coordinate-unit",
        required=True,
        choices=("mm", "cm", "m"),
    )
    argo_surface_cohort.add_argument("--speed-min-cm-per-ms", required=True, type=float)
    argo_surface_cohort.add_argument("--speed-max-cm-per-ms", required=True, type=float)
    argo_surface_cohort.add_argument("--gates")
    argo_surface = sub.add_parser(
        "argo-surface-baseline",
        help="Fit CardiEP surface-Eikonal on blinded ARGO calibration LATs",
    )
    argo_surface.add_argument("split")
    argo_surface.add_argument("output_dir")
    argo_surface.add_argument(
        "--coordinate-unit",
        required=True,
        choices=("mm", "cm", "m"),
        help="Explicit unit of the ARGO/CARTO mesh coordinates; never inferred",
    )
    argo_surface.add_argument("--speed-min-cm-per-ms", required=True, type=float)
    argo_surface.add_argument("--speed-max-cm-per-ms", required=True, type=float)
    argo_surface.add_argument("--gates")
    argo_cohort_prepare = sub.add_parser(
        "argo-cohort-prepare",
        help="Prepare deterministic blinded splits for the full ARGO cohort",
    )
    argo_cohort_prepare.add_argument("dataset_root")
    argo_cohort_prepare.add_argument("output_root")
    argo_cohort_prepare.add_argument("--holdout-fraction", type=float, default=0.2)
    argo_cohort_prepare.add_argument("--seed", type=int, default=42)
    argo_cohort_score = sub.add_parser(
        "argo-cohort-score",
        help="Score all ARGO patients and aggregate with equal patient weighting",
    )
    argo_cohort_score.add_argument("cohort_manifest")
    argo_cohort_score.add_argument("predictions_root")
    argo_cohort_score.add_argument("--gates")
    argo_cohort_score.add_argument("--output-dir")
    argo_score = sub.add_parser(
        "argo-score",
        help="Score predictions against blinded held-out ARGO raw measurements",
    )
    argo_score.add_argument("split")
    argo_score.add_argument("predictions")
    argo_score.add_argument("--gates")
    argo_score.add_argument("--output")
    args = parser.parse_args()
    registry = load_registry()

    if args.cmd == "argo-validate":
        from .argo_validation import load_argo_patient

        patient = load_argo_patient(
            args.patient_dir,
            strict_official_counts=not args.allow_nonofficial_count,
        )
        print(
            json.dumps(
                {
                    "dataset": patient["dataset"],
                    "dataset_version": patient["dataset_version"],
                    "doi": patient["doi"],
                    "patient_id": patient["patient_id"],
                    "n_vertices": patient["n_vertices"],
                    "n_triangles": patient["n_triangles"],
                    "n_points": patient["n_points"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.cmd == "argo-prepare":
        from .argo_validation import prepare_argo_empirical_study

        result = prepare_argo_empirical_study(
            args.patient_dir,
            args.output_dir,
            holdout_fraction=args.holdout_fraction,
            seed=args.seed,
            strict_official_counts=not args.allow_nonofficial_count,
        )
        print(json.dumps(result["split"], indent=2, sort_keys=True))
        return
    if args.cmd == "argo-surface-cohort":
        from .argo_validation import run_argo_surface_cohort_baseline

        gates = {}
        if args.gates:
            gates_raw = json.loads(Path(args.gates).read_text(encoding="utf-8"))
            if not isinstance(gates_raw, dict):
                raise TypeError("ARGO gates JSON must contain an object")
            gates = gates_raw
        report = run_argo_surface_cohort_baseline(
            registry,
            args.cohort_manifest,
            args.output_root,
            coordinate_unit=args.coordinate_unit,
            speed_min_cm_per_ms=args.speed_min_cm_per_ms,
            speed_max_cm_per_ms=args.speed_max_cm_per_ms,
            gates=gates,
        )
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        if report["cohort_report"]["status"] == "fail":
            raise SystemExit(2)
        return
    if args.cmd == "argo-surface-baseline":
        from .argo_validation import run_argo_surface_baseline

        gates = {}
        if args.gates:
            gates_raw = json.loads(Path(args.gates).read_text(encoding="utf-8"))
            if not isinstance(gates_raw, dict):
                raise TypeError("ARGO gates JSON must contain an object")
            gates = gates_raw
        report = run_argo_surface_baseline(
            registry,
            args.split,
            args.output_dir,
            coordinate_unit=args.coordinate_unit,
            speed_min_cm_per_ms=args.speed_min_cm_per_ms,
            speed_max_cm_per_ms=args.speed_max_cm_per_ms,
            gates=gates,
        )
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        if report["holdout_report"]["status"] == "fail":
            raise SystemExit(2)
        return
    if args.cmd == "argo-cohort-prepare":
        from .argo_validation import prepare_argo_cohort

        result = prepare_argo_cohort(
            args.dataset_root,
            args.output_root,
            holdout_fraction=args.holdout_fraction,
            seed=args.seed,
        )
        print(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return
    if args.cmd == "argo-cohort-score":
        from .argo_validation import score_argo_cohort

        gates = {}
        if args.gates:
            gates_raw = json.loads(Path(args.gates).read_text(encoding="utf-8"))
            if not isinstance(gates_raw, dict):
                raise TypeError("ARGO gates JSON must contain an object")
            gates = gates_raw
        report = score_argo_cohort(
            args.cohort_manifest,
            args.predictions_root,
            gates=gates,
            output_dir=args.output_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        if report["status"] == "fail":
            raise SystemExit(2)
        return
    if args.cmd == "argo-score":
        from .argo_validation import score_argo_holdout

        gates = {}
        if args.gates:
            gates_raw = json.loads(Path(args.gates).read_text(encoding="utf-8"))
            if not isinstance(gates_raw, dict):
                raise TypeError("ARGO gates JSON must contain an object")
            gates = gates_raw
        report = score_argo_holdout(
            args.split,
            args.predictions,
            gates=gates,
        )
        encoded = json.dumps(
            report,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded, encoding="utf-8")
            print(output)
        else:
            print(encoded, end="")
        if report["status"] == "fail":
            raise SystemExit(2)
        return

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
    if args.cmd == "operational-rehearsal":
        from .rehearsal import run_operational_rehearsal

        summary = run_operational_rehearsal(
            args.workdir,
            case_id=args.case_id,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
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
        feature_columns=["gene_a", "gene_b"],
        reference_labels={row["sample_id"]: row["target"] for row in rows},
        benchmark_validation_values=[
            rows[-4]["group_id"],
            rows[-3]["group_id"],
        ],
        benchmark_test_values=[
            rows[-2]["group_id"],
            rows[-1]["group_id"],
        ],
        simulation={"preset": "mi", "n_cells": 16, "duration": 1.0, "dt": 0.25},
        seed=42,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(workflow_run.model_dump_json(indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
