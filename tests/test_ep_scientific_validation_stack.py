import numpy as np
import pytest

cardiep = pytest.importorskip("cardiep")
cardiinfer = pytest.importorskip("cardiinfer")


def test_cardiep_niederer_contract_is_available_through_stack_pin() -> None:
    spec = cardiep.niederer_2011_spec()
    reference = cardiep.fenicsx_beat_niederer_reference()

    assert spec["benchmark_id"] == "niederer-2011"
    assert reference.benchmark_id == "niederer-2011"
    assert reference.sample_ids == tuple(f"P{i}" for i in range(1, 10))

    report = cardiep.compare_activation_profiles(
        reference,
        cardiep.ActivationProfile.from_dict(reference.to_dict()),
        thresholds=cardiep.AgreementThresholds(
            rmse_ms_max=1e-12,
            max_abs_ms_max=1e-12,
            correlation_min=0.999999999999,
            abs_bias_ms_max=1e-12,
        ),
    )
    assert report["status"] == "pass"
    assert report["metrics"]["rmse_ms"] == pytest.approx(0.0)

    refinement = cardiep.run_eikonal_refinement_validation()
    assert refinement["passed"] is True
    assert refinement["validation_status"] == "numerical_refinement_check"
    assert refinement["metrics"]["monotone_error_reduction"] is True


def test_cardiinfer_recovery_calibration_primitives_are_available() -> None:
    assert cardiinfer.posterior_cdf_at_truth(
        1.0,
        np.asarray([0.0, 1.0, 2.0]),
        np.asarray([0.2, 0.6, 0.2]),
    ) == pytest.approx(0.5)

    summary = cardiinfer.summarize_recovery_trials(
        [
            {
                "success": True,
                "parameters": {
                    "fibre_speed": {
                        "truth": 0.1,
                        "mean": 0.1,
                        "median": 0.1,
                        "q025": 0.08,
                        "q975": 0.12,
                        "posterior_cdf_at_truth": 0.5,
                    }
                },
            }
        ],
        gates={
            "max_failure_rate": 0.0,
            "parameters": {
                "fibre_speed": {
                    "rmse_max": 1e-12,
                    "abs_bias_max": 1e-12,
                    "coverage_95_min": 1.0,
                    "cdf_ks_max": 0.5,
                }
            },
        },
    )
    assert summary["status"] == "pass"
