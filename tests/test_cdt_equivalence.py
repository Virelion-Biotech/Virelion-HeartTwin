from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hearttwin.cardiac_twin import CardiacDigitalTwin, ConductionNetwork, EPParameters, MeshGeometry


PINNED_COMMIT = "816d51fab0837cfe9e20c7d3a318429e9acf0733"


def _fixture_geometry() -> tuple[MeshGeometry, ConductionNetwork]:
    xyz = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    tet = np.array([[0, 1, 1, 2]], dtype=int)
    edges = np.array([[0, 1], [1, 2]], dtype=int)
    basis = np.repeat(np.eye(3)[None, :, :], 2, axis=0)
    geometry = MeshGeometry(xyz, tet, edge_nodes=edges, edge_fibre_sheet_normal=basis)
    conduction = ConductionNetwork((0,), root_activation_ms={0: 0.0})
    return geometry, conduction


def test_native_forward_is_deterministic() -> None:
    geometry, conduction = _fixture_geometry()
    twin = CardiacDigitalTwin(
        geometry,
        conduction,
        EPParameters(
            fibre_speed=1.0,
            sheet_speed=1.0,
            normal_speed=1.0,
            purkinje_speed=1.0,
            endo_dense_speed=1.0,
            endo_sparse_speed=1.0,
        ),
        legacy_output=True,
    )
    first = twin.simulate(with_ecg=False).activation
    second = twin.simulate(with_ecg=False).activation
    np.testing.assert_array_equal(first, second)
    np.testing.assert_array_equal(first, np.array([1, 1001, 2001]))


def test_pinned_reference_metadata_is_explicit() -> None:
    manifest = Path("configs/cdt-reference.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["upstream"]["commit"] == PINNED_COMMIT
    assert payload["data"]["doi"] == "10.5281/zenodo.14034739"


def test_real_fixture_comparison_when_present() -> None:
    reference = Path(".cdt_reference/reference.npz")
    native = Path(".cdt_reference/native.npz")
    if not (reference.exists() and native.exists()):
        pytest.skip("Generated CDT fixture not present")
    from scripts.compare_cdt_lat import main as compare_main

    # Run the comparator through its public CLI contract only when the fixture exists.
    import sys

    previous = sys.argv
    try:
        sys.argv = [
            "compare_cdt_lat",
            "--reference", str(reference),
            "--native", str(native),
            "--output", ".cdt_reference/test-compare.json",
            "--max-abs-tol", "0",
            "--rmse-tol", "0",
            "--mismatch-fraction-tol", "0",
        ]
        compare_main()
    finally:
        sys.argv = previous
