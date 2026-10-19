from __future__ import annotations

from dataclasses import dataclass

from hearttwin.api import VirelionServices


@dataclass
class _Result:
    payload: dict

    def model_dump(self, mode: str = "python") -> dict:
        assert mode == "json"
        return dict(self.payload)


@dataclass
class _Report:
    payload: dict

    def model_dump(self, mode: str = "python") -> dict:
        assert mode == "json"
        return dict(self.payload)


class _ServicesStub:
    registry = object()


def test_run_patient_ep_with_holdout_evaluation_is_strictly_downstream(monkeypatch) -> None:
    events: list[str] = []
    result = _Result({"patient_id": "P1", "confirmation": {"status": "complete"}})

    def _run(registry, *, case, spec, **kwargs):
        assert registry is _ServicesStub.registry
        assert case == "case"
        assert spec == "workflow-spec"
        assert kwargs == {"parameter_bounds": {"cv": (0.1, 1.0)}}
        events.append("confirmation_complete")
        return result

    def _evaluate(received, *, spec):
        assert events == ["confirmation_complete"]
        assert received is result
        assert spec == "evaluation-spec"
        events.append("holdout_read")
        return [_Report({"evidence_id": "held-out-1", "mean_nrmse": 0.25})]

    monkeypatch.setattr("hearttwin.api.run_strict_patient_ep_workflow", _run)
    monkeypatch.setattr("hearttwin.api.evaluate_patient_ep_holdouts", _evaluate)

    output = VirelionServices.run_patient_ep_with_holdout_evaluation(
        _ServicesStub(),
        "case",  # type: ignore[arg-type]
        "workflow-spec",  # type: ignore[arg-type]
        evaluation_spec="evaluation-spec",
        parameter_bounds={"cv": (0.1, 1.0)},
    )

    assert events == ["confirmation_complete", "holdout_read"]
    assert output == {
        "workflow": {"patient_id": "P1", "confirmation": {"status": "complete"}},
        "validation": [{"evidence_id": "held-out-1", "mean_nrmse": 0.25}],
    }
