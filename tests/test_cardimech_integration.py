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
