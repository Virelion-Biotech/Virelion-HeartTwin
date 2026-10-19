"""Held-out ECG evaluation for patient-specific HeartTwin EP workflows.

This module is deliberately downstream of simulation.  It accepts measured and
predicted waveforms, computes predeclared metrics, and has no calibration or
solver interface.  The primary path does not optimize temporal alignment against
held-out data; a fixed offset may be declared in advance when acquisition and
simulation clocks have a known offset.
"""
from __future__ import annotations

from math import sqrt
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_STANDARD_12_LEADS = ("I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6")


class ECGWaveform(BaseModel):
    """In-memory ECG waveform used only for evaluation, not fitting."""

    model_config = ConfigDict(extra="forbid")
    waveform_id: str
    lead_names: list[str]
    sample_rate_hz: float = Field(gt=0)
    signals: list[list[float]]
    unit: Literal["V", "mV", "uV"] = "mV"
    qrs_onset_ms: float | None = None
    qrs_offset_ms: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_waveform(self) -> ECGWaveform:
        if not self.lead_names:
            raise ValueError("ECG waveform requires at least one lead")
        if len(self.lead_names) != len(set(self.lead_names)):
            raise ValueError("ECG lead_names must be unique")
        if len(self.signals) != len(self.lead_names):
            raise ValueError("signals must contain exactly one trace per lead")
        lengths = {len(trace) for trace in self.signals}
        if not lengths or 0 in lengths or len(lengths) != 1:
            raise ValueError("all ECG traces must be non-empty and have equal length")
        array = np.asarray(self.signals, dtype=float)
        if not np.isfinite(array).all():
            raise ValueError("ECG signals must contain only finite values")
        if (self.qrs_onset_ms is None) != (self.qrs_offset_ms is None):
            raise ValueError("QRS onset and offset must either both be provided or both be absent")
        if self.qrs_onset_ms is not None and self.qrs_offset_ms is not None:
            if self.qrs_offset_ms <= self.qrs_onset_ms:
                raise ValueError("qrs_offset_ms must be greater than qrs_onset_ms")
        return self

    @property
    def n_samples(self) -> int:
        return len(self.signals[0])


class ECGAcceptanceCriteria(BaseModel):
    """Predeclared validation thresholds.  No universal clinical defaults are assumed."""

    model_config = ConfigDict(extra="forbid")
    max_qrs_duration_error_ms: float | None = Field(default=None, ge=0)
    max_mean_nrmse: float | None = Field(default=None, ge=0)
    min_mean_correlation: float | None = Field(default=None, ge=-1, le=1)
    max_per_lead_nrmse: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_criterion(self) -> ECGAcceptanceCriteria:
        if all(
            value is None
            for value in (
                self.max_qrs_duration_error_ms,
                self.max_mean_nrmse,
                self.min_mean_correlation,
                self.max_per_lead_nrmse,
            )
        ):
            raise ValueError("at least one acceptance criterion must be declared")
        return self


class ECGEvaluationSpec(BaseModel):
    """Predeclared evaluation protocol for one held-out ECG comparison."""

    model_config = ConfigDict(extra="forbid")
    expected_leads: list[str] = Field(default_factory=lambda: list(_STANDARD_12_LEADS))
    evaluation_window_ms: tuple[float, float] | None = None
    fixed_prediction_offset_ms: float = 0.0
    normalization: Literal["measured_rms", "measured_peak_to_peak"] = "measured_rms"
    require_standard_12_lead: bool = True
    acceptance: ECGAcceptanceCriteria | None = None

    @field_validator("expected_leads")
    @classmethod
    def validate_expected_leads(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("expected_leads must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def validate_protocol(self) -> ECGEvaluationSpec:
        if self.require_standard_12_lead and set(self.expected_leads) != set(_STANDARD_12_LEADS):
            raise ValueError("standard 12-lead evaluation requires exactly the standard 12 leads")
        if self.evaluation_window_ms is not None:
            start, stop = self.evaluation_window_ms
            if stop <= start:
                raise ValueError("evaluation_window_ms stop must be greater than start")
        return self


class ECGLeadMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead: str
    rmse: float
    nrmse: float
    correlation: float | None
    measured_scale: float


class ECGValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measured_waveform_id: str
    predicted_waveform_id: str
    evidence_id: str
    withheld_from_fit: bool
    n_samples_compared: int
    sample_rate_hz: float
    fixed_prediction_offset_ms: float
    evaluation_window_ms: tuple[float, float]
    lead_metrics: list[ECGLeadMetric]
    mean_nrmse: float
    mean_correlation: float | None
    qrs_duration_measured_ms: float | None = None
    qrs_duration_predicted_ms: float | None = None
    qrs_duration_error_ms: float | None = None
    acceptance_passed: bool | None = None
    acceptance_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _to_mv(waveform: ECGWaveform) -> np.ndarray:
    factors = {"V": 1000.0, "mV": 1.0, "uV": 0.001}
    return np.asarray(waveform.signals, dtype=float) * factors[waveform.unit]


def _sample_shift(offset_ms: float, sample_rate_hz: float) -> int:
    raw = offset_ms * sample_rate_hz / 1000.0
    rounded = int(round(raw))
    if abs(raw - rounded) > 1e-6:
        raise ValueError(
            "fixed_prediction_offset_ms must resolve to an integer sample at the declared sample rate"
        )
    return rounded


def _window_indices(
    spec: ECGEvaluationSpec,
    measured: ECGWaveform,
    prediction_shift: int,
) -> tuple[int, int, int, int, tuple[float, float]]:
    n_measured = measured.n_samples
    if spec.evaluation_window_ms is None:
        start_m = max(0, -prediction_shift)
        stop_m = min(n_measured, n_measured - prediction_shift)
        if stop_m <= start_m:
            raise ValueError("fixed prediction offset leaves no overlapping ECG samples")
        window = (start_m * 1000.0 / measured.sample_rate_hz, stop_m * 1000.0 / measured.sample_rate_hz)
    else:
        start_ms, stop_ms = spec.evaluation_window_ms
        start_m = int(round(start_ms * measured.sample_rate_hz / 1000.0))
        stop_m = int(round(stop_ms * measured.sample_rate_hz / 1000.0))
        if start_m < 0 or stop_m > n_measured or stop_m <= start_m:
            raise ValueError("evaluation_window_ms falls outside the measured waveform")
        window = spec.evaluation_window_ms
    start_p = start_m + prediction_shift
    stop_p = stop_m + prediction_shift
    return start_m, stop_m, start_p, stop_p, window


def _correlation(measured: np.ndarray, predicted: np.ndarray) -> float | None:
    measured_centered = measured - measured.mean()
    predicted_centered = predicted - predicted.mean()
    denom = sqrt(float(np.dot(measured_centered, measured_centered))) * sqrt(
        float(np.dot(predicted_centered, predicted_centered))
    )
    if denom <= np.finfo(float).eps:
        return None
    value = float(np.dot(measured_centered, predicted_centered) / denom)
    return float(np.clip(value, -1.0, 1.0))


def evaluate_heldout_ecg(
    *,
    measured: ECGWaveform,
    predicted: ECGWaveform,
    evidence_id: str,
    withheld_from_fit: bool,
    spec: ECGEvaluationSpec,
) -> ECGValidationReport:
    """Evaluate a prediction against genuinely held-out ECG data.

    The function refuses non-held-out evidence and does not estimate any timing,
    amplitude, or model parameters from the validation signal.
    """
    if not withheld_from_fit:
        raise ValueError("ECG validation evidence must be explicitly withheld_from_fit")
    if measured.waveform_id == predicted.waveform_id:
        raise ValueError("measured and predicted waveform IDs must differ")
    if abs(measured.sample_rate_hz - predicted.sample_rate_hz) > 1e-9:
        raise ValueError("measured and predicted ECGs must have the same sample rate")

    expected = list(spec.expected_leads)
    measured_index = {lead: index for index, lead in enumerate(measured.lead_names)}
    predicted_index = {lead: index for index, lead in enumerate(predicted.lead_names)}
    missing_measured = sorted(set(expected) - set(measured_index))
    missing_predicted = sorted(set(expected) - set(predicted_index))
    if missing_measured:
        raise ValueError("measured ECG is missing expected leads: " + ", ".join(missing_measured))
    if missing_predicted:
        raise ValueError("predicted ECG is missing expected leads: " + ", ".join(missing_predicted))

    shift = _sample_shift(spec.fixed_prediction_offset_ms, measured.sample_rate_hz)
    start_m, stop_m, start_p, stop_p, window = _window_indices(spec, measured, shift)
    if start_p < 0 or stop_p > predicted.n_samples:
        raise ValueError("evaluation window plus fixed prediction offset falls outside prediction")
    if stop_p - start_p != stop_m - start_m:
        raise ValueError("measured and predicted windows must have equal sample counts")

    measured_mv = _to_mv(measured)
    predicted_mv = _to_mv(predicted)
    lead_metrics: list[ECGLeadMetric] = []
    warnings: list[str] = []
    eps = np.finfo(float).eps

    for lead in expected:
        m = measured_mv[measured_index[lead], start_m:stop_m]
        p = predicted_mv[predicted_index[lead], start_p:stop_p]
        residual = p - m
        rmse = float(np.sqrt(np.mean(residual * residual)))
        if spec.normalization == "measured_rms":
            scale = float(np.sqrt(np.mean((m - m.mean()) ** 2)))
        else:
            scale = float(np.ptp(m))
        if scale <= eps:
            raise ValueError(f"measured lead {lead} has zero scale; NRMSE is undefined")
        corr = _correlation(m, p)
        if corr is None:
            warnings.append(f"correlation is undefined for lead {lead} because a trace is constant")
        lead_metrics.append(
            ECGLeadMetric(
                lead=lead,
                rmse=rmse,
                nrmse=rmse / scale,
                correlation=corr,
                measured_scale=scale,
            )
        )

    mean_nrmse = float(np.mean([item.nrmse for item in lead_metrics]))
    correlations = [item.correlation for item in lead_metrics if item.correlation is not None]
    mean_correlation = float(np.mean(correlations)) if correlations else None

    qrs_measured = None
    qrs_predicted = None
    qrs_error = None
    if measured.qrs_onset_ms is not None and predicted.qrs_onset_ms is not None:
        qrs_measured = float(measured.qrs_offset_ms - measured.qrs_onset_ms)  # type: ignore[operator]
        qrs_predicted = float(predicted.qrs_offset_ms - predicted.qrs_onset_ms)  # type: ignore[operator]
        qrs_error = abs(qrs_predicted - qrs_measured)
    elif measured.qrs_onset_ms is not None or predicted.qrs_onset_ms is not None:
        warnings.append("QRS duration error unavailable because only one waveform has QRS annotations")

    failures: list[str] = []
    passed: bool | None = None
    criteria = spec.acceptance
    if criteria is not None:
        if criteria.max_qrs_duration_error_ms is not None:
            if qrs_error is None:
                failures.append("QRS duration criterion cannot be evaluated without QRS annotations")
            elif qrs_error > criteria.max_qrs_duration_error_ms:
                failures.append(
                    f"QRS duration error {qrs_error:.3f} ms exceeds "
                    f"{criteria.max_qrs_duration_error_ms:.3f} ms"
                )
        if criteria.max_mean_nrmse is not None and mean_nrmse > criteria.max_mean_nrmse:
            failures.append(
                f"mean NRMSE {mean_nrmse:.6g} exceeds {criteria.max_mean_nrmse:.6g}"
            )
        if criteria.min_mean_correlation is not None:
            if mean_correlation is None:
                failures.append("mean correlation criterion cannot be evaluated")
            elif mean_correlation < criteria.min_mean_correlation:
                failures.append(
                    f"mean correlation {mean_correlation:.6g} is below "
                    f"{criteria.min_mean_correlation:.6g}"
                )
        if criteria.max_per_lead_nrmse is not None:
            for item in lead_metrics:
                if item.nrmse > criteria.max_per_lead_nrmse:
                    failures.append(
                        f"lead {item.lead} NRMSE {item.nrmse:.6g} exceeds "
                        f"{criteria.max_per_lead_nrmse:.6g}"
                    )
        passed = not failures

    return ECGValidationReport(
        measured_waveform_id=measured.waveform_id,
        predicted_waveform_id=predicted.waveform_id,
        evidence_id=evidence_id,
        withheld_from_fit=True,
        n_samples_compared=stop_m - start_m,
        sample_rate_hz=measured.sample_rate_hz,
        fixed_prediction_offset_ms=spec.fixed_prediction_offset_ms,
        evaluation_window_ms=window,
        lead_metrics=lead_metrics,
        mean_nrmse=mean_nrmse,
        mean_correlation=mean_correlation,
        qrs_duration_measured_ms=qrs_measured,
        qrs_duration_predicted_ms=qrs_predicted,
        qrs_duration_error_ms=qrs_error,
        acceptance_passed=passed,
        acceptance_failures=failures,
        warnings=warnings,
    )
