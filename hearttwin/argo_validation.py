from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .provenance import sha256


ARGO_VERSION = "1.0.0"
ARGO_DOI = "10.13026/8gh2-e660"
_STANDARD_ECG_LEADS = (
    "I",
    "II",
    "III",
    "aVL",
    "aVR",
    "aVF",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
)
_EXPECTED_POINT_COUNTS = {
    "Pt1": 157,
    "Pt2": 104,
    "Pt3": 90,
    "Pt4": 471,
    "Pt5": 46,
    "Pt6": 839,
    "Pt7": 76,
    "Pt8": 129,
    "Pt9": 50,
}
_GAIN_RE = re.compile(
    r"^(?P<gain>[-+0-9.eE]+)(?:\((?P<baseline>[-+0-9.eE]+)\))?/(?P<unit>.+)$"
)


@dataclass(frozen=True)
class ArgoSignalSpec:
    format_code: int
    gain: float
    baseline: float
    unit: str
    description: str


@dataclass(frozen=True)
class ArgoHeader:
    record_name: str
    n_signals: int
    sample_rate_hz: float
    n_samples: int
    signals: tuple[ArgoSignalSpec, ...]

    def __post_init__(self) -> None:
        if self.n_signals != len(self.signals):
            raise ValueError("ARGO header signal count does not match signal definitions")
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("ARGO sample rate must be positive and finite")
        if self.n_samples < 1:
            raise ValueError("ARGO n_samples must be positive")


@dataclass(frozen=True)
class ArgoRecord:
    header: ArgoHeader
    values_mV: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.values_mV, dtype=float)
        if values.shape != (self.header.n_samples, self.header.n_signals):
            raise ValueError("ARGO signal matrix has the wrong shape")
        if not np.all(np.isfinite(values)):
            raise ValueError("ARGO signal matrix contains non-finite values")
        object.__setattr__(self, "values_mV", values)

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(item.description for item in self.header.signals)

    def channel(self, name: str) -> np.ndarray:
        try:
            index = self.channel_names.index(name)
        except ValueError as exc:
            raise KeyError(name) from exc
        return self.values_mV[:, index]


@dataclass(frozen=True)
class ArgoPoint:
    point_id: str
    xyz: np.ndarray
    nearest_mesh_index: int
    nearest_mesh_distance: float
    map_voltage_mV: float | None
    map_lat_ms: float | None
    record_base: Path

    def __post_init__(self) -> None:
        xyz = np.asarray(self.xyz, dtype=float)
        if xyz.shape != (3,) or not np.all(np.isfinite(xyz)):
            raise ValueError("ARGO point xyz must contain three finite values")
        if self.nearest_mesh_index < 0:
            raise ValueError("nearest_mesh_index must be non-negative")
        if not math.isfinite(self.nearest_mesh_distance) or self.nearest_mesh_distance < 0:
            raise ValueError("nearest_mesh_distance must be finite and non-negative")
        object.__setattr__(self, "xyz", xyz)


def parse_argo_header(path: str | Path, *, strict_argo_v1: bool = True) -> ArgoHeader:
    lines = [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError("ARGO WFDB header is empty")
    first = lines[0].split()
    if len(first) < 4:
        raise ValueError("ARGO WFDB first header line must contain record, signals, fs, samples")
    record_name = first[0]
    n_signals = int(first[1])
    sample_rate_hz = float(first[2].split("/")[0])
    n_samples = int(first[3])
    if len(lines) != n_signals + 1:
        raise ValueError(
            f"ARGO WFDB header declares {n_signals} signals but has {len(lines)-1} signal lines"
        )

    signals: list[ArgoSignalSpec] = []
    for line in lines[1:]:
        tokens = line.split()
        if len(tokens) < 3:
            raise ValueError(f"Malformed ARGO WFDB signal line: {line!r}")
        format_code = int(tokens[1].split("x")[0].split(":")[0].split("+")[0])
        gain_match = _GAIN_RE.match(tokens[2])
        if gain_match is None:
            raise ValueError(f"Could not parse ARGO WFDB gain token {tokens[2]!r}")
        gain = float(gain_match.group("gain"))
        baseline = float(gain_match.group("baseline") or 0.0)
        unit = gain_match.group("unit")
        if not math.isfinite(gain) or gain <= 0:
            raise ValueError("ARGO WFDB gain must be positive and finite")
        if not math.isfinite(baseline):
            raise ValueError("ARGO WFDB baseline must be finite")
        signals.append(
            ArgoSignalSpec(
                format_code=format_code,
                gain=gain,
                baseline=baseline,
                unit=unit,
                description=tokens[-1],
            )
        )

    header = ArgoHeader(
        record_name=record_name,
        n_signals=n_signals,
        sample_rate_hz=sample_rate_hz,
        n_samples=n_samples,
        signals=tuple(signals),
    )
    if strict_argo_v1:
        if header.n_signals != 15:
            raise ValueError("ARGO v1 records must contain 15 signals")
        if not math.isclose(header.sample_rate_hz, 1000.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("ARGO v1 records must be sampled at 1000 Hz")
        if header.n_samples != 2500:
            raise ValueError("ARGO v1 records must contain 2500 samples")
        if any(item.format_code != 32 for item in header.signals):
            raise ValueError("ARGO v1 direct reader supports WFDB format 32 only")
        if any(item.unit != "mV" for item in header.signals):
            raise ValueError("ARGO v1 signal units must be mV")
        ecg = tuple(item.description for item in header.signals[-12:])
        if ecg != _STANDARD_ECG_LEADS:
            raise ValueError(
                f"ARGO ECG lead order mismatch: expected {_STANDARD_ECG_LEADS}, got {ecg}"
            )
    return header


def read_argo_record(record_base: str | Path, *, strict_argo_v1: bool = True) -> ArgoRecord:
    base = Path(record_base)
    if base.suffix in {".hea", ".dat"}:
        base = base.with_suffix("")
    header = parse_argo_header(base.with_suffix(".hea"), strict_argo_v1=strict_argo_v1)
    dat_path = base.with_suffix(".dat")
    raw = np.fromfile(dat_path, dtype="<i4")
    expected = header.n_samples * header.n_signals
    if raw.size != expected:
        raise ValueError(
            f"ARGO data file has {raw.size} int32 values; expected {expected}"
        )
    raw = raw.reshape(header.n_samples, header.n_signals)
    values = np.empty(raw.shape, dtype=float)
    for index, signal in enumerate(header.signals):
        values[:, index] = (raw[:, index].astype(float) - signal.baseline) / signal.gain
    return ArgoRecord(header=header, values_mV=values)


def _read_csv_rows(path: Path, expected_header: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != expected_header:
            raise ValueError(
                f"{path.name} header must be {expected_header}; got {reader.fieldnames}"
            )
        return [dict(row) for row in reader]


def _read_xyzmesh(path: Path) -> np.ndarray:
    rows = _read_csv_rows(path, ("X", "Y", "Z"))
    values = np.asarray(
        [[float(row["X"]), float(row["Y"]), float(row["Z"])] for row in rows],
        dtype=float,
    )
    if values.ndim != 2 or values.shape[1] != 3 or not np.all(np.isfinite(values)):
        raise ValueError("ARGO XYZmesh must be a finite N x 3 matrix")
    return values


def _read_connectivity(path: Path, n_vertices: int) -> np.ndarray:
    rows = _read_csv_rows(path, ("node1", "node2", "node3"))
    triangles = np.asarray(
        [
            [int(row["node1"]), int(row["node2"]), int(row["node3"])]
            for row in rows
        ],
        dtype=int,
    )
    if triangles.ndim != 2 or triangles.shape[1] != 3:
        raise ValueError("ARGO connectivity must be an N x 3 triangle matrix")
    if np.any(triangles < 1) or np.any(triangles > n_vertices):
        raise ValueError("ARGO connectivity contains out-of-range one-based node indices")
    if np.any(np.sort(triangles, axis=1)[:, 1:] == np.sort(triangles, axis=1)[:, :-1]):
        raise ValueError("ARGO connectivity contains repeated nodes in a triangle")
    return triangles - 1


def _read_coloring(path: Path, n_vertices: int) -> tuple[np.ndarray, np.ndarray]:
    rows = _read_csv_rows(path, ("Voltage", "LAT"))
    if len(rows) != n_vertices:
        raise ValueError(
            f"ARGO MESHcoloring has {len(rows)} rows but XYZmesh has {n_vertices}"
        )
    voltage = np.asarray([float(row["Voltage"]) for row in rows], dtype=float)
    lat = np.asarray([float(row["LAT"]) for row in rows], dtype=float)
    paired_missing = np.isnan(voltage) == np.isnan(lat)
    if not np.all(paired_missing):
        raise ValueError("ARGO voltage/LAT missingness must be paired")
    if np.any(np.isinf(voltage)) or np.any(np.isinf(lat)):
        raise ValueError("ARGO MESHcoloring cannot contain infinite values")
    return voltage, lat


def _read_positions(path: Path) -> tuple[list[str], np.ndarray]:
    rows = _read_csv_rows(path, ("Point", "X", "Y", "Z"))
    ids = [f"P{int(row['Point'])}" for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("ARGO POS_POINTS contains duplicate point IDs")
    xyz = np.asarray(
        [[float(row["X"]), float(row["Y"]), float(row["Z"])] for row in rows],
        dtype=float,
    )
    if not np.all(np.isfinite(xyz)):
        raise ValueError("ARGO POS_POINTS coordinates must be finite")
    return ids, xyz


def load_argo_patient(
    patient_dir: str | Path,
    *,
    strict_official_counts: bool = True,
) -> dict[str, Any]:
    root = Path(patient_dir).expanduser().resolve()
    patient_id = root.name
    required = (
        "XYZmesh.txt",
        "ConnectivityList.txt",
        "MESHcoloring.txt",
        "POS_POINTS.txt",
        "AblationPoints.txt",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"ARGO patient folder is missing files: {missing}")

    vertices = _read_xyzmesh(root / "XYZmesh.txt")
    connectivity = _read_connectivity(root / "ConnectivityList.txt", len(vertices))
    voltage, map_lat = _read_coloring(root / "MESHcoloring.txt", len(vertices))
    point_ids, positions = _read_positions(root / "POS_POINTS.txt")

    if strict_official_counts and patient_id in _EXPECTED_POINT_COUNTS:
        expected = _EXPECTED_POINT_COUNTS[patient_id]
        if len(point_ids) != expected:
            raise ValueError(
                f"{patient_id} has {len(point_ids)} POS_POINTS; ARGO v1 expects {expected}"
            )

    points: list[ArgoPoint] = []
    for point_id, xyz in zip(point_ids, positions, strict=True):
        record_base = root / point_id
        if not record_base.with_suffix(".hea").is_file():
            raise FileNotFoundError(record_base.with_suffix(".hea"))
        if not record_base.with_suffix(".dat").is_file():
            raise FileNotFoundError(record_base.with_suffix(".dat"))
        distance = np.linalg.norm(vertices - xyz[None, :], axis=1)
        nearest = int(np.argmin(distance))
        points.append(
            ArgoPoint(
                point_id=point_id,
                xyz=xyz,
                nearest_mesh_index=nearest,
                nearest_mesh_distance=float(distance[nearest]),
                map_voltage_mV=(
                    None if np.isnan(voltage[nearest]) else float(voltage[nearest])
                ),
                map_lat_ms=None if np.isnan(map_lat[nearest]) else float(map_lat[nearest]),
                record_base=record_base,
            )
        )

    return {
        "schema_version": "hearttwin-argo-patient-v1",
        "dataset": "ARGO",
        "dataset_version": ARGO_VERSION,
        "doi": ARGO_DOI,
        "patient_id": patient_id,
        "coordinate_unit": "CARTO_native",
        "n_vertices": int(len(vertices)),
        "n_triangles": int(len(connectivity)),
        "n_points": len(points),
        "points": points,
        "vertices": vertices,
        "connectivity": connectivity,
        "map_voltage_mV": voltage,
        "map_lat_ms": map_lat,
    }


def _ecg_matrix(record: ArgoRecord) -> np.ndarray:
    indices = [record.channel_names.index(name) for name in _STANDARD_ECG_LEADS]
    return record.values_mV[:, indices].T


def extract_argo_measurement(
    record: ArgoRecord,
    *,
    pre_ms: float = 350.0,
    post_ms: float = 350.0,
    local_search_ms: float = 200.0,
) -> dict[str, Any]:
    fs = float(record.header.sample_rate_hz)
    if pre_ms <= 0 or post_ms <= 0 or local_search_ms <= 0:
        raise ValueError("ARGO extraction windows must be positive")
    ecg = _ecg_matrix(record)
    demeaned = ecg - np.median(ecg, axis=1, keepdims=True)
    energy = np.sqrt(np.mean(demeaned**2, axis=0))

    pre = int(round(pre_ms * fs / 1000.0))
    post = int(round(post_ms * fs / 1000.0))
    search_start = max(pre, record.header.n_samples - int(round(1500.0 * fs / 1000.0)))
    search_stop = record.header.n_samples - post
    if search_stop <= search_start:
        raise ValueError("ARGO record is too short for the requested ECG window")
    r_index = search_start + int(np.argmax(energy[search_start:search_stop]))

    bipolar = record.values_mV[:, 0]
    derivative = np.diff(bipolar, prepend=bipolar[0])
    radius = int(round(local_search_ms * fs / 1000.0))
    lo = max(1, r_index - radius)
    hi = min(record.header.n_samples, r_index + radius + 1)
    if hi - lo < 3:
        raise ValueError("ARGO local-activation search window is empty")
    local_index = lo + int(np.argmax(np.abs(derivative[lo:hi])))
    local_lat_ms = (local_index - r_index) * 1000.0 / fs

    segment = ecg[:, r_index - pre : r_index + post + 1].copy()
    baseline_width = max(1, min(segment.shape[1] // 5, int(round(80.0 * fs / 1000.0))))
    segment -= np.median(segment[:, :baseline_width], axis=1, keepdims=True)
    scale = float(np.max(np.abs(segment)))
    if scale <= 1e-12:
        raise ValueError("ARGO ECG segment is effectively flat")
    segment /= scale
    relative_time_ms = (
        np.arange(segment.shape[1], dtype=float) - pre
    ) * 1000.0 / fs

    abs_derivative = np.abs(derivative[lo:hi])
    background = float(np.median(abs_derivative))
    peak = float(np.max(abs_derivative))
    confidence_ratio = peak / max(background, 1e-12)

    return {
        "reference_index": int(r_index),
        "local_activation_index": int(local_index),
        "local_activation_ms": float(local_lat_ms),
        "local_activation_confidence_ratio": confidence_ratio,
        "lead_names": list(_STANDARD_ECG_LEADS),
        "relative_time_ms": relative_time_ms,
        "ecg_values": segment,
    }


def _split_ids(
    patient_id: str,
    point_ids: list[str],
    *,
    holdout_fraction: float,
    seed: int,
) -> tuple[list[str], list[str]]:
    if not math.isfinite(holdout_fraction) or not 0 < holdout_fraction < 1:
        raise ValueError("holdout_fraction must lie strictly between 0 and 1")
    if len(point_ids) < 2:
        raise ValueError("ARGO empirical validation requires at least two mapping points")
    ranked = sorted(
        point_ids,
        key=lambda point_id: hashlib.sha256(
            f"{patient_id}|{point_id}|{seed}".encode("utf-8")
        ).hexdigest(),
    )
    n_holdout = min(
        len(point_ids) - 1,
        max(1, int(round(len(point_ids) * holdout_fraction))),
    )
    holdout = sorted(ranked[:n_holdout])
    calibration = sorted(ranked[n_holdout:])
    return calibration, holdout


def prepare_argo_empirical_study(
    patient_dir: str | Path,
    output_dir: str | Path,
    *,
    holdout_fraction: float = 0.2,
    seed: int = 42,
    strict_official_counts: bool = True,
) -> dict[str, Any]:
    patient = load_argo_patient(
        patient_dir,
        strict_official_counts=strict_official_counts,
    )
    points = {item.point_id: item for item in patient["points"]}
    calibration_ids, holdout_ids = _split_ids(
        patient["patient_id"],
        list(points),
        holdout_fraction=holdout_fraction,
        seed=seed,
    )
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    calibration_targets = []
    for point_id in calibration_ids:
        point = points[point_id]
        measurement = extract_argo_measurement(read_argo_record(point.record_base))
        calibration_targets.append(
            {
                "point_id": point_id,
                "xyz": point.xyz.tolist(),
                "signal_lat_ms": measurement["local_activation_ms"],
                "signal_lat_confidence_ratio": measurement[
                    "local_activation_confidence_ratio"
                ],
                "secondary_map_lat_ms": point.map_lat_ms,
                "secondary_map_voltage_mV": point.map_voltage_mV,
            }
        )

    extraction = {
        "r_reference": "maximum_multilead_rms_in_reliable_final-beat_search_window",
        "local_activation": "maximum_absolute_bipolar_EGM_derivative_within_±200_ms_of_R",
        "ecg_window_ms": [-350.0, 350.0],
        "ecg_normalization": "per-lead baseline subtraction plus one global amplitude scale",
    }
    split_identity = {
        "schema_version": "hearttwin-argo-split-identity-v1",
        "dataset": "ARGO",
        "dataset_version": ARGO_VERSION,
        "doi": ARGO_DOI,
        "patient_id": patient["patient_id"],
        "seed": int(seed),
        "holdout_fraction": float(holdout_fraction),
        "calibration_ids": calibration_ids,
        "holdout_ids": holdout_ids,
        "extraction": extraction,
    }
    split_payload = {
        "schema_version": "hearttwin-argo-split-v1",
        **split_identity,
        "schema_version": "hearttwin-argo-split-v1",
        "patient_dir": str(Path(patient_dir).expanduser().resolve()),
        "scientific_boundary": (
            "ARGO provides surface ECG, intracardiac EGMs and reconstructed CARTO EA maps, "
            "but not a volumetric fibre-resolved ventricular anatomy for CardiEP. This split "
            "supports empirical observable validation; patient-specific parameter recovery "
            "requires an independently registered volumetric anatomy/fibre model."
        ),
    }
    split_payload["split_sha256"] = sha256(split_identity)

    calibration_payload = {
        "schema_version": "hearttwin-argo-calibration-targets-v1",
        "patient_id": patient["patient_id"],
        "split_sha256": split_payload["split_sha256"],
        "targets": calibration_targets,
        "heldout_targets_included": False,
    }
    split_path = output / "split.json"
    calibration_path = output / "calibration-targets.json"
    split_path.write_text(
        json.dumps(split_payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    calibration_path.write_text(
        json.dumps(calibration_payload, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return {
        "split_path": str(split_path),
        "calibration_targets_path": str(calibration_path),
        "split": split_payload,
        "calibration_targets": calibration_payload,
    }


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=float).reshape(-1)
    right = np.asarray(right, dtype=float).reshape(-1)
    if left.size != right.size or left.size < 2:
        raise ValueError("Correlation arrays must have the same length >= 2")
    if np.allclose(left, left[0]) or np.allclose(right, right[0]):
        return 1.0 if np.allclose(left, right) else 0.0
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else 0.0


def _score_ecg(
    measured_time: np.ndarray,
    measured_values: np.ndarray,
    predicted: dict[str, Any],
) -> dict[str, float]:
    lead_names = tuple(str(item) for item in predicted.get("lead_names") or ())
    if lead_names != _STANDARD_ECG_LEADS:
        raise ValueError("Predicted ARGO ECG must use the canonical 12-lead order")
    pred_time = np.asarray(predicted.get("relative_time_ms"), dtype=float).reshape(-1)
    pred_values = np.asarray(predicted.get("values"), dtype=float)
    if pred_values.shape != (12, pred_time.size):
        raise ValueError("Predicted ECG values must have shape (12, len(relative_time_ms))")
    if pred_time.size < 2 or not np.all(np.diff(pred_time) > 0):
        raise ValueError("Predicted ECG relative_time_ms must be strictly increasing")
    if not np.all(np.isfinite(pred_time)) or not np.all(np.isfinite(pred_values)):
        raise ValueError("Predicted ECG must be finite")

    common_mask = (measured_time >= pred_time[0]) & (measured_time <= pred_time[-1])
    if int(np.sum(common_mask)) < 10:
        raise ValueError("Predicted/measured ECG windows have insufficient temporal overlap")
    common_time = measured_time[common_mask]
    measured = measured_values[:, common_mask]
    interpolated = np.vstack(
        [np.interp(common_time, pred_time, pred_values[i]) for i in range(12)]
    )
    baseline_width = max(1, interpolated.shape[1] // 5)
    interpolated -= np.median(
        interpolated[:, :baseline_width],
        axis=1,
        keepdims=True,
    )
    scale = float(np.max(np.abs(interpolated)))
    if scale <= 1e-12:
        raise ValueError("Predicted ECG is effectively flat")
    interpolated /= scale

    correlations = [
        _correlation(measured[i], interpolated[i])
        for i in range(12)
    ]
    residual = interpolated - measured
    return {
        "mean_lead_correlation": float(np.mean(correlations)),
        "median_lead_correlation": float(np.median(correlations)),
        "min_lead_correlation": float(np.min(correlations)),
        "normalized_rmse": float(np.sqrt(np.mean(residual**2))),
    }


def score_argo_holdout(
    split_path: str | Path,
    predictions_path: str | Path,
    *,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    split = json.loads(Path(split_path).read_text(encoding="utf-8"))
    predictions = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    if split.get("schema_version") != "hearttwin-argo-split-v1":
        raise ValueError("Unsupported ARGO split schema")
    if predictions.get("schema_version") != "hearttwin-argo-predictions-v1":
        raise ValueError("Unsupported ARGO predictions schema")
    if predictions.get("patient_id") != split.get("patient_id"):
        raise ValueError("ARGO prediction patient_id does not match split")
    if predictions.get("split_sha256") != split.get("split_sha256"):
        raise ValueError("ARGO predictions were generated for a different split")

    records = predictions.get("records")
    if not isinstance(records, dict):
        raise TypeError("ARGO predictions.records must be an object")
    expected_ids = set(split["holdout_ids"])
    if set(records) != expected_ids:
        raise ValueError(
            "ARGO predictions must contain exactly the held-out point IDs"
        )

    patient = load_argo_patient(split["patient_dir"], strict_official_counts=False)
    points = {item.point_id: item for item in patient["points"]}
    rows = []
    for point_id in split["holdout_ids"]:
        point = points[point_id]
        measured = extract_argo_measurement(read_argo_record(point.record_base))
        predicted = records[point_id]
        pred_lat = float(predicted["lat_ms"])
        if not math.isfinite(pred_lat):
            raise ValueError("Predicted LAT values must be finite")
        row: dict[str, Any] = {
            "point_id": point_id,
            "measured_signal_lat_ms": measured["local_activation_ms"],
            "predicted_lat_ms": pred_lat,
            "secondary_map_lat_ms": point.map_lat_ms,
            "signal_lat_confidence_ratio": measured[
                "local_activation_confidence_ratio"
            ],
        }
        if predicted.get("ecg") is not None:
            row["ecg"] = _score_ecg(
                np.asarray(measured["relative_time_ms"], dtype=float),
                np.asarray(measured["ecg_values"], dtype=float),
                dict(predicted["ecg"]),
            )
        rows.append(row)

    measured_lat = np.asarray([row["measured_signal_lat_ms"] for row in rows], dtype=float)
    predicted_lat = np.asarray([row["predicted_lat_ms"] for row in rows], dtype=float)
    lat_residual = predicted_lat - measured_lat
    lat_metrics = {
        "n": len(rows),
        "rmse_ms": float(np.sqrt(np.mean(lat_residual**2))),
        "mae_ms": float(np.mean(np.abs(lat_residual))),
        "bias_ms": float(np.mean(lat_residual)),
        "max_abs_ms": float(np.max(np.abs(lat_residual))),
        "correlation": _correlation(measured_lat, predicted_lat) if len(rows) >= 2 else None,
    }

    map_rows = [row for row in rows if row["secondary_map_lat_ms"] is not None]
    map_metrics = None
    if map_rows:
        map_truth = np.asarray([row["secondary_map_lat_ms"] for row in map_rows], dtype=float)
        map_pred = np.asarray([row["predicted_lat_ms"] for row in map_rows], dtype=float)
        residual = map_pred - map_truth
        map_metrics = {
            "n": len(map_rows),
            "rmse_ms": float(np.sqrt(np.mean(residual**2))),
            "mae_ms": float(np.mean(np.abs(residual))),
            "bias_ms": float(np.mean(residual)),
            "correlation": _correlation(map_truth, map_pred) if len(map_rows) >= 2 else None,
            "interpretation": (
                "Secondary concordance against the reconstructed/interpolated CARTO LAT map; "
                "not an independent held-out measurement."
            ),
        }

    ecg_rows = [row["ecg"] for row in rows if row.get("ecg") is not None]
    ecg_metrics = None
    if ecg_rows:
        ecg_metrics = {
            "n": len(ecg_rows),
            "mean_lead_correlation": float(
                np.mean([row["mean_lead_correlation"] for row in ecg_rows])
            ),
            "median_lead_correlation": float(
                np.median([row["median_lead_correlation"] for row in ecg_rows])
            ),
            "worst_record_min_lead_correlation": float(
                np.min([row["min_lead_correlation"] for row in ecg_rows])
            ),
            "mean_normalized_rmse": float(
                np.mean([row["normalized_rmse"] for row in ecg_rows])
            ),
        }

    gate_config = dict(gates or {})
    checks: dict[str, bool] = {}
    if gate_config.get("lat_rmse_ms_max") is not None:
        threshold = float(gate_config["lat_rmse_ms_max"])
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("lat_rmse_ms_max must be finite and non-negative")
        checks["lat_rmse"] = lat_metrics["rmse_ms"] <= threshold
    if gate_config.get("lat_abs_bias_ms_max") is not None:
        threshold = float(gate_config["lat_abs_bias_ms_max"])
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("lat_abs_bias_ms_max must be finite and non-negative")
        checks["lat_abs_bias"] = abs(lat_metrics["bias_ms"]) <= threshold
    if gate_config.get("ecg_mean_correlation_min") is not None:
        threshold = float(gate_config["ecg_mean_correlation_min"])
        if not -1 <= threshold <= 1:
            raise ValueError("ecg_mean_correlation_min must lie in [-1, 1]")
        checks["ecg_available"] = ecg_metrics is not None
        checks["ecg_mean_correlation"] = bool(
            ecg_metrics is not None
            and ecg_metrics["mean_lead_correlation"] >= threshold
        )

    status = "not_gated" if not checks else ("pass" if all(checks.values()) else "fail")
    return {
        "schema_version": "hearttwin-argo-holdout-report-v1",
        "dataset": "ARGO",
        "dataset_version": ARGO_VERSION,
        "doi": ARGO_DOI,
        "patient_id": split["patient_id"],
        "split_sha256": split["split_sha256"],
        "n_calibration": len(split["calibration_ids"]),
        "n_holdout": len(split["holdout_ids"]),
        "lat_signal_metrics": lat_metrics,
        "secondary_map_lat_metrics": map_metrics,
        "ecg_metrics": ecg_metrics,
        "records": rows,
        "gates": gate_config,
        "checks": checks,
        "status": status,
        "scientific_boundary": (
            "This is held-out observable validation on a nine-patient post-ischemic VT dataset. "
            "It is not a clinical validation study. The signal-based LAT extractor is an explicit "
            "computational definition and is not identical to CARTO's proprietary LAT annotation."
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hearttwin-argo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("patient_dir")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("patient_dir")
    prepare.add_argument("output_dir")
    prepare.add_argument("--holdout-fraction", type=float, default=0.2)
    prepare.add_argument("--seed", type=int, default=42)
    score = sub.add_parser("score")
    score.add_argument("split")
    score.add_argument("predictions")
    score.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "validate":
        patient = load_argo_patient(args.patient_dir)
        print(
            json.dumps(
                {
                    "patient_id": patient["patient_id"],
                    "n_vertices": patient["n_vertices"],
                    "n_triangles": patient["n_triangles"],
                    "n_points": patient["n_points"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.cmd == "prepare":
        result = prepare_argo_empirical_study(
            args.patient_dir,
            args.output_dir,
            holdout_fraction=args.holdout_fraction,
            seed=args.seed,
        )
        print(json.dumps(result["split"], indent=2, sort_keys=True))
        return 0
    if args.cmd == "score":
        report = score_argo_holdout(args.split, args.predictions)
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded, encoding="utf-8")
            print(output)
        else:
            print(encoded, end="")
        return 2 if report["status"] == "fail" else 0
    return 2
