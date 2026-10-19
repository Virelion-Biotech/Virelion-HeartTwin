from __future__ import annotations

import pytest
from pydantic import ValidationError

from hearttwin.ep_parameter_mapping import (
    EPParameterMappingSpec,
    apply_ep_parameter_mapping,
)


def _activation_only_mapping() -> EPParameterMappingSpec:
    names = [
        "fibre_speed",
        "sheet_speed",
        "normal_speed",
        "endo_dense_speed",
        "endo_sparse_speed",
        "purkinje_speed",
        "r0",
        "r1",
    ]
    return EPParameterMappingSpec(
        mapping_id="camps-to-opencarp-prescribed-activation-v1",
        mapping_version="1.0.0",
        source_backend="camps-cdt-external-v1",
        target_backend="opencarp-external-v1",
        rules=[],
        ignored_source_parameters=[
            {
                "source_parameter": name,
                "reason": (
                    "The v1 openCARP profile transfers the calibrated activation field "
                    "rather than reinterpreting this scalar parameter."
                ),
            }
            for name in names
        ],
        intended_use=(
            "Prescribed-activation forward ECG confirmation with no scalar parameter replay."
        ),
        evidence_reference="Virelion prescribed-activation LF12 profile v1.0.0",
    )


def test_activation_only_mapping_can_explicitly_ignore_all_source_scalars() -> None:
    mapping = _activation_only_mapping()
    values = {
        "fibre_speed": 0.065,
        "sheet_speed": 0.04,
        "normal_speed": 0.048,
        "endo_dense_speed": 0.12,
        "endo_sparse_speed": 0.08,
        "purkinje_speed": 0.3,
        "r0": 1.0,
        "r1": 0.0,
    }
    units = {name: ("cm/ms" if "speed" in name else "1") for name in values}
    result = apply_ep_parameter_mapping(
        mapping,
        source_backend="camps-cdt-external-v1",
        target_backend="opencarp-external-v1",
        values=values,
        units=units,
    )
    assert result.values == {}
    assert result.units == {}
    assert result.mapped_source_parameters == []
    assert result.ignored_source_parameters == sorted(values)
    assert len(result.mapping_fingerprint) == 64
    assert len(result.source_parameter_fingerprint) == 64


def test_activation_only_mapping_still_rejects_unaccounted_source_parameter() -> None:
    mapping = _activation_only_mapping()
    with pytest.raises(ValueError, match="does not account"):
        apply_ep_parameter_mapping(
            mapping,
            source_backend="camps-cdt-external-v1",
            target_backend="opencarp-external-v1",
            values={"fibre_speed": 0.065, "unexpected": 1.0},
            units={"fibre_speed": "cm/ms", "unexpected": "1"},
        )


def test_mapping_cannot_be_empty_without_explicit_ignored_parameters() -> None:
    with pytest.raises(ValidationError, match="mapped or explicitly ignored"):
        EPParameterMappingSpec(
            mapping_id="empty",
            mapping_version="1.0.0",
            source_backend="camps-cdt-external-v1",
            target_backend="opencarp-external-v1",
            rules=[],
            ignored_source_parameters=[],
            intended_use="This invalid mapping contains no declared transfer semantics.",
        )
