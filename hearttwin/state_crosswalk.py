"""Conservative, versioned cross-service cardiac-state transformations.

Only direct semantic identities or sign inversions are executable here.
Ambiguous biological mappings remain review-only in docs/CARDIAC_STATE_CROSSWALK_REVIEW.md.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CROSSWALK_VERSION = "0.1.0"

# CardiSim positive-health quantities that have a direct CardiVex burden inverse.
_INVERSE = {
    "contractility": "contractile_impairment",
    "electrophysiology": "electrophysiologic_disturbance",
    "metabolism": "metabolic_stress",
    "mitochondrial_health": "mitochondrial_dysfunction",
    "viability": "viability_burden",
}

# Direct burden-like quantities with matching semantics.
_IDENTITY = {
    "inflammation": "inflammatory_activation",
    "fibrosis": "fibrosis_remodeling",
    "oxidative_stress": "oxidative_stress",
}


class CrosswalkError(ValueError):
    pass


def _bounded(value: Any, name: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise CrosswalkError(f"{name} must be in [0, 1], got {number}")
    return number


def cardisim_state_to_cardivex_domains(state: Mapping[str, Any]) -> dict[str, float]:
    """Transform only scientifically direct CardiSim phenotypes into CardiVex domains."""
    required = set(_INVERSE) | set(_IDENTITY)
    missing = sorted(required - set(state))
    if missing:
        raise CrosswalkError(f"CardiSim state is missing crosswalk inputs: {missing}")

    domains: dict[str, float] = {}
    for source, target in _INVERSE.items():
        domains[target] = 1.0 - _bounded(state[source], source)
    for source, target in _IDENTITY.items():
        domains[target] = _bounded(state[source], source)
    return domains


def cardisim_summary_to_cardivex(
    summary: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Return initial domains, final domains and absolute domain changes."""
    initial_raw = summary.get("initial")
    final_raw = summary.get("final")
    if not isinstance(initial_raw, Mapping) or not isinstance(final_raw, Mapping):
        raise CrosswalkError("CardiSim summary must contain mapping-valued initial and final states")
    initial = cardisim_state_to_cardivex_domains(initial_raw)
    final = cardisim_state_to_cardivex_domains(final_raw)
    changes = {name: abs(final[name] - initial[name]) for name in final}
    return initial, final, changes
