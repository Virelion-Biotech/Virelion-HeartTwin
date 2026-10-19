from __future__ import annotations

import math

import pytest

from hearttwin.patient_ecg_eval import (
    ECGAcceptanceCriteria,
    ECGEvaluationSpec,
    ECGWaveform,
    evaluate_heldout_ecg,
)


LEADS = ["I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]


def _signals(scale: float = 1.0, *, n: int = 8) -> list[list[float]]:
    base = [0.0, 0.2, 1.0, 0.5, -0.4, -0.1, 0.0, 0.0][:n]
    return [[scale * value for value in base] for _ in LEADS]


def _waveform(waveform_id: str, *, scale: float = 1.0) -> ECGWaveform:
    return ECGWaveform(
        waveform_id=waveform_id,
        lead_names=LEADS,
        sample_rate_hz=1000.0,
        signals=_signals(scale),
        unit="mV",
        qrs_onset_ms=1.0,
        qrs_offset_ms=6.0,
    )


def test_identical_waveforms_have_zero_error_and_unit_correlation() -> None:
    report = evaluate_heldout_ecg(
        measured=_waveform("measured"),
        predicted=_waveform("predicted"),
        evidence_id="holdout-1",
        withheld_from_fit=True,
        spec=ECGEvaluationSpec(),
    )
    assert report.mean_nrmse == pytest.approx(0.0)
    assert report.mean_correlation == pytest.approx(1.0)
    assert report.qrs_duration_error_ms == pytest.approx(0.0)
    assert all(metric.nrmse == pytest.approx(0.0) for metric in report.lead_metrics)


def test_validation_refuses_non_holdout_measurement() -> None:
    with pytest.raises(ValueError, match="withheld_from_fit"):
        evaluate_heldout_ecg(
            measured=_waveform("measured"),
            predicted=_waveform("predicted"),
            evidence_id="fit-data",
            withheld_from_fit=False,
            spec=ECGEvaluationSpec(),
        )


def test_acceptance_criteria_are_predeclared_and_auditable() -> None:
    report = evaluate_heldout_ecg(
        measured=_waveform("measured", scale=1.0),
        predicted=_waveform("predicted", scale=1.2),
        evidence_id="holdout-1",
        withheld_from_fit=True,
        spec=ECGEvaluationSpec(
            acceptance=ECGAcceptanceCriteria(
                max_mean_nrmse=0.10,
                min_mean_correlation=0.99,
            )
        ),
    )
    assert report.acceptance_passed is False
    assert any("mean NRMSE" in failure for failure in report.acceptance_failures)
    assert report.mean_correlation == pytest.approx(1.0)


def test_fixed_offset_must_land_on_integer_sample() -> None:
    with pytest.raises(ValueError, match="integer sample"):
        evaluate_heldout_ecg(
            measured=_waveform("measured"),
            predicted=_waveform("predicted"),
            evidence_id="holdout-1",
            withheld_from_fit=True,
            spec=ECGEvaluationSpec(fixed_prediction_offset_ms=0.25),
        )


def test_missing_standard_lead_fails_closed() -> None:
    predicted = _waveform("predicted").model_copy(
        update={"lead_names": LEADS[:-1], "signals": _signals()[:-1]}
    )
    with pytest.raises(ValueError, match="missing expected leads"):
        evaluate_heldout_ecg(
            measured=_waveform("measured"),
            predicted=predicted,
            evidence_id="holdout-1",
            withheld_from_fit=True,
            spec=ECGEvaluationSpec(),
        )


def test_voltage_unit_conversion_is_applied_before_error() -> None:
    measured = _waveform("measured")
    predicted = ECGWaveform(
        waveform_id="predicted",
        lead_names=LEADS,
        sample_rate_hz=1000.0,
        signals=[[value / 1000.0 for value in trace] for trace in _signals()],
        unit="V",
        qrs_onset_ms=1.0,
        qrs_offset_ms=6.0,
    )
    report = evaluate_heldout_ecg(
        measured=measured,
        predicted=predicted,
        evidence_id="holdout-1",
        withheld_from_fit=True,
        spec=ECGEvaluationSpec(),
    )
    assert math.isclose(report.mean_nrmse, 0.0, abs_tol=1e-12)
