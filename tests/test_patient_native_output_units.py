"""Regression coverage for native physical units at solver/calibration boundaries."""

import numpy as np
import pytest

from hearttwin.patient_closed_loop import hemodynamic_observables
from hearttwin.patient_domain import PatientDomainJob, validate_domain_job
from hearttwin.patient_observations import output_unit, sample_prediction
from test_patient_native_calibration import native_calibration_request


@pytest.mark.parametrize("path,unit", [
    ("pressure_mmHg.LV", "mmHg"),
    ("volume_mL.LV", "mL"),
    ("flow_mL_per_s.LV_to_SA", "mL/s"),
])
def test_native_curves_carry_authoritative_qualified_units(path, unit):
    data = np.arange(24, dtype=float).reshape(3, 8)
    prediction = hemodynamic_observables([0., .1, .2], data, data, data)
    assert output_unit(prediction, path) == unit
    spec = {"model_output": path, "unit": unit, "model_time": "time_s", "time_s": [.05, .15]}
    assert len(sample_prediction(prediction, spec)) == 2
    wrong = "mL" if unit != "mL" else "mmHg"
    with pytest.raises(ValueError, match="units"):
        sample_prediction(prediction, {**spec, "unit": wrong, "prediction_unit": wrong})


def test_qualified_units_take_precedence_over_ambiguous_legacy_leaf():
    assert output_unit({"units": {"pressure_mmHg.LV": "mmHg", "LV": "mL"}}, "pressure_mmHg.LV") == "mmHg"
    assert output_unit({"units": {"pressure": "mmHg"}}, "series.pressure") == "mmHg"


@pytest.mark.parametrize("path,wrong", [
    ("pressure_mmHg.LV", "mL"),
    ("volume_mL.LV", "mmHg"),
    ("flow_mL_per_s.LV_to_SA", "mL"),
])
def test_native_calibration_rejects_dimensionally_wrong_output_before_inference(tmp_path, path, wrong):
    pytest.importorskip("scipy")
    request = native_calibration_request(tmp_path)
    request["targets"][0].update(model_output=path, unit=wrong, prediction_unit=wrong)
    with pytest.raises(ValueError, match="output and observation units"):
        validate_domain_job(PatientDomainJob.model_validate(request))
