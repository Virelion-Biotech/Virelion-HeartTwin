from __future__ import annotations

import shlex
import sys

import pytest

from hearttwin.service_registry import ServiceAdapter, ServiceSpec


def test_large_command_payload_roundtrips_over_stdin(tmp_path):
    script = tmp_path / "echo_payload.py"
    script.write_text("import json, os, sys\nraw = sys.stdin.read() if os.environ.get('HEARTTWIN_PAYLOAD_STDIN') == '1' else os.environ['HEARTTWIN_PAYLOAD']\nprint(raw)\n")
    adapter = ServiceAdapter(ServiceSpec(name="fixture", repository="fixture", capabilities=("trace.record",), command=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"))
    payload = {"data": "x" * 200_000}
    assert adapter.invoke("trace.record", payload) == payload


def test_evaluation_rejects_missing_independent_labels():
    pytest.importorskip("cardieval")
    from hearttwin.native_services import invoke_native
    with pytest.raises(ValueError, match="independently"):
        invoke_native("CardiEval", "evaluation.run", {"benchmark": {"assignments": {"s1": "test"}}, "predictions": [{"sample_id": "s1", "y_pred": 1, "y_true": 1}]})


def test_learning_rejects_outcome_metadata_as_features():
    pytest.importorskip("cardilearn")
    from hearttwin.native_services import invoke_native
    with pytest.raises(ValueError, match="outcome/identifier"):
        invoke_native("CardiLearn", "learn.infer", {"data": [{"sample_id": "s1", "target": 1, "label": "MI", "group_id": "g1"}], "feature_columns": ["label"]})


def test_simulated_domains_use_actual_variables_and_extrapolated_evidence():
    pytest.importorskip("cardisim")
    from hearttwin.native_services import invoke_native
    from hearttwin.contracts import SimulationResultPayload
    from hearttwin.workflow import _scenario_from_workflow
    result = SimulationResultPayload.model_validate(invoke_native("CardiSim", "simulation.run", {"preset": "mi", "n_cells": 4, "duration": 1.0}))
    scenario = _scenario_from_workflow("fixture", result)
    assert scenario["evidence_tier"] == "extrapolated"
    assert scenario["phenotype_domains"]["contractile_impairment"]["value"] == pytest.approx(1 - result.summary["final"]["contractility"])
    assert scenario["temporal_profile"][0]["domains"]["inflammatory_activation"]["value"] == pytest.approx(result.summary["initial"]["inflammation"])
    assert "uncertainty" not in scenario["phenotype_domains"]["contractile_impairment"]



def test_undefined_learning_metrics_become_warnings_not_nan() -> None:
    from hearttwin.native_services import _finite_learning_metrics

    metrics, warnings = _finite_learning_metrics(
        {
            "validation": {
                "accuracy": 0.75,
                "roc_auc": float("nan"),
                "loss": float("inf"),
            }
        }
    )

    assert metrics == {"validation": {"accuracy": 0.75}}
    assert len(warnings) == 2
    assert any("validation.roc_auc" in item for item in warnings)
    assert any("validation.loss" in item for item in warnings)



def test_pinned_cardilearn_uses_json_null_for_undefined_metrics() -> None:
    pytest.importorskip("cardilearn")
    import json
    import numpy as np
    from cardilearn.metrics import classification_metrics, regression_metrics

    classification = classification_metrics(
        np.asarray([1, 1, 1]),
        np.asarray([1, 1, 1]),
        np.asarray([0.7, 0.8, 0.9]),
    )
    regression = regression_metrics(
        np.asarray([2.0]),
        np.asarray([2.0]),
    )

    assert classification["auroc"] is None
    assert regression["r2"] is None
    json.dumps(
        {"classification": classification, "regression": regression},
        allow_nan=False,
    )
