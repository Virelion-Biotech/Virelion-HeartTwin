from __future__ import annotations

import pytest

from hearttwin.ep_parameter_mapping import (
    EPParameterMappingSpec,
    EPParameterMapRule,
    IgnoredEPParameter,
    apply_ep_parameter_mapping,
)


def _mapping() -> EPParameterMappingSpec:
    return EPParameterMappingSpec(
        mapping_id="fast-to-confirmation-v1",
        mapping_version="1.0.0",
        source_backend="fast-v1",
        target_backend="confirmation-v1",
        intended_use="Synthetic contract test for explicit EP parameter replay.",
        rules=[
            EPParameterMapRule(
                source_parameter="speed_cm_per_ms",
                target_parameter="speed_m_per_s",
                source_unit="cm/ms",
                target_unit="m/s",
                scale=10.0,
                rationale="Convert centimetres per millisecond to metres per second.",
            ),
            EPParameterMapRule(
                source_parameter="activation_onset_ms",
                target_parameter="stimulus_offset_ms",
                source_unit="ms",
                target_unit="ms",
                scale=1.0,
                offset=0.0,
                rationale="Same temporal origin in this synthetic mapping contract.",
            ),
        ],
        ignored_source_parameters=[
            IgnoredEPParameter(
                source_parameter="fit_only_nuisance",
                reason="Calibration-only nuisance parameter has no target solver analogue.",
            )
        ],
    )


def test_parameter_mapping_is_explicit_and_auditable() -> None:
    spec = _mapping()
    result = apply_ep_parameter_mapping(
        spec,
        source_backend="fast-v1",
        target_backend="confirmation-v1",
        values={
            "speed_cm_per_ms": 0.06,
            "activation_onset_ms": 12.0,
            "fit_only_nuisance": 0.4,
        },
        units={
            "speed_cm_per_ms": "cm/ms",
            "activation_onset_ms": "ms",
            "fit_only_nuisance": "1",
        },
    )
    assert result.values == {
        "speed_m_per_s": pytest.approx(0.6),
        "stimulus_offset_ms": pytest.approx(12.0),
    }
    assert result.units == {"speed_m_per_s": "m/s", "stimulus_offset_ms": "ms"}
    assert result.ignored_source_parameters == ["fit_only_nuisance"]
    assert len(result.mapping_fingerprint) == 64
    assert len(result.source_parameter_fingerprint) == 64


def test_parameter_mapping_rejects_unaccounted_source_parameters() -> None:
    with pytest.raises(ValueError, match="does not account"):
        apply_ep_parameter_mapping(
            _mapping(),
            source_backend="fast-v1",
            target_backend="confirmation-v1",
            values={
                "speed_cm_per_ms": 0.06,
                "activation_onset_ms": 12.0,
                "unexpected": 7.0,
            },
            units={
                "speed_cm_per_ms": "cm/ms",
                "activation_onset_ms": "ms",
                "unexpected": "1",
            },
        )


def test_parameter_mapping_rejects_unit_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match mapping unit"):
        apply_ep_parameter_mapping(
            _mapping(),
            source_backend="fast-v1",
            target_backend="confirmation-v1",
            values={"speed_cm_per_ms": 0.06, "activation_onset_ms": 12.0},
            units={"speed_cm_per_ms": "m/s", "activation_onset_ms": "ms"},
        )


def test_parameter_mapping_rejects_backend_mismatch() -> None:
    with pytest.raises(ValueError, match="expects source backend"):
        apply_ep_parameter_mapping(
            _mapping(),
            source_backend="wrong-fast-backend",
            target_backend="confirmation-v1",
            values={"speed_cm_per_ms": 0.06, "activation_onset_ms": 12.0},
            units={"speed_cm_per_ms": "cm/ms", "activation_onset_ms": "ms"},
        )


def test_mapping_contract_rejects_mapped_and_ignored_overlap() -> None:
    with pytest.raises(ValueError, match="both mapped and ignored"):
        EPParameterMappingSpec(
            mapping_id="bad",
            mapping_version="1",
            source_backend="a",
            target_backend="b",
            intended_use="Deliberately invalid mapping contract for testing.",
            rules=[
                EPParameterMapRule(
                    source_parameter="x",
                    target_parameter="y",
                    source_unit="1",
                    target_unit="1",
                    scale=1.0,
                    rationale="Synthetic identity relation for invalid overlap test.",
                )
            ],
            ignored_source_parameters=[
                IgnoredEPParameter(
                    source_parameter="x",
                    reason="Deliberate overlap for contract validation test.",
                )
            ],
        )


def test_mapping_contract_rejects_duplicate_target_parameters() -> None:
    with pytest.raises(ValueError, match="target parameter"):
        EPParameterMappingSpec(
            mapping_id="bad-target",
            mapping_version="1",
            source_backend="a",
            target_backend="b",
            intended_use="Deliberately invalid duplicate-target contract for testing.",
            rules=[
                EPParameterMapRule(
                    source_parameter="x1",
                    target_parameter="y",
                    source_unit="1",
                    target_unit="1",
                    scale=1.0,
                    rationale="Synthetic first mapping relation for duplicate target test.",
                ),
                EPParameterMapRule(
                    source_parameter="x2",
                    target_parameter="y",
                    source_unit="1",
                    target_unit="1",
                    scale=1.0,
                    rationale="Synthetic second mapping relation for duplicate target test.",
                ),
            ],
        )
