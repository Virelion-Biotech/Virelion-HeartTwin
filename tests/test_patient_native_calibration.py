"""Native four-chamber calibration retains existing fit/holdout and unit gates."""

import hashlib
import json
from copy import deepcopy

import numpy as np
import pytest

from hearttwin.patient_domain import PatientDomainJob, execute_patient_domain, validate_domain_job


def native_calibration_request(tmp_path):
    from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop

    compartments = {}
    for name in COMPARTMENTS:
        p = {"unstressed_volume_mL": 0., "external_pressure_mmHg": 0.}
        if name in {"LA", "LV", "RA", "RV"}:
            p.update(emin_mmHg_per_mL=1., emax_mmHg_per_mL=2. if name == "LV" else 1., activation_start_s=0., activation_duration_s=0.3)
        else:
            p["compliance_mL_per_mmHg"] = 1.
        compartments[name] = p
    template = {"subject_id": "P1", "backend": "native-four-chamber-v1", "compartments": compartments, "cycle_length_s": 1.,
        "initial_volumes_mL": [80.] * 8, "edge_resistance_mmHg_s_per_mL": [1.] * 8,
        "time_s": np.linspace(0, 0.4, 41).tolist(), "solver": {"rtol": 1e-7, "atol_mL": 1e-8, "max_step_s": 0.005}, "output_dir": str(tmp_path / "truth")}
    truth = simulate_closed_loop(template)
    case = {"patient_id": "P1", "encounters": [{"encounter_id": "baseline"}], "acquisitions": [], "evidence": []}
    targets = []
    for name, chamber, times, role in [("fit", "LV", [0.1, 0.2, 0.3], "calibration"), ("hold", "SA", [0.15, 0.25, 0.35], "validation")]:
        values = np.interp(times, truth["time_s"], truth["pressure_mmHg"][chamber]).tolist()
        raw = json.dumps({"time_s": times, "values": values, "unit": "mmHg"}).encode()
        path = tmp_path / (name + ".json")
        path.write_bytes(raw)
        case["acquisitions"].append({"acquisition_id": name, "subject_id": "P1", "encounter_id": "baseline", "modality": "pressure",
            "source_files": [{"file_id": name, "uri": path.as_uri(), "sha256": hashlib.sha256(raw).hexdigest()}]})
        case["evidence"].append({"evidence_id": name, "subject_id": "P1", "acquisition_id": name, "encounter_id": "baseline",
            "role": role, "data_kind": "pressure", "withheld_from_fit": role == "validation"})
        targets.append({"evidence_id": name, "source_file_id": name, "model_output": "pressure_mmHg." + chamber,
            "unit": "mmHg", "prediction_unit": "mmHg", "model_time": "time_s", "observation_kind": "curve", "noise_sd": 1., "max_abs_error": 3.})
    template = deepcopy(template)
    template["compartments"]["LV"]["emax_mmHg_per_mL"] = 1.8
    return {"case": case, "case_revision": 1, "case_fingerprint": "a" * 64, "encounter_id": "baseline",
        "domain": "circulation", "native_capability": "patient.circulation.simulate", "forward_template": template,
        "parameter_paths": {"emax": "compartments.LV.emax_mmHg_per_mL"}, "parameter_bounds": {"emax": [1.5, 2.5]},
        "parameter_units": {"emax": "mmHg/mL"}, "targets": targets, "sampler_settings": {"population_size": 6, "generations": 2}}


def test_real_native_circulation_inverse_and_fresh_holdout_replay(tmp_path):
    pytest.importorskip("scipy")
    pytest.importorskip("cardiinfer")
    from hearttwin.config import load_registry

    p = native_calibration_request(tmp_path)
    result = execute_patient_domain(p, load_registry(), output_dir=tmp_path / "calibration")
    assert result["heldout_acceptance_passed"] is True
    assert result["inference"]["backend"] == "native-map-de-v1"
    assert (tmp_path / "calibration/prediction.json").is_file()
    assert len(result["prediction"]["pressure_mmHg"]["LV"]) == 41
    inference = (tmp_path / "calibration/inference-request.json").read_text()
    assert "hold.json" not in inference


@pytest.mark.parametrize("mutation", ["unit", "geometry", "encounter", "domain"])
def test_native_calibration_rejects_unbound_or_wrong_unit_parameters(tmp_path, mutation):
    pytest.importorskip("scipy")
    p = native_calibration_request(tmp_path)
    if mutation == "unit":
        p["parameter_units"]["emax"] = "Pa"
    elif mutation == "geometry":
        p["parameter_paths"]["emax"] = "time_s.0"
    elif mutation == "encounter":
        p["encounter_id"] = None
    else:
        p["domain"] = "mechanics"
    with pytest.raises(ValueError):
        validate_domain_job(PatientDomainJob.model_validate(p))
