"""Strict held-out BSP evaluation for native phenomenological propagation artifacts.

No clinical defaults, montage inference, fitting, alignment search or resampling.
"""
from __future__ import annotations

import json

import numpy as np

from .patient_ecg_eval import ECGWaveform, evaluate_heldout_ecg
from .patient_observations import verified_bytes


def _waveform(record, values, identifier):
    time = np.asarray(record["time_s"], dtype=float)
    array = np.asarray(values, dtype=float)
    if (time.ndim != 1 or len(time) < 2 or not np.isfinite(time).all()
            or time[0] != 0 or np.any(np.diff(time) <= 0)
            or not np.allclose(np.diff(time), time[1], rtol=1e-9, atol=1e-12)):
        raise ValueError("Explicit zero-origin uniform BSP time grid required; no resampling")
    if array.shape != (len(time), len(record["lead_names"])):
        raise ValueError("BSP values must be time-by-channel in declared channel order")
    if record.get("signal_unit") != "V":
        raise ValueError("Explicit physical BSP voltage unit V required")
    wave = ECGWaveform(
        waveform_id=identifier, lead_names=record["lead_names"],
        sample_rate_hz=1 / time[1], signals=array.T.tolist(), unit="V",
        metadata={"measurement_reference": record["measurement_reference"],
                  "subject_id": record["subject_id"], "coordinate_frame": record["coordinate_frame"]})
    return wave, time


def evaluate_bsp_holdout(prediction, observation, *, spec):
    """Evaluate immutable observations against a frozen native BSP prediction.

    This lower-level research comparison does not replace PatientCase's
    independent-source qualification or certify data really stayed out of fit.
    """
    if spec.require_standard_12_lead or spec.fixed_prediction_offset_ms != 0:
        raise ValueError("Declare a nonstandard BSP protocol with zero fixed offset")
    predicted = json.loads(verified_bytes(prediction))
    measured = json.loads(verified_bytes(observation))
    if (predicted.get("schema") != "hearttwin-cubic-excitable-v1"
            or predicted.get("signal_kind") != "body_surface_potentials"
            or measured.get("schema") != "hearttwin-bsp-observation-v1"):
        raise ValueError("Native BSP prediction and explicit measured BSP schema required")
    for key in ["subject_id", "coordinate_frame", "measurement_reference", "lead_names"]:
        if not predicted.get(key) or predicted.get(key) != measured.get(key):
            raise ValueError(f"BSP {key} must match exactly; no implicit reference/montage conversion")
    if measured.get("withheld_from_fit") is not True:
        raise ValueError("BSP observation must be explicitly withheld_from_fit")
    evidence_id = measured.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError("Explicit BSP holdout evidence_id required")
    if list(spec.expected_leads) != predicted["lead_names"]:
        raise ValueError("Predeclared BSP channel order must match exactly")
    pw, pt = _waveform(predicted, predicted["bsp_V"], prediction["sha256"])
    mw, mt = _waveform(measured, measured["values_V"], observation["sha256"])
    if pt.shape != mt.shape or not np.allclose(pt, mt, rtol=0, atol=1e-12):
        raise ValueError("BSP time grids must match; no alignment or implicit resampling")
    return evaluate_heldout_ecg(
        measured=mw, predicted=pw, evidence_id=evidence_id, withheld_from_fit=True, spec=spec)
