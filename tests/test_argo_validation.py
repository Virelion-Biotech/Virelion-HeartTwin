import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from hearttwin.argo_validation import (
    _STANDARD_ECG_LEADS,
    extract_argo_measurement,
    load_argo_patient,
    parse_argo_header,
    prepare_argo_empirical_study,
    read_argo_record,
    score_argo_holdout,
)


def _write_record(root: Path, point_id: str, *, lat_offset_ms: int) -> None:
    fs = 1000
    n_samples = 2500
    n_signals = 15
    gain = 333.3333

    names = ("BIP", "UNI1", "UNI2", *_STANDARD_ECG_LEADS)
    header = [f"{point_id} {n_signals} {fs} {n_samples}"]
    header.extend(
        f"{point_id}.dat 32 {gain}(0)/mV {name}"
        for name in names
    )
    (root / f"{point_id}.hea").write_text(
        "\n".join(header) + "\n",
        encoding="utf-8",
    )

    values = np.zeros((n_samples, n_signals), dtype=float)
    r_index = 1800
    local_index = r_index + lat_offset_ms

    values[local_index:, 0] = 1.0
    values[:, 1] = 0.2 * values[:, 0]
    values[:, 2] = -0.1 * values[:, 0]

    t = np.arange(n_samples, dtype=float)
    qrs = np.exp(-0.5 * ((t - r_index) / 8.0) ** 2)
    twave = 0.25 * np.exp(-0.5 * ((t - (r_index + 240)) / 35.0) ** 2)
    for lead_index in range(12):
        amplitude = 0.5 + 0.05 * lead_index
        polarity = -1.0 if lead_index in {2, 4} else 1.0
        values[:, 3 + lead_index] = polarity * amplitude * (qrs + twave)

    raw = np.rint(values * gain).astype("<i4")
    raw.tofile(root / f"{point_id}.dat")


def _synthetic_patient(tmp_path: Path) -> Path:
    root = tmp_path / "PtSynthetic"
    root.mkdir()

    (root / "XYZmesh.txt").write_text(
        "X,Y,Z\n"
        "0,0,0\n"
        "1,0,0\n"
        "0,1,0\n"
        "0,0,1\n",
        encoding="utf-8",
    )
    (root / "ConnectivityList.txt").write_text(
        "node1,node2,node3\n"
        "1,2,3\n"
        "1,2,4\n"
        "1,3,4\n"
        "2,3,4\n",
        encoding="utf-8",
    )
    (root / "MESHcoloring.txt").write_text(
        "Voltage,LAT\n"
        "1.0,-30\n"
        "1.2,-10\n"
        "NaN,NaN\n"
        "0.8,40\n",
        encoding="utf-8",
    )
    (root / "POS_POINTS.txt").write_text(
        "Point,X,Y,Z\n"
        "1,0,0,0\n"
        "2,1,0,0\n"
        "3,0,1,0\n"
        "4,0,0,1\n",
        encoding="utf-8",
    )
    (root / "AblationPoints.txt").write_text(
        "X,Y,Z\n",
        encoding="utf-8",
    )

    for point_id, offset in zip(("P1", "P2", "P3", "P4"), (-30, -10, 20, 40), strict=True):
        _write_record(root, point_id, lat_offset_ms=offset)
    return root


def test_argo_wfdb_header_and_direct_format32_reader(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    header = parse_argo_header(root / "P1.hea")
    assert header.n_signals == 15
    assert header.sample_rate_hz == pytest.approx(1000.0)
    assert header.n_samples == 2500
    assert tuple(item.description for item in header.signals[-12:]) == _STANDARD_ECG_LEADS

    record = read_argo_record(root / "P1")
    assert record.values_mV.shape == (2500, 15)
    assert record.channel_names[-12:] == _STANDARD_ECG_LEADS
    assert float(np.max(record.channel("BIP"))) == pytest.approx(1.0, abs=0.005)


def test_signal_based_argo_lat_and_ecg_extraction(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    measurement = extract_argo_measurement(read_argo_record(root / "P3"))

    assert measurement["local_activation_ms"] == pytest.approx(20.0, abs=1.0)
    assert abs(measurement["relative_time_ms"][0] + 350.0) < 1e-12
    assert abs(measurement["relative_time_ms"][-1] - 350.0) < 1e-12
    assert measurement["ecg_values"].shape[0] == 12
    assert np.max(np.abs(measurement["ecg_values"])) == pytest.approx(1.0)
    assert measurement["local_activation_confidence_ratio"] > 1.0


def test_argo_loader_preserves_legitimate_paired_nan_map_holes(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    patient = load_argo_patient(root, strict_official_counts=False)
    points = {item.point_id: item for item in patient["points"]}

    assert patient["n_vertices"] == 4
    assert patient["n_triangles"] == 4
    assert patient["n_points"] == 4
    assert points["P3"].map_lat_ms is None
    assert points["P3"].map_voltage_mV is None


def test_argo_prepare_never_writes_heldout_targets(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    result = prepare_argo_empirical_study(
        root,
        tmp_path / "study",
        holdout_fraction=0.5,
        seed=17,
        strict_official_counts=False,
    )
    split = result["split"]
    targets = result["calibration_targets"]

    assert len(split["calibration_ids"]) == 2
    assert len(split["holdout_ids"]) == 2
    target_ids = {item["point_id"] for item in targets["targets"]}
    assert target_ids == set(split["calibration_ids"])
    assert not target_ids & set(split["holdout_ids"])
    assert targets["heldout_targets_included"] is False

    copied_root = tmp_path / "copied" / root.name
    copied_root.parent.mkdir(parents=True)
    shutil.copytree(root, copied_root)
    copied = prepare_argo_empirical_study(
        copied_root,
        tmp_path / "study-copy",
        holdout_fraction=0.5,
        seed=17,
        strict_official_counts=False,
    )
    assert copied["split"]["patient_dir"] != split["patient_dir"]
    assert copied["split"]["split_sha256"] == split["split_sha256"]
    assert copied["split"]["calibration_ids"] == split["calibration_ids"]
    assert copied["split"]["holdout_ids"] == split["holdout_ids"]


def test_argo_holdout_scoring_recomputes_raw_targets_and_accepts_perfect_predictions(
    tmp_path: Path,
) -> None:
    root = _synthetic_patient(tmp_path)
    prepared = prepare_argo_empirical_study(
        root,
        tmp_path / "study",
        holdout_fraction=0.5,
        seed=7,
        strict_official_counts=False,
    )
    split = prepared["split"]
    records = {}
    for point_id in split["holdout_ids"]:
        measurement = extract_argo_measurement(read_argo_record(root / point_id))
        records[point_id] = {
            "lat_ms": measurement["local_activation_ms"],
            "ecg": {
                "lead_names": measurement["lead_names"],
                "relative_time_ms": measurement["relative_time_ms"].tolist(),
                "values": measurement["ecg_values"].tolist(),
            },
        }

    predictions = {
        "schema_version": "hearttwin-argo-predictions-v1",
        "patient_id": split["patient_id"],
        "split_sha256": split["split_sha256"],
        "records": records,
    }
    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(
        json.dumps(predictions, indent=2) + "\n",
        encoding="utf-8",
    )

    report = score_argo_holdout(
        prepared["split_path"],
        predictions_path,
        gates={
            "lat_rmse_ms_max": 1e-12,
            "lat_abs_bias_ms_max": 1e-12,
            "ecg_mean_correlation_min": 0.999999,
        },
    )
    assert report["status"] == "pass"
    assert report["lat_signal_metrics"]["rmse_ms"] == pytest.approx(0.0)
    assert report["ecg_metrics"]["mean_lead_correlation"] == pytest.approx(1.0)
    assert report["secondary_map_lat_metrics"] is not None


def test_argo_predictions_are_bound_to_exact_holdout_split(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    prepared = prepare_argo_empirical_study(
        root,
        tmp_path / "study",
        holdout_fraction=0.5,
        seed=7,
        strict_official_counts=False,
    )
    split = prepared["split"]
    predictions = {
        "schema_version": "hearttwin-argo-predictions-v1",
        "patient_id": split["patient_id"],
        "split_sha256": "0" * 64,
        "records": {
            point_id: {"lat_ms": 0.0}
            for point_id in split["holdout_ids"]
        },
    }
    path = tmp_path / "bad-predictions.json"
    path.write_text(json.dumps(predictions) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="different split"):
        score_argo_holdout(prepared["split_path"], path)


def test_argo_coloring_rejects_unpaired_missingness(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    (root / "MESHcoloring.txt").write_text(
        "Voltage,LAT\n"
        "1.0,-30\n"
        "1.2,-10\n"
        "NaN,20\n"
        "0.8,40\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missingness"):
        load_argo_patient(root, strict_official_counts=False)
