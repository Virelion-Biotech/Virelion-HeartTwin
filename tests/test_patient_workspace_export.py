"""Patient export rejects unreviewed, incomplete and mismatched geometry."""

import hashlib
import json
from types import SimpleNamespace
import pytest


def payload(tmp_path):
    fields = {
        "node_xyz": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "tetra": [[0, 1, 2, 3]],
        "lvendo_nodes": [0, 1],
        "rvendo_nodes": [2, 3],
        "fibre": [[1, 0, 0]] * 4,
        "sheet": [[0, 1, 0]] * 4,
        "normal": [[0, 0, 1]] * 4,
        "material": [1],
        "vc_ab_cut": [0, 0.3, 0.6, 1],
        "vc_rt": [0, 0.3, 0.6, 1],
        "electrode_xyz": [[i + 2, 2, 2] for i in range(10)],
    }
    path = tmp_path / "volume.json"
    path.write_text(json.dumps({"fields": fields}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "subject_id": "P1",
        "source_bundle_fingerprint": "a" * 64,
        "coordinate_unit": "mm",
        "coordinate_frame": "model",
        "index_base": 0,
        "electrode_order": ["LA", "RA", "LL", "RL", "V1", "V2", "V3", "V4", "V5", "V6"],
        "fields": fields,
        "geometry_source": {"uri": path.as_uri(), "sha256": digest},
        "anatomy_bundle": {
            "subject_id": "P1",
            "study_id": "S1",
            "acquisition_id": "mri1",
            "bundle_fingerprint": "a" * 64,
            "artifacts": [
                {
                    "artifact_id": "volume",
                    "kind": "volume_mesh",
                    "uri": path.as_uri(),
                    "sha256": digest,
                    "frame_id": "model",
                }
            ],
        },
    }


def registry():
    return SimpleNamespace(
        capability=lambda _: SimpleNamespace(
            available=lambda: True,
            invoke=lambda *args: {"bundle_fingerprint": "a" * 64},
        )
    )


def test_export_complete_and_preserves_upstream_electrode_order(tmp_path):
    pytest.importorskip("cardiep")
    from hearttwin.patient_workspace import prepare_patient_workspace

    result = prepare_patient_workspace(
        payload(tmp_path), registry=registry(), output_dir=tmp_path / "workspace"
    )
    assert result["camps_ready"] is True and result["opencarp_ready"] is False
    manifest = json.loads((tmp_path / "workspace/camps-manifest.json").read_text())
    assert manifest["coordinate_unit"] == "mm"
    assert (tmp_path / "workspace/camps-staging.json").exists()


@pytest.mark.parametrize("problem", ["electrode_order", "index", "source", "frame"])
def test_export_rejects_wrong_semantics(tmp_path, problem):
    pytest.importorskip("cardiep")
    from hearttwin.patient_workspace import prepare_patient_workspace

    item = payload(tmp_path)
    if problem == "electrode_order":
        item["electrode_order"][0] = "RA"
    if problem == "index":
        item["fields"]["tetra"][0][0] = 999
    if problem == "source":
        item["geometry_source"]["sha256"] = "b" * 64
    if problem == "frame":
        item["coordinate_frame"] = "another"
    with pytest.raises(ValueError):
        prepare_patient_workspace(
            item, registry=registry(), output_dir=tmp_path / "workspace"
        )
    assert not (tmp_path / "workspace").exists()
