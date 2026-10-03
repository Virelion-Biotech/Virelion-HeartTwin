import hashlib
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
    prepare_argo_cohort,
    prepare_argo_empirical_study,
    read_argo_record,
    run_argo_surface_baseline,
    score_argo_cohort,
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



def test_official_argo_validation_rejects_unknown_patient_identity(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    with pytest.raises(ValueError, match="Pt1 through Pt9"):
        load_argo_patient(root, strict_official_counts=True)


def test_argo_cohort_preserves_patient_level_weighting(tmp_path: Path) -> None:
    source = _synthetic_patient(tmp_path)
    dataset = tmp_path / "cohort-data"
    dataset.mkdir()
    patient_ids = ["PtA", "PtB"]
    for patient_id in patient_ids:
        shutil.copytree(source, dataset / patient_id)

    prepared = prepare_argo_cohort(
        dataset,
        tmp_path / "cohort-study",
        holdout_fraction=0.5,
        seed=11,
        patient_ids=patient_ids,
        strict_official_counts=False,
    )
    manifest = prepared["manifest"]
    assert manifest["patient_count"] == 2
    assert manifest["primary_weighting"] == "equal_patient"

    prediction_root = tmp_path / "cohort-predictions"
    for item in manifest["patients"]:
        patient_id = item["patient_id"]
        split = json.loads(Path(item["split_path"]).read_text(encoding="utf-8"))
        records = {}
        patient_root = dataset / patient_id
        for point_id in split["holdout_ids"]:
            measurement = extract_argo_measurement(
                read_argo_record(patient_root / point_id)
            )
            records[point_id] = {
                "lat_ms": measurement["local_activation_ms"],
                "ecg": {
                    "lead_names": measurement["lead_names"],
                    "relative_time_ms": measurement["relative_time_ms"].tolist(),
                    "values": measurement["ecg_values"].tolist(),
                },
            }
        out = prediction_root / patient_id
        out.mkdir(parents=True)
        (out / "predictions.json").write_text(
            json.dumps(
                {
                    "schema_version": "hearttwin-argo-predictions-v1",
                    "patient_id": patient_id,
                    "split_sha256": split["split_sha256"],
                    "records": records,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    report = score_argo_cohort(
        prepared["manifest_path"],
        prediction_root,
        gates={
            "lat_rmse_ms_max": 1e-12,
            "lat_abs_bias_ms_max": 1e-12,
            "ecg_mean_correlation_min": 0.999999,
        },
        output_dir=tmp_path / "cohort-reports",
    )
    assert report["status"] == "pass"
    assert report["patient_count"] == 2
    assert report["failed_patients"] == []
    assert report["lat_equal_patient"]["rmse_ms"] == pytest.approx(0.0)
    assert report["lat_equal_patient"]["mean_abs_patient_bias_ms"] == pytest.approx(0.0)
    assert report["ecg_patient_count"] == 2
    assert report["ecg_equal_patient"]["mean_lead_correlation"] == pytest.approx(1.0)
    assert (tmp_path / "cohort-reports" / "cohort-report.json").is_file()


def test_argo_cohort_fails_closed_on_missing_patient_predictions(tmp_path: Path) -> None:
    source = _synthetic_patient(tmp_path)
    dataset = tmp_path / "cohort-data"
    dataset.mkdir()
    for patient_id in ("PtA", "PtB"):
        shutil.copytree(source, dataset / patient_id)
    prepared = prepare_argo_cohort(
        dataset,
        tmp_path / "cohort-study",
        holdout_fraction=0.5,
        seed=1,
        patient_ids=["PtA", "PtB"],
        strict_official_counts=False,
    )
    with pytest.raises(FileNotFoundError, match="Missing ARGO predictions"):
        score_argo_cohort(prepared["manifest_path"], tmp_path / "empty-predictions")


def test_argo_surface_baseline_preserves_blinding(tmp_path: Path) -> None:
    root = _synthetic_patient(tmp_path)
    prepared = prepare_argo_empirical_study(
        root,
        tmp_path / "study",
        holdout_fraction=0.5,
        seed=9,
        strict_official_counts=False,
    )

    class _Adapter:
        def invoke(self, capability, payload):
            assert capability == "ep.calibrate"
            assert payload["backend"] == "surface-eikonal-v1"
            split = prepared["split"]
            assert set(payload["settings"]["root_candidates"]) <= {0, 1, 2, 3}
            out = tmp_path / "fake-cardiep"
            out.mkdir(exist_ok=True)
            activation = out / "activation.json"
            activation.write_text(
                json.dumps(
                    {
                        "schema_version": "cardiep-surface-activation-v1",
                        "activation_ms": [-30.0, -10.0, 20.0, 40.0],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return {
                "contract_version": "1.0",
                "subject_id": split["patient_id"],
                "backend": "surface-eikonal-v1",
                "parameters": {
                    "values": {"isotropic_speed_cm_per_ms": 0.1},
                    "units": {"isotropic_speed_cm_per_ms": "cm/ms"},
                    "source": "calibrated",
                },
                "objective": 0.0,
                "converged": True,
                "posterior_ref": None,
                "simulated": {
                    "contract_version": "1.0",
                    "subject_id": split["patient_id"],
                    "backend": "surface-eikonal-v1",
                    "parameters": {
                        "values": {"isotropic_speed_cm_per_ms": 0.1},
                        "units": {},
                        "source": "calibrated",
                    },
                    "outputs": [
                        {
                            "artifact_id": "activation",
                            "kind": "activation_map",
                            "uri": activation.resolve().as_uri(),
                            "sha256": hashlib.sha256(activation.read_bytes()).hexdigest(),
                            "coordinate_frame": "ARGO_CARTO",
                            "metadata": {},
                        }
                    ],
                    "validation_status": "software_checked",
                    "warnings": [],
                    "provenance": {},
                },
                "diagnostics": {},
                "provenance": {},
            }

    class _Registry:
        def capability(self, name):
            assert name == "ep.calibrate"
            return _Adapter()

    result = run_argo_surface_baseline(
        _Registry(),
        prepared["split_path"],
        tmp_path / "baseline",
        coordinate_unit="mm",
        speed_min_cm_per_ms=0.05,
        speed_max_cm_per_ms=0.2,
        gates={"lat_rmse_ms_max": 1e-12, "lat_abs_bias_ms_max": 1e-12},
    )
    assert result["holdout_report"]["status"] == "pass"
    predictions = json.loads(
        Path(result["predictions_path"]).read_text(encoding="utf-8")
    )
    assert set(predictions["records"]) == set(prepared["split"]["holdout_ids"])
    assert not set(predictions["records"]) & set(prepared["split"]["calibration_ids"])
