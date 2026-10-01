"""Native adapters for services that can run in-process when installed.

The adapters intentionally depend on public Python APIs from the component
repositories. They return JSON-compatible dictionaries so the existing
HeartTwin ServiceAdapter boundary remains backwards-compatible.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _native_unavailable(name: str, exc: Exception) -> RuntimeError:
    error = RuntimeError(
        f"Native service {name!r} is unavailable: install the corresponding Virelion package."
    )
    error.__cause__ = exc
    return error


def invoke_native(service: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    dispatch = {
        "CardiAnatomy": _cardianatomy,
        "CardiAtlas": _cardiatlas,
        "CardiBench": _cardibench,
        "CardiEval": _cardieval,
        "CardiLearn": _cardilearn,
        "CardiEP": _cardiep,
        "CardiInfer": _cardiinfer,
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



def _cardiep(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardiep.api import EPAPI
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiEP", exc)
    api = EPAPI()
    if capability == "ep.health":
        return api.health()
    if capability == "ep.simulate":
        return api.simulate(payload)
    if capability == "ep.calibrate":
        return api.calibrate(payload)
    raise ValueError(f"CardiEP does not support {capability}")


def _cardiinfer(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardiinfer.api import InferAPI
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiInfer", exc)
    api = InferAPI()
    if capability == "infer.health":
        return api.health()
    if capability == "infer.run":
        return api.infer(payload)
    if capability == "infer.propagate":
        return api.propagate(payload)
    raise ValueError(f"CardiInfer does not support {capability}")


def _cardianatomy(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardianatomy import AnatomyAPI
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiAnatomy", exc)

    api = AnatomyAPI()
    if capability == "anatomy.health":
        return api.health()
    if capability == "anatomy.build":
        return api.build(payload)
    if capability == "anatomy.validate":
        return api.validate(payload)
    if capability == "anatomy.tools":
        return api.tools()
    if capability == "anatomy.microstructure.reference":
        return api.reference_microstructure(payload)
    if capability == "anatomy.scar.classify":
        return api.scar_classify(payload)
    if capability == "anatomy.presets":
        return api.presets()
    if capability == "anatomy.registration.rigid":
        return api.registration_rigid(payload)
    if capability == "anatomy.geometry.measure":
        return api.geometry_measure(payload)
    if capability == "anatomy.manifest.audit":
        return api.manifest_audit(payload)
    if capability == "anatomy.series.rank":
        return api.series_rank(payload)
    if capability == "anatomy.segmentation.qc":
        return api.segmentation_qc(payload)
    if capability == "anatomy.cine.phases":
        return api.cine_phases(payload)
    if capability == "anatomy.transforms.compose":
        return api.transforms_compose(payload)
    if capability == "anatomy.transforms.invert":
        return api.transforms_invert(payload)
    raise ValueError(f"CardiAnatomy does not support {capability}")


_ATLAS_CONTEXT_ID_FIELDS = (
    "phenotype_ids", "cell_state_ids", "marker_ids", "dataset_ids",
    "study_ids", "sample_ids", "intervention_ids", "evidence_ids",
)


def _cardiatlas_api(payload: dict[str, Any]):
    try:
        from cardiatlas import AtlasAPI, AtlasService, SQLiteAtlasStore, record_from_dict
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiAtlas", exc)

    db_path = os.getenv("CARDIATLAS_DB")
    if db_path:
        path = Path(db_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"CARDIATLAS_DB does not exist or is not a file: {path}")
        with SQLiteAtlasStore(path) as store:
            service = store.load_service()
    else:
        service = AtlasService.empty()

    raw_records = payload.get("records", [])
    if raw_records is None:
        raw_records = []
    if not isinstance(raw_records, list):
        raise ValueError("records must be an array of record objects")
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise ValueError(f"records[{index}] must be an object")
        service.add(record_from_dict(raw))
    return AtlasAPI(service)


def _normalize_atlas_context(raw: dict[str, Any], record_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    resolved: set[str] = set()
    for field in _ATLAS_CONTEXT_ID_FIELDS:
        values = raw.get(field, [])
        if isinstance(values, list):
            resolved.update(item for item in values if isinstance(item, str))
    provenance = sorted(resolved)
    missing = sorted(set(record_ids) - resolved)

    normalized = dict(raw)
    normalized["provenance"] = provenance
    metadata = dict(normalized.get("metadata") or {})
    if missing:
        metadata["missing_record_ids"] = missing
    else:
        metadata.pop("missing_record_ids", None)
    normalized["metadata"] = metadata
    return normalized, provenance


def _cardiatlas(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    api = _cardiatlas_api(payload)

    if capability == "atlas.search":
        has_query = "query" in payload
        has_text = "text" in payload
        if has_query and has_text and payload["query"] != payload["text"]:
            raise ValueError("query and text disagree")
        if not has_query and not has_text:
            return {"contract_version": "1.0", "query": "", "records": [], "count": 0}

        query = payload.get("query", payload.get("text", ""))
        if not isinstance(query, str):
            raise ValueError("query/text must be a string")
        record_type = payload.get("record_type")
        if record_type is not None and not isinstance(record_type, str):
            raise ValueError("record_type must be a string or null")
        tags = payload.get("tags", [])
        if not isinstance(tags, (list, tuple)) or isinstance(tags, (str, bytes)):
            raise ValueError("tags must be an array of strings")
        if not all(isinstance(tag, str) for tag in tags):
            raise ValueError("tags must contain only strings")
        limit = payload.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if not 0 <= limit <= 1000:
            raise ValueError("limit must be between 0 and 1000")

        result = api.search(text=query, record_type=record_type, tags=tuple(tags), limit=limit)
        records = [item["record"] for item in result["results"]]
        return {
            "contract_version": "1.0",
            "query": query,
            "records": records,
            "count": len(records),
        }

    if capability == "atlas.context":
        record_ids = payload.get("record_ids", [])
        if not isinstance(record_ids, list) or not all(isinstance(item, str) for item in record_ids):
            raise ValueError("record_ids must be an array of strings")
        context_id = payload.get("context_id")
        if context_id is None:
            context_id = f"ctx-{payload.get('entity_id', 'unknown')}"
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("context_id must be a non-empty string")

        raw = api.context(record_ids=record_ids, context_id=context_id).to_dict()
        normalized, provenance = _normalize_atlas_context(raw, record_ids)
        return {
            "contract_version": "1.0",
            "context_id": context_id,
            "record_ids": list(record_ids),
            "context": normalized,
            "provenance": provenance,
        }

    raise ValueError(f"CardiAtlas does not support {capability}")


def _cardibench(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardi_bench import Sample, materialize
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiBench", exc)
    if capability != "benchmark.resolve":
        raise ValueError(f"CardiBench does not support {capability}")
    raw_samples = payload.get("samples") or []
    samples = [
        Sample(
            sample_id=str(item["sample_id"]), group_id=str(item["group_id"]), study_id=str(item["study_id"]),
            label=str(item["label"]), technical_group=item.get("technical_group"), organism=item.get("organism"),
            timepoint=item.get("timepoint"), cell_context=item.get("cell_context"), region=item.get("region"),
        )
        for item in raw_samples
    ]
    result = materialize(
        samples,
        benchmark_id=str(payload.get("benchmark_id", "hearttwin-e2e")),
        version=str(payload.get("version", "1.0")),
        policy=str(payload.get("policy", "subject_heldout")),
        test_values={str(item) for item in payload.get("test_values", [])},
        validation_values={str(item) for item in payload.get("validation_values", [])},
        seed=int(payload.get("seed", 0)),
    )
    data = result.to_dict()
    data.pop("label_counts", None)
    return {"contract_version": "1.0", **data, "samples": raw_samples}


def _cardilearn(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        import pandas as pd
        from cardilearn.config import SplitConfig, TrainingConfig
        from cardilearn.data import Dataset
        from cardilearn.training import train
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiLearn", exc)
    if capability not in {"learn.infer", "learn.predict"}:
        raise ValueError(f"CardiLearn does not support {capability}")
    rows = payload.get("data") or []
    if not rows:
        raise ValueError("CardiLearn native adapter requires a non-empty 'data' table")
    target = str(payload.get("target_column", "target"))
    group = payload.get("group_column", "group_id")
    frame = pd.DataFrame(rows)
    # Outcome aliases and identifiers must never become model features by default.
    features = payload.get("feature_columns")
    reserved = {target, group, "sample_id", "id", "study_id", "label", "subgroup", "condition", "region", "technical_group"}
    if not isinstance(features, list) or not features or len(features) != len(set(features)):
        raise ValueError("CardiLearn adapter requires explicit, unique feature_columns")
    if set(features) & reserved or not set(features) <= set(frame.columns):
        raise ValueError("feature_columns contain missing columns or outcome/identifier metadata")
    dataset = Dataset(frame=frame[features + [target] + ([group] if group else [])], target_column=target, group_column=group if group else None)
    split = SplitConfig(
        test_size=float(payload.get("test_size", 0.2)), validation_size=float(payload.get("validation_size", 0.2)),
        random_state=int(payload.get("seed", 42)), stratify=bool(payload.get("stratify", True)),
    )
    config = TrainingConfig(
        task=str(payload.get("task", "classification")), model=str(payload.get("model", "logistic_regression")),
        target_column=target, group_column=group if group else None, random_state=int(payload.get("seed", 42)), split=split,
    )
    assignments = payload.get("split_assignments")
    if assignments is None:
        result = train(dataset, config)
    else:
        from types import SimpleNamespace
        from cardilearn.models import build_model
        from cardilearn.metrics import evaluate
        from cardilearn.reproducibility import dataframe_fingerprint
        if "sample_id" not in frame or frame.sample_id.isna().any() or frame.sample_id.astype(str).duplicated().any():
            raise ValueError("Locked training requires unique sample IDs")
        ids = frame.sample_id.astype(str).tolist()
        if set(assignments) != set(ids) or set(assignments.values()) - {"train", "validation", "test"}:
            raise ValueError("Split assignments must cover exactly the learning samples")
        indices = {name: [i for i, sid in enumerate(ids) if assignments[sid] == name] for name in ("train", "validation", "test")}
        if not indices["train"] or not indices["test"]:
            raise ValueError("Locked benchmark needs nonempty training and test partitions")
        if group:
            if frame[group].isna().any():
                raise ValueError("Biological group IDs must not be missing")
            group_splits = {}
            for sid, gid in zip(ids, frame[group].astype(str)):
                group_splits.setdefault(gid, set()).add(assignments[sid])
            if any(len(values) != 1 for values in group_splits.values()):
                raise ValueError("Biological group leakage across benchmark splits")
        X, y = dataset.features(), dataset.target
        if config.task == "classification" and y.iloc[indices["train"]].nunique() < 2:
            raise ValueError("Training partition requires at least two classes")
        model = build_model(config.task, config.model, X.iloc[indices["train"]])
        model.fit(X.iloc[indices["train"]], y.iloc[indices["train"]])
        metrics = {name: evaluate(model, X.iloc[index], y.iloc[index], config.task)
                   for name, index in indices.items() if name != "test" and index}
        result = SimpleNamespace(model=model, splits=SimpleNamespace(**indices), metrics=metrics,
                                 dataset_fingerprint=dataframe_fingerprint(dataset.frame))
    if payload.get("prediction_data") is not None:
        prediction_frame = pd.DataFrame(payload["prediction_data"])
        X_pred = prediction_frame[dataset.feature_columns]
        y_pred_target = prediction_frame[target] if target in prediction_frame.columns else None
    else:
        prediction_frame = frame.iloc[result.splits.test]
        X_pred = dataset.features().iloc[result.splits.test]
        y_pred_target = dataset.target.iloc[result.splits.test]
    source_rows = prediction_frame.to_dict(orient="records")

    predictions = result.model.predict(X_pred)
    scores = None
    if config.task == "classification" and hasattr(result.model, "predict_proba"):
        probabilities = result.model.predict_proba(X_pred)
        if getattr(probabilities, "ndim", 1) == 2 and probabilities.shape[1] == 2:
            classes = list(result.model.classes_)
            if set(classes) != {0, 1}:
                raise ValueError("Binary probability scoring requires explicit 0/1 target encoding")
            scores = probabilities[:, classes.index(1)]
    y_values = [None] * len(predictions) if y_pred_target is None else y_pred_target.tolist()
    prediction_rows = []
    for index, (row, y_true, y_pred) in enumerate(zip(source_rows, y_values, predictions.tolist(), strict=True)):
        prediction_rows.append({
            "sample_id": str(row.get("sample_id", row.get("id", f"test-{index:04d}"))),
            "y_true": None if y_true is None or pd.isna(y_true) else y_true,
            "y_pred": y_pred,
            "score": None if scores is None else float(scores[index]),
            "subgroup": None if row.get("subgroup") is None else str(row["subgroup"]),
        })
    return {
        "contract_version": "1.0", "model_id": str(payload.get("model_id", config.model)), "task": config.task,
        "target_column": target, "feature_columns": features, "metrics": result.metrics, "predictions": prediction_rows,
        "dataset_fingerprint": result.dataset_fingerprint,
    }


def _cardieval(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardieval import BenchmarkManifest, BenchmarkTask, PredictionRecord, evaluate_submission
        from cardieval.provenance import canonical_json_hash
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiEval", exc)
    if capability != "evaluation.run":
        raise ValueError(f"CardiEval does not support {capability}")
    benchmark = payload.get("benchmark")
    predictions = payload.get("predictions") or []
    if not isinstance(benchmark, dict) or not predictions:
        raise ValueError("CardiEval native adapter requires benchmark and non-empty predictions")
    test_ids = [sample_id for sample_id, split in benchmark.get("assignments", {}).items() if split == "test"]
    pred_by_id = {str(item["sample_id"]): item for item in predictions}
    if len(pred_by_id) != len(predictions) or set(pred_by_id) != set(test_ids):
        raise ValueError("Predictions must cover exactly the benchmark test IDs without duplicates")
    labels = payload.get("reference_labels")
    if not isinstance(labels, dict) or set(labels) != set(test_ids) or any(v is None for v in labels.values()):
        raise ValueError("Evaluation requires independently supplied reference_labels for every test ID")
    records = [PredictionRecord.model_validate(pred_by_id[sample_id]) for sample_id in test_ids]
    benchmark_id = str(benchmark["benchmark_id"])
    version = str(benchmark["version"])
    manifest = BenchmarkManifest(
        benchmark_id=benchmark_id, version=version, task="binary_classification", split="test",
        sample_ids=test_ids, dataset_sha256=str(benchmark["metadata_sha256"]),
        label_schema={"0": "reference", "1": "target"}, metadata={"source": "HeartTwin/CardiBench"},
        authoritative_labels=labels,
    )
    task = BenchmarkTask(
        benchmark_id=benchmark_id, version=version, task_id=str(payload.get("task_id", "binary-cardiac-state-detection")),
        task_type="binary_classification", allowed_metrics=["accuracy", "balanced_accuracy", "macro_f1", "auroc", "auprc", "brier", "ece"],
        primary_metric="macro_f1", primary_direction="higher_is_better", splits=["test"],
        requires_authoritative_labels=True,
        description="HeartTwin multimodal integration evaluation task",
    )
    report = evaluate_submission(manifest, records, model_id=str(payload.get("model_id", "unknown")), task_contract=task)
    report_json = report.model_dump(mode="json")
    return {
        "contract_version": "1.0", "benchmark_id": benchmark_id, "benchmark_version": version,
        "task_id": task.task_id, "model_id": str(payload.get("model_id", "unknown")),
        "primary_metric": report.primary_metric, "primary_value": report.primary_value,
        "metrics": [metric.model_dump(mode="json") for metric in report.metrics],
        "warnings": list(report.warnings), "errors": list(report.errors),
        "evaluation_fingerprint": canonical_json_hash(report_json),
    }


def _cardisim(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardisim import CardiacSimulator, SimulationConfig, population_preset
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiSim", exc)
    if capability != "simulation.run":
        raise ValueError(f"CardiSim does not support {capability}")
    config = SimulationConfig(
        duration=float(payload.get("duration", 7.0)), dt=float(payload.get("dt", 0.25)),
        n_cells=int(payload.get("n_cells", 64)), seed=int(payload.get("seed", 0)),
        heterogeneity=float(payload.get("heterogeneity", 0.05)), process_noise=float(payload.get("process_noise", 0.0)),
    )
    result = CardiacSimulator(config).run(population_preset(str(payload.get("preset", "baseline"))))
    return {"contract_version": "1.0", "backend": "Virelion-CardiSim", "summary": result.summary(), "events": list(result.events), "population_size": int(config.n_cells)}


def _cardivex(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    if capability != "vex.observe":
        raise ValueError(f"CardiVex does not support {capability}")
    try:
        from cardivex import Confidence, EvidenceTier, Scenario, ScenarioState, run_end_to_end
        from cardivex.models import DomainValue
        from cardivex.features import from_domain_scores
    except Exception as exc:  # pragma: no cover
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
    temporal = [
        ScenarioState(
            state=str(item.get("state", "state")), relative_time=float(item.get("relative_time", 0.0)),
            duration=float(item.get("duration", 0.0)), domains=domain_map(dict(item.get("domains") or {})),
        )
        for item in raw.get("temporal_profile", [])
    ]
    scenario = Scenario(
        scenario_id=str(raw["scenario_id"]), version=str(raw.get("version", "1.0")),
        name=str(raw.get("name", raw["scenario_id"])), target_model=str(raw.get("target_model", "HeartTwin")),
        evidence_tier=EvidenceTier(str(raw.get("evidence_tier", "observed"))), confidence=Confidence(str(raw.get("confidence", "exploratory"))),
        phenotype_domains=domain_map(dict(raw.get("phenotype_domains") or {})), temporal_profile=tuple(temporal),
        description=str(raw.get("description", "")), severity_profile={str(k): float(v) for k, v in dict(raw.get("severity_profile") or {}).items()},
        interaction_profile=tuple(raw.get("interaction_profile") or ()), variation_space=dict(raw.get("variation_space") or {}),
        validation_targets=tuple(raw.get("validation_targets") or ()), ood_status=str(raw.get("ood_status", "train")),
        provenance_sources=tuple(raw.get("provenance_sources") or ()), provenance_transformations=tuple(raw.get("provenance_transformations") or ()),
    )
    result = run_end_to_end(scenario, baseline=from_domain_scores({key: item.value for key, item in scenario.temporal_profile[0].domains.items()}))
    return {"contract_version": "1.0", "scenario_id": scenario.scenario_id, "evidence_tier": scenario.evidence_tier.value, "confidence": scenario.confidence.value, "primary": result.assessment.to_dict(), **result.to_dict()}


def _cardistudio(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from cardistudio import ChallengeSpec, PopulationBuilder, approximate_two_sample_n, full_factorial, validate_challenge, validate_population
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("CardiStudio", exc)
    if capability == "design.generate":
        design = full_factorial(
            dict(payload.get("factors") or {"condition": ["sham", "MI"]}),
            replicates=int(payload.get("replicates", 1)), blocks=int(payload.get("blocks", 1)), seed=int(payload.get("seed", 42)),
        )
        return {"contract_version": "1.0", "design": design.rows, "n_runs": design.n_runs}
    if capability == "power.plan":
        n = approximate_two_sample_n(effect_size=float(payload.get("effect_size", 0.5)), alpha=float(payload.get("alpha", 0.05)), power=float(payload.get("power", 0.8)))
        return {"contract_version": "1.0", "n_per_arm": int(n)}
    if capability in {"population.generate", "design.validate"}:
        raw_spec = payload.get("spec")
        if not isinstance(raw_spec, dict):
            raise ValueError(f"{capability} requires a CardiStudio ChallengeSpec under 'spec'")
        spec = ChallengeSpec.from_dict(raw_spec)
        if capability == "design.validate":
            report = validate_challenge(spec)
            return {"contract_version": "1.0", "report": {"valid": report.valid, "errors": report.errors, "warnings": report.warnings, "metrics": report.metrics}}
        population = PopulationBuilder(spec).build()
        report = validate_population(population.rows, spec)
        return {"contract_version": "1.0", "rows": population.rows, "provenance": population.provenance, "validation": {"valid": report.valid, "errors": report.errors, "warnings": report.warnings, "metrics": report.metrics}}
    raise ValueError(f"CardiStudio does not support {capability}")


def _dccp(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from dccp import assess_scenario, evaluate_recovery, load_scenario, validate_scenario
        from dccp.scenario import Scenario
    except Exception as exc:  # pragma: no cover
        raise _native_unavailable("DCCP", exc)
    if capability == "challenge.materialize":
        from dccp.library import materialize_challenge_set
        root = payload.get("scenario_root", "scenarios")
        return {"contract_version": "1.0", "challenge_set": materialize_challenge_set(str(root), include_ood=bool(payload.get("include_ood", True)))}
    if capability == "host.map":
        from dccp.omics_map import map_module_scores_to_axes
        return {"contract_version": "1.0", "axes": map_module_scores_to_axes(dict(payload.get("module_scores") or {}))}
    raw = payload.get("scenario")
    scenario = Scenario.from_dict(raw) if isinstance(raw, dict) else load_scenario(str(payload["scenario_path"]))
    if capability == "challenge.validate":
        return {"contract_version": "1.0", "errors": validate_scenario(scenario.raw)}
    if capability == "challenge.assess":
        assessment = assess_scenario(scenario)
        return {"contract_version": "1.0", **assessment.as_dict()}
    if capability == "recovery.score":
        baseline = payload.get("baseline"); challenged = payload.get("challenged"); rescued = payload.get("rescued")
        if not all(isinstance(item, dict) for item in (baseline, challenged, rescued)):
            raise ValueError("recovery.score requires baseline, challenged, and rescued phenotype maps")
        from dccp.recovery import evaluate_recovery
        report = evaluate_recovery(
            scenario_id=str(payload.get("scenario_id", scenario.scenario_id)),
            intervention_name=str(payload.get("intervention_name", "HeartTwin intervention")),
            baseline={str(k): float(v) for k, v in baseline.items()},
            challenged={str(k): float(v) for k, v in challenged.items()},
            rescued={str(k): float(v) for k, v in rescued.items()},
        )
        return {"contract_version": "1.0", **report.as_dict()}
    raise ValueError(f"DCCP does not support {capability}")
