from __future__ import annotations

from dataclasses import dataclass

from hearttwin.scientific_api import ScientificVirelionServices


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


class _Facade(ScientificVirelionServices):
    def __init__(self):
        self.registry = object()


def test_scientific_run_patient_ep_uses_declared_wrapper(monkeypatch) -> None:
    captured = {}

    def _run(registry, *, case, spec, **kwargs):
        captured.update(
            registry=registry,
            case=case,
            spec=spec,
            kwargs=kwargs,
        )
        return "result"

    monkeypatch.setattr("hearttwin.scientific_api.run_strict_patient_ep_workflow", _run)
    facade = _Facade()
    assert facade.run_patient_ep(
        "case",  # type: ignore[arg-type]
        "spec",  # type: ignore[arg-type]
        confirmation_settings={"opencarp_experiment_spec": {"contract_version": "1.0"}},
    ) == "result"
    assert captured["registry"] is facade.registry
    assert captured["case"] == "case"
    assert captured["spec"] == "spec"
    assert "confirmation_settings" in captured["kwargs"]


def test_scientific_holdout_path_reads_validation_only_after_confirmation(monkeypatch) -> None:
    events: list[str] = []
    result = _Result({"patient_id": "P1", "confirmation": {"status": "complete"}})

    def _run(registry, *, case, spec, **kwargs):
        events.append("confirmation_complete")
        return result

    def _evaluate(received, *, spec):
        assert received is result
        assert events == ["confirmation_complete"]
        assert spec == "eval"
        events.append("holdout_read")
        return [_Report({"evidence_id": "holdout", "mean_nrmse": 0.2})]

    monkeypatch.setattr("hearttwin.scientific_api.run_strict_patient_ep_workflow", _run)
    monkeypatch.setattr("hearttwin.scientific_api.evaluate_patient_ep_holdouts", _evaluate)

    facade = _Facade()
    output = facade.run_patient_ep_with_holdout_evaluation(
        "case",  # type: ignore[arg-type]
        "spec",  # type: ignore[arg-type]
        evaluation_spec="eval",
    )
    assert events == ["confirmation_complete", "holdout_read"]
    assert output == {
        "workflow": {"patient_id": "P1", "confirmation": {"status": "complete"}},
        "validation": [{"evidence_id": "holdout", "mean_nrmse": 0.2}],
    }
