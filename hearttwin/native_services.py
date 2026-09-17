"""Native adapters for services that can run in-process when installed.

The adapters intentionally depend on public Python APIs from the component
repositories. They return JSON-compatible dictionaries so the existing
HeartTwin ServiceAdapter boundary remains backwards-compatible.
"""
from __future__ import annotations

from typing import Any


def _native_unavailable(name: str, exc: Exception) -> RuntimeError:
    return RuntimeError(
        f"Native service {name!r} is unavailable: install the corresponding Virelion package."
    ) from exc


def invoke_native(service: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    dispatch = {
        "CardiAtlas": _cardiatlas,
        "CardiBench": _cardibench,
        "CardiEval": _cardieval,
        "CardiLearn": _cardilearn,
        "CardiSim": _cardisim,
        "CardiVex": _cardivex,
        "CardiStudio": _cardistudio,
        "DCCP": _dccp,
    }
    try:
        handler = dispatch[service]
    except KeyError as exc:
        raise ValueError(f"Unknown native HeartTwin service: {service}") from exc
    return handler(capability, payload)


def _cardiatlas(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardiatlas import AtlasService, record_from_dict
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiAtlas", exc)

    service = AtlasService.empty()
    for raw in payload.get("records", []):
        if isinstance(raw, dict):
            service.add(record_from_dict(raw))

    if capability == "atlas.search":
        query = str(payload.get("query", ""))
        record_type = payload.get("record_type")
        records = service.search(query, record_type=record_type)
        return {
            "contract_version": "1.0",
            "query": query,
            "records": [record.to_dict() for record in records],
            "count": len(records),
        }

    if capability == "atlas.context":
        context_id = str(payload.get("context_id") or f"ctx-{payload.get('entity_id', 'unknown')}")
        record_ids = [str(item) for item in payload.get("record_ids", [])]
        context = service.atlas_context(context_id, record_ids)
        return {"contract_version": "1.0", **context.to_dict()}

    raise ValueError(f"CardiAtlas does not support {capability}")


def _cardibench(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardi_bench import Sample, materialize
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiBench", exc)

    if capability != "benchmark.resolve":
        raise ValueError(f"CardiBench does not support {capability}")
    raw_samples = payload.get("samples") or []
    samples = [
        Sample(
            sample_id=str(item["sample_id"]),
            group_id=str(item["group_id"]),
            study_id=str(item["study_id"]),
            label=str(item["label"]),
            technical_group=item.get("technical_group"),
            organism=item.get("organism"),
            timepoint=item.get("timepoint"),
            cell_context=item.get("cell_context"),
            region=item.get("region"),
        )
        for item in raw_samples
    ]
    result = materialize(
        samples,
        benchmark_id=str(payload.get("benchmark_id", "hearttwin-e2e")),
        version=str(payload.get("version", "1.0")),
        policy=str(payload.get("policy", "subject_heldout")),
        seed=int(payload.get("seed", 0)),
    )
    return {
        "contract_version": "1.0",
        **result.to_dict(),
        "samples": raw_samples,
    }


def _cardilearn(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        import pandas as pd
        from cardilearn.config import SplitConfig, TrainingConfig
        from cardilearn.data import Dataset
        from cardilearn.training import train
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiLearn", exc)

    if capability not in {"learn.infer", "learn.predict"}:
        raise ValueError(f"CardiLearn does not support {capability}")

    rows = payload.get("data") or []
    if not rows:
        raise ValueError("CardiLearn native adapter requires a non-empty 'data' table")
    target = str(payload.get("target_column", "target"))
    group = payload.get("group_column", "group_id")
    frame = pd.DataFrame(rows)
    dataset = Dataset(frame=frame, target_column=target, group_column=group if group else None)
    split = SplitConfig(
        test_size=float(payload.get("test_size", 0.2)),
        validation_size=float(payload.get("validation_size", 0.2)),
        random_state=int(payload.get("seed", 42)),
        stratify=bool(payload.get("stratify", True)),
    )
    config = TrainingConfig(
        task=str(payload.get("task", "classification")),
        model=str(payload.get("model", "logistic_regression")),
        target_column=target,
        group_column=group if group else None,
        random_state=int(payload.get("seed", 42)),
        split=split,
    )
    result = train(dataset, config)
    test_idx = result.splits.test
    X_test = dataset.features().iloc[test_idx]
    y_test = dataset.target.iloc[test_idx]
    if config.task == "classification":
        model = result.model
        predictions = model.predict(X_test)
        scores = None
        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(X_test)
            if getattr(probabilities, "ndim", 1) == 2 and probabilities.shape[1] == 2:
                scores = probabilities[:, 1]
        prediction_rows = []
        source_test = frame.iloc[test_idx]
        for row, y_true, y_pred, score in zip(source_test.to_dict(orient="records"), y_test.tolist(), predictions.tolist(), scores.tolist() if scores is not None else [None] * len(predictions), strict=True):
            prediction_rows.append(
                {
                    "sample_id": str(row.get("sample_id", row.get("id", f"test-{len(prediction_rows):04d}"))),
                    "y_true": y_true,
                    "y_pred": y_pred,
                    "score": None if score is None else float(score),
                    "subgroup": None if row.get("subgroup") is None else str(row["subgroup"]),
                }
            )
    else:
        predictions = result.model.predict(X_test)
        source_test = frame.iloc[test_idx]
        prediction_rows = [
            {
                "sample_id": str(row.get("sample_id", row.get("id", f"test-{i:04d}"))),
                "y_true": y_true,
                "y_pred": y_pred,
                "score": None,
                "subgroup": None,
            }
            for i, (row, y_true, y_pred) in enumerate(zip(source_test.to_dict(orient="records"), y_test.tolist(), predictions.tolist(), strict=True))
        ]

    return {
        "contract_version": "1.0",
        "model_id": str(payload.get("model_id", config.model)),
        "task": config.task,
        "target_column": target,
        "metrics": result.metrics,
        "predictions": prediction_rows,
        "dataset_fingerprint": result.dataset_fingerprint,
    }


def _cardisim(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardisim import CardiacSimulator, SimulationConfig, population_preset
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiSim", exc)

    if capability != "simulation.run":
        raise ValueError(f"CardiSim does not support {capability}")
    config = SimulationConfig(
        duration=float(payload.get("duration", 7.0)),
        dt=float(payload.get("dt", 0.25)),
        n_cells=int(payload.get("n_cells", 64)),
        seed=int(payload.get("seed", 0)),
        heterogeneity=float(payload.get("heterogeneity", 0.05)),
        process_noise=float(payload.get("process_noise", 0.0)),
    )
    result = CardiacSimulator(config).run(population_preset(str(payload.get("preset", "baseline"))))
    summary = result.summary()
    return {
        "contract_version": "1.0",
        "backend": "Virelion-CardiSim",
        "summary": summary,
        "events": list(result.events),
        "population_size": int(config.n_cells),
    }


def _cardivex(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    if capability != "vex.observe":
        raise ValueError(f"CardiVex does not support {capability}")
    try:
        from cardivex import (
            Confidence,
            DomainValue,
            EvidenceTier,
            Scenario,
            ScenarioState,
            healthy_baseline,
            run_end_to_end,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiVex", exc)

    raw = payload.get("scenario")
    if not isinstance(raw, dict):
        raise ValueError("vex.observe requires a scenario object")

    def domain_map(mapping: dict[str, Any]) -> dict[str, DomainValue]:
        return {
            str(name): DomainValue(
                value=float(item.get("value", item) if isinstance(item, dict) else item),
                uncertainty=float(item.get("uncertainty", 0.0)) if isinstance(item, dict) else 0.0,
                evidence_status=str(item.get("evidence_status", "modeled")) if isinstance(item, dict) else "modeled",
            )
            for name, item in mapping.items()
        }

    temporal = []
    for item in raw.get("temporal_profile", []):
        temporal.append(
            ScenarioState(
                state=str(item.get("state", "state")),
                relative_time=float(item.get("relative_time", 0.0)),
                duration=float(item.get("duration", 0.0)),
                domains=domain_map(dict(item.get("domains") or {})),
            )
        )
    scenario = Scenario(
        scenario_id=str(raw["scenario_id"]),
        version=str(raw.get("version", "1.0")),
        name=str(raw.get("name", raw["scenario_id"])),
        target_model=str(raw.get("target_model", "HeartTwin")),
        evidence_tier=EvidenceTier(str(raw.get("evidence_tier", "observed"))),
        confidence=Confidence(str(raw.get("confidence", "exploratory"))),
        phenotype_domains=domain_map(dict(raw.get("phenotype_domains") or {})),
        temporal_profile=tuple(temporal),
        description=str(raw.get("description", "")),
        severity_profile={str(k): float(v) for k, v in dict(raw.get("severity_profile") or {}).items()},
        interaction_profile=tuple(raw.get("interaction_profile") or ()),
        variation_space=dict(raw.get("variation_space") or {}),
        validation_targets=tuple(raw.get("validation_targets") or ()),
        ood_status=str(raw.get("ood_status", "train")),
        provenance_sources=tuple(raw.get("provenance_sources") or ()),
        provenance_transformations=tuple(raw.get("provenance_transformations") or ()),
    )
    result = run_end_to_end(scenario, baseline=healthy_baseline())
    return {"contract_version": "1.0", **result.to_dict()}


def _cardistudio(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardistudio import approximate_two_sample_n, full_factorial
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("CardiStudio", exc)

    if capability == "design.generate":
        design = full_factorial(
            dict(payload.get("factors") or {"condition": ["sham", "MI"]}),
            replicates=int(payload.get("replicates", 1)),
            blocks=int(payload.get("blocks", 1)),
            seed=int(payload.get("seed", 42)),
        )
        return {"contract_version": "1.0", "design": design.rows, "n_runs": design.n_runs}
    if capability == "power.plan":
        n = approximate_two_sample_n(
            effect_size=float(payload.get("effect_size", 0.5)),
            alpha=float(payload.get("alpha", 0.05)),
            power=float(payload.get("power", 0.8)),
        )
        return {"contract_version": "1.0", "n_per_arm": int(n)}
    if capability in {"population.generate", "design.validate"}:
        # Keep native support explicit and deterministic without inventing a second API.
        return {
            "contract_version": "1.0",
            "supported": False,
            "message": f"{capability} requires a typed PopulationSpec/ChallengeSpec payload; use design.generate or power.plan for the generic native path.",
        }
    raise ValueError(f"CardiStudio does not support {capability}")


def _dccp(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from dccp import (
            HeuristicDetector,
            PrototypeDetector,
            assess_scenario,
            audit_scenario,
            build_rescue_event_dict,
            evaluate_recovery,
            load_scenario,
            validate_scenario,
        )
        from dccp.scenario import Scenario
    except Exception as exc:  # pragma: no cover - environment dependent
        raise _native_unavailable("DCCP", exc)

    raw = payload.get("scenario")
    scenario = Scenario.from_dict(raw) if isinstance(raw, dict) else load_scenario(str(payload["scenario_path"]))
    if capability == "challenge.validate":
        return {"contract_version": "1.0", "errors": validate_scenario(scenario.raw or raw or {})}
    if capability == "challenge.assess":
        assessment = assess_scenario(scenario)
        return {"contract_version": "1.0", **assessment.as_dict()}
    if capability == "challenge.materialize":
        from dccp.library import materialize_challenge_set
        scenarios = payload.get("scenarios") or [scenario.raw or raw]
        result = materialize_challenge_set(scenarios)
        return {"contract_version": "1.0", "materialized": result}
    if capability == "recovery.score":
        baseline = payload.get("baseline")
        challenged = payload.get("challenged")
        recovered = payload.get("recovered")
        if not all(isinstance(item, dict) for item in (baseline, challenged, recovered)):
            raise ValueError("recovery.score requires baseline, challenged, and recovered phenotype maps")
        report = evaluate_recovery(baseline, challenged, recovered)
        return {"contract_version": "1.0", **report.to_dict()}
    if capability == "host.map":
        scores = dict(payload.get("module_scores") or {})
        from dccp.omics_map import map_module_scores_to_axes
        mapped = map_module_scores_to_axes(scores)
        return {"contract_version": "1.0", "axes": mapped}
    raise ValueError(f"DCCP does not support {capability}")
