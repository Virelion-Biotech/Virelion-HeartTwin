import json
import shutil

import pytest

from hearttwin.config import load_registry


def _cardimech_spec():
    registry = load_registry()
    return next(spec for spec in registry.services() if spec.name == "CardiMech")


def test_cardimech_is_registered_with_full_contract() -> None:
    spec = _cardimech_spec()
    assert spec.repository == "Virelion-Biotech/Virelion-CardiMech"
    assert spec.command == "cardimech-hearttwin"
    assert set(spec.capabilities) == {
        "mechanics.health",
        "mechanics.backends",
        "mechanics.materials",
        "mechanics.simulate",
        "mechanics.prepare_calibration",
        "mechanics.validate.reference",
        "mechanics.ecosystem",
    }


def test_cardimech_command_health_when_component_is_installed() -> None:
    if shutil.which("cardimech-hearttwin") is None:
        pytest.skip("CardiMech component package is not installed in base-only CI")
    adapter = load_registry().capability("mechanics.health")
    assert adapter is not None
    result = adapter.invoke("mechanics.health", {})
    assert result["service"] == "CardiMech"
    assert result["status"] == "ok"


def test_cardimech_reference_simulation_when_component_is_installed() -> None:
    if shutil.which("cardimech-hearttwin") is None:
        pytest.skip("CardiMech component package is not installed in base-only CI")
    adapter = load_registry().capability("mechanics.simulate")
    assert adapter is not None
    result = adapter.invoke(
        "mechanics.simulate",
        {
            "subject_id": "hearttwin-cardimech-smoke",
            "anatomy_ref": {
                "artifact_id": "reference-lv",
                "kind": "reference_geometry",
                "uri": "memory://reference-lv",
            },
            "backend": "numpy-lumped-v1",
            "parameters": {
                "passive": {"v0_ml": 10.0, "a_mmHg": 0.08, "b": 0.055},
                "active": {"emax_mmHg_per_ml": 2.1},
                "source": "fixed",
            },
            "settings": {"cycles": 4, "dt_s": 0.001, "inline_series": False},
        },
    )
    assert result["backend"] == "numpy-lumped-v1"
    assert result["qc"]["passed"] is True



def test_cardimech_calibration_executes_through_cardiinfer_and_returns_to_mechanics(
    tmp_path,
) -> None:
    required = ("cardimech-hearttwin", "cardimech-forward")
    if any(shutil.which(command) is None for command in required):
        pytest.skip("CardiMech command adapters are not installed in base-only CI")
    pytest.importorskip("cardiinfer")

    observation = tmp_path / "edv.json"
    observation.write_text(
        json.dumps({"values": [125.0]}),
        encoding="utf-8",
    )

    registry = load_registry()
    prepare = registry.capability("mechanics.prepare_calibration")
    infer = registry.capability("infer.run")
    simulate = registry.capability("mechanics.simulate")
    assert prepare is not None
    assert infer is not None
    assert simulate is not None

    subject_id = "hearttwin-cardimech-infer-loop"
    bundle = prepare.invoke(
        "mechanics.prepare_calibration",
        {
            "subject_id": subject_id,
            "anatomy_ref": {
                "artifact_id": "reference-lv",
                "kind": "reference_geometry",
                "uri": "memory://reference-lv",
            },
            "observations": [
                {
                    "observation_id": "edv",
                    "kind": "end_diastolic_volume",
                    "artifact": {
                        "artifact_id": "edv-observation",
                        "kind": "scalar",
                        "uri": observation.resolve().as_uri(),
                    },
                    "unit": "mL",
                }
            ],
            "backend": "numpy-lumped-v1",
            "parameter_bounds": {
                "passive.a_mmHg": [0.04, 0.12],
                "active.emax_mmHg_per_ml": [1.5, 2.8],
            },
            "initial_parameters": {
                "passive": {"v0_ml": 10.0, "a_mmHg": 0.08, "b": 0.055},
                "active": {"emax_mmHg_per_ml": 2.1},
                "source": "prior",
            },
            "settings": {"discrepancy": "rmse"},
        },
    )

    request = dict(bundle["cardiinfer_request"])
    request["sampler_settings"] = {
        "n_particles": 8,
        "n_generations": 1,
        "initial_oversample": 1,
        "output_dir": str(tmp_path / "cardiinfer"),
    }
    request["seed"] = 20261002

    result = infer.invoke("infer.run", request)
    assert result["backend"] == "native-abc-smc-v1"
    assert result["model_service"] == "CardiMech"
    assert result["model_capability"] == "mechanics.simulate"
    assert result["validation_status"] == "software_checked"
    assert result["provenance"]["forward_transport"] == "command"
    assert result["posterior_samples"]["kind"] == "posterior_samples"

    posterior_means = {
        item["parameter"]: item["mean"]
        for item in result["posterior"]
        if item.get("mean") is not None
    }
    assert set(posterior_means) == {
        "passive.a_mmHg",
        "active.emax_mmHg_per_ml",
    }

    replay = simulate.invoke(
        "mechanics.simulate",
        {
            "subject_id": subject_id,
            "parameters": posterior_means,
            "context": bundle["model_context"],
        },
    )
    assert replay["subject_id"] == subject_id
    assert replay["backend"] == "numpy-lumped-v1"
    assert replay["qc"]["passed"] is True
    assert replay["parameters"]["passive"]["a_mmHg"] == pytest.approx(
        posterior_means["passive.a_mmHg"]
    )
    assert replay["parameters"]["active"]["emax_mmHg_per_ml"] == pytest.approx(
        posterior_means["active.emax_mmHg_per_ml"]
    )
