"""Nonlinear chamber law and structural activation-screen regressions."""
from copy import deepcopy

import numpy as np
import pytest

from hearttwin.patient_closed_loop import chamber_pressure, validate_activation_support


def chamber():
    return {"unstressed_volume_mL": 10., "external_pressure_mmHg": 2.,
            "emin_mmHg_per_mL": .05, "emax_mmHg_per_mL": 2.,
            "activation_start_s": 0., "activation_duration_s": .4}


def test_exponential_law_linear_limit_and_peak_endpoints():
    p = chamber()
    for volume in [0., 10., 100., 220.]:
        for t in [0., .1, .2, .4]:
            assert chamber_pressure(t, volume, {**p, "passive_exponent_per_mL": 1e-10}, 1.) == pytest.approx(chamber_pressure(t, volume, p, 1.), rel=2e-7, abs=1e-8)
    nonlinear = {**p, "passive_exponent_per_mL": .01, "activation_peak_fraction": .3}
    assert chamber_pressure(.12, 100., nonlinear, 1.) == pytest.approx(182.)
    assert chamber_pressure(.4, 100., nonlinear, 1.) == pytest.approx(2.+5.*np.expm1(.9))
    values = [chamber_pressure(0., v, nonlinear, 1.) for v in [10., 100., 150., 200.]]
    assert np.all(np.diff(values) > 0)
    assert (values[3]-values[2])/50 > (values[2]-values[1])/50


def test_asymmetric_half_peak_matches_original_activation():
    p = chamber()
    for t in np.linspace(0., 1., 31):
        assert chamber_pressure(t, 100., {**p, "activation_peak_fraction": .5}, 1.) == pytest.approx(chamber_pressure(t, 100., p, 1.))


def test_inactive_emax_is_rejected_but_wrapped_activation_is_present():
    p = {**chamber(), "activation_start_s": .8, "activation_duration_s": .15}
    template = {"cycle_length_s": 1., "compartments": {"LA": p}}
    paths = {"atrial_strength": "compartments.LA.emax_mmHg_per_mL"}
    with pytest.raises(ValueError, match="Structurally inactive"):
        validate_activation_support(template, paths, .7)
    assert "activation_present" in validate_activation_support(template, paths, .9)["atrial_strength"]
    wrapped = deepcopy(template)
    wrapped["compartments"]["LA"]["activation_duration_s"] = .3
    assert "activation_present" in validate_activation_support(wrapped, paths, .05)["atrial_strength"]
    assert "activation_present" in validate_activation_support(wrapped, paths, 0.)["atrial_strength"]
    # Moving the activation time is itself a fitted parameter, so a zero at the
    # initial template is insufficient proof of inactivity over its whole bounds.
    paths["start"] = "compartments.LA.activation_start_s"
    assert validate_activation_support(template, paths, .7)["atrial_strength"] == "timing_is_fitted_not_screened"


def test_optional_native_parameters_have_explicit_units():
    from hearttwin.patient_domain import native_parameter_unit
    capability = "patient.circulation.simulate"
    assert native_parameter_unit(capability, "compartments.LV.passive_exponent_per_mL") == "1/mL"
    assert native_parameter_unit(capability, "compartments.LV.activation_peak_fraction") == "1"


def test_inactive_strength_rejected_before_inference_without_opening_holdout(tmp_path, monkeypatch):
    pytest.importorskip("scipy")
    from test_patient_native_calibration import native_calibration_request
    import hearttwin.patient_domain as domain
    request = native_calibration_request(tmp_path)
    request["forward_template"]["compartments"]["LA"]["activation_start_s"] = .6
    request["forward_template"]["compartments"]["LA"]["activation_duration_s"] = .2
    request["parameter_paths"] = {"atrial": "compartments.LA.emax_mmHg_per_mL"}
    request["parameter_bounds"] = {"atrial": [1., 2.]}
    request["parameter_units"] = {"atrial": "mmHg/mL"}
    (tmp_path / "hold.json").unlink()
    def forbidden_inference(*args, **kwargs):
        raise AssertionError("Inference must not run for a provably inactive parameter")
    monkeypatch.setattr(domain, "invoke", forbidden_inference)
    with pytest.raises(ValueError, match="Structurally inactive"):
        domain.execute_patient_domain(request, object(), output_dir=tmp_path/"run")


def test_nonlinear_parameter_real_native_inverse_and_replay(tmp_path):
    pytest.importorskip("scipy")
    pytest.importorskip("cardiinfer")
    import hashlib
    import json
    from test_patient_native_calibration import native_calibration_request
    from hearttwin.config import load_registry
    from hearttwin.patient_closed_loop import simulate_closed_loop
    from hearttwin.patient_domain import execute_patient_domain
    request = native_calibration_request(tmp_path)
    truth_request = deepcopy(request["forward_template"])
    truth_request["compartments"]["LV"]["passive_exponent_per_mL"] = .01
    truth = simulate_closed_loop(truth_request)
    for target, chamber_name, times in [
        (request["targets"][0], "LV", [.1, .2, .3]),
        (request["targets"][1], "SA", [.15, .25, .35]),
    ]:
        name = target["evidence_id"]
        raw = json.dumps({"time_s": times, "values": np.interp(times, truth["time_s"], truth["pressure_mmHg"][chamber_name]).tolist(), "unit": "mmHg"}).encode()
        (tmp_path/(name+".json")).write_bytes(raw)
        acquisition = next(a for a in request["case"]["acquisitions"] if a["acquisition_id"] == name)
        acquisition["source_files"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    request["forward_template"]["compartments"]["LV"]["passive_exponent_per_mL"] = .008
    request["parameter_paths"] = {"k": "compartments.LV.passive_exponent_per_mL"}
    request["parameter_bounds"] = {"k": [.005, .015]}
    request["parameter_units"] = {"k": "1/mL"}
    result = execute_patient_domain(request, load_registry(), output_dir=tmp_path/"nonlinear-inverse")
    assert result["inference"]["backend"] == "native-map-de-v1"
    assert result["heldout_acceptance_passed"] is True
    assert (tmp_path/"nonlinear-inverse/activation-support.json").is_file()
