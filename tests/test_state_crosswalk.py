import pytest
from hearttwin.state_crosswalk import (
    CROSSWALK_VERSION, CrosswalkError, cardisim_state_to_cardivex_domains,
    cardisim_summary_to_cardivex,
)

def _state(**overrides):
    state = {
        "contractility": 0.8, "electrophysiology": 0.75, "metabolism": 0.7,
        "mitochondrial_health": 0.65, "viability": 0.9, "inflammation": 0.2,
        "fibrosis": 0.1, "oxidative_stress": 0.3, "angiogenesis": 0.5,
        "hypertrophy": 0.4,
    }
    state.update(overrides)
    return state

def test_crosswalk_uses_only_direct_identity_or_inverse_semantics():
    out = cardisim_state_to_cardivex_domains(_state())
    assert out["contractile_impairment"] == pytest.approx(0.2)
    assert out["electrophysiologic_disturbance"] == pytest.approx(0.25)
    assert out["metabolic_stress"] == pytest.approx(0.3)
    assert out["mitochondrial_dysfunction"] == pytest.approx(0.35)
    assert out["viability_burden"] == pytest.approx(0.1)
    assert out["inflammatory_activation"] == pytest.approx(0.2)
    assert out["fibrosis_remodeling"] == pytest.approx(0.1)
    assert out["oxidative_stress"] == pytest.approx(0.3)
    assert "endothelial_vascular_dysfunction" not in out
    assert "structural_disorganization" not in out
    assert CROSSWALK_VERSION == "0.1.0"

def test_summary_crosswalk_preserves_initial_and_final_separately():
    before, after, changes = cardisim_summary_to_cardivex(
        {"initial": _state(), "final": _state(contractility=0.4, inflammation=0.7)}
    )
    assert before["contractile_impairment"] == pytest.approx(0.2)
    assert after["contractile_impairment"] == pytest.approx(0.6)
    assert changes["contractile_impairment"] == pytest.approx(0.4)
    assert changes["inflammatory_activation"] == pytest.approx(0.5)

def test_crosswalk_rejects_missing_or_out_of_bounds_inputs():
    state = _state()
    state.pop("viability")
    with pytest.raises(CrosswalkError, match="missing crosswalk inputs"):
        cardisim_state_to_cardivex_domains(state)
    with pytest.raises(CrosswalkError, match=r"must be in \[0, 1\]"):
        cardisim_state_to_cardivex_domains(_state(fibrosis=1.2))
