"""Prepare complete, explicitly supplied patient geometry for Camps.

Never infers chamber tags, ventricular coordinates, fibres or electrode locations.
Requires reviewed anatomy and a matching canonical bundle fingerprint first.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import shutil

import numpy as np

from .patient_execution import write_stage

KINDS = {
    "node_xyz": "node_xyz",
    "tetra": "tetra_connectivity",
    "lvendo_nodes": "lvendo_nodes",
    "rvendo_nodes": "rvendo_nodes",
    "fibre": "fibre_field",
    "sheet": "sheet_field",
    "normal": "normal_field",
    "material": "tetra_material",
    "vc_ab_cut": "ventricular_coordinate_ab_cut",
    "vc_rt": "ventricular_coordinate_rt",
    "electrode_xyz": "electrode_xyz",
}


def prepare_patient_workspace(payload, *, registry, output_dir):
    from cardiep.camps_workspace import CampsWorkspaceManifest
    from cardiep.camps_staging import materialize_camps_geometry_workspace
    from .contracts import AnatomyBundlePayload

    bundle = AnatomyBundlePayload.model_validate(payload["anatomy_bundle"])
    subject = payload["subject_id"]
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", subject)
        or bundle.subject_id != subject
    ):
        raise ValueError("Workspace subject must be a safe identifier matching anatomy")
    adapter = registry.capability("anatomy.validate")
    if adapter is None or not adapter.available():
        raise RuntimeError("Patient workspace preparation requires CardiAnatomy")
    ready = adapter.invoke(
        "anatomy.validate",
        {"target": "patient_ep", "bundle": bundle.model_dump(mode="json")},
    )
    fingerprint = ready.get("bundle_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", fingerprint
    ):
        raise ValueError("Anatomy readiness did not return a canonical fingerprint")
    if bundle.bundle_fingerprint not in {None, fingerprint}:
        raise ValueError("Anatomy bundle fingerprint changed during readiness")
    if payload.get("source_bundle_fingerprint") != fingerprint:
        raise ValueError("Geometry source fingerprint differs from reviewed anatomy")
    if payload.get("coordinate_unit") not in {"mm", "cm", "m"} or not payload.get(
        "coordinate_frame"
    ):
        raise ValueError("Explicit coordinate frame and physical units are required")
    if payload.get("index_base") not in {0, 1}:
        raise ValueError("Explicit index_base 0 or 1 is required")
    fields = payload["fields"]
    if set(fields) != set(KINDS):
        raise ValueError("Workspace requires exactly these fields: " + ", ".join(KINDS))
    arrays = {name: np.asarray(value) for name, value in fields.items()}
    if any(not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError("Workspace fields must be finite")
    xyz, tetra = arrays["node_xyz"], arrays["tetra"]
    if xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) < 4:
        raise ValueError("node_xyz must be N x 3, with at least four nodes")
    if tetra.ndim != 2 or tetra.shape[1] != 4 or not len(tetra):
        raise ValueError("tetra must be nonempty M x 4")
    base = payload["index_base"]
    for name in ("tetra", "lvendo_nodes", "rvendo_nodes", "material"):
        a = arrays[name]
        if not np.equal(a, np.floor(a)).all():
            raise ValueError(f"{name} must contain integer values")
    for name in ("tetra", "lvendo_nodes", "rvendo_nodes"):
        a = arrays[name]
        if a.size == 0 or a.min() < base or a.max() >= len(xyz) + base:
            raise ValueError(f"{name} indices are outside node range")
    for name in ("fibre", "sheet", "normal"):
        a = arrays[name]
        if a.shape != xyz.shape or np.any(np.linalg.norm(a, axis=1) < 1e-12):
            raise ValueError(f"{name} must be a nonzero N x 3 field")
    if arrays["material"].reshape(-1).shape != (len(tetra),):
        raise ValueError("material must contain one label per tetrahedron")
    for name in ("vc_ab_cut", "vc_rt"):
        if arrays[name].reshape(-1).shape != (len(xyz),):
            raise ValueError(f"{name} must contain one value per node")
    electrodes = arrays["electrode_xyz"]
    if electrodes.ndim != 2 or electrodes.shape[1] != 3 or len(electrodes) != 10:
        raise ValueError(
            "Camps workspace requires exactly ten ordered electrode coordinates"
        )
    if payload.get("electrode_order") != [
        "LA",
        "RA",
        "LL",
        "RL",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V6",
    ]:
        raise ValueError(
            "Declare electrode_order LA, RA, LL, RL, V1..V6; reorder explicitly before preparation"
        )
    # Input arrays must be derived from the selected reviewed mesh, not merely labelled with its hash.
    source = payload.get("geometry_source")
    if (
        not isinstance(source, dict)
        or not source.get("uri")
        or not source.get("sha256")
    ):
        raise ValueError(
            "Geometry arrays require a hash-pinned geometry_source artifact"
        )
    from .patient_ecg_artifacts import _local_path

    path = _local_path(source["uri"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Geometry source hash mismatch")
    meshes = [item for item in bundle.artifacts if item.get("kind") == "volume_mesh"]
    selected = [
        item
        for item in meshes
        if item.get("metadata", {}).get("selected_for_patient_model") is True
    ]
    selected = selected if selected else meshes
    if len(selected) != 1 or selected[0].get("sha256") != source["sha256"]:
        raise ValueError("Geometry source must be the selected reviewed volume mesh")
    if selected[0].get("frame_id") != payload["coordinate_frame"]:
        raise ValueError("Export coordinate frame differs from selected volume mesh")
    original = json.loads(raw)
    if "fields" not in original or any(
        not np.array_equal(np.asarray(original["fields"].get(name)), a)
        for name, a in arrays.items()
    ):
        raise ValueError("Export arrays do not match the pinned geometry source fields")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError(
            "Workspace output must be a new directory; existing runs are immutable"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=destination.name + "-", dir=destination.parent)
    )
    try:
        source_dir = temporary / "sources"
        source_dir.mkdir()
        refs = {}
        for name, kind in KINDS.items():
            path = source_dir / (name + ".csv")
            np.savetxt(
                path,
                arrays[name],
                delimiter=",",
                fmt="%d"
                if name in {"tetra", "lvendo_nodes", "rvendo_nodes", "material"}
                else "%.17g",
            )
            refs[name] = {
                "artifact_id": subject + "-" + name,
                "kind": kind,
                "uri": (destination / "sources" / path.name).as_uri(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "coordinate_frame": payload["coordinate_frame"],
            }
        manifest = CampsWorkspaceManifest(
            subject_id=subject,
            source_bundle_fingerprint=fingerprint,
            coordinate_frame=payload["coordinate_frame"],
            coordinate_unit=payload["coordinate_unit"],
            index_base=base,
            **refs,
        )
        write_stage(temporary / "camps-manifest.json", manifest.model_dump(mode="json"))
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    staged = materialize_camps_geometry_workspace(
        manifest, staging_root=destination / "camps"
    )
    write_stage(destination / "camps-staging.json", staged.model_dump(mode="json"))
    report = {
        "subject_id": subject,
        "anatomy_bundle_fingerprint": fingerprint,
        "workspace_manifest_fingerprint": manifest.fingerprint,
        "camps_ready": True,
        "opencarp_ready": False,
        "remaining": [
            "Supply a separately generated and verified openCARP heart/torso/electrode workspace and lead fields",
            "Qualify runtime, mesh/time convergence and measured held-out prediction",
        ],
        "scientific_qualification": "not_established",
    }
    write_stage(destination / "readiness.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Prepare a reviewed patient Camps geometry workspace"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    from .config import load_registry

    result = prepare_patient_workspace(
        json.loads(args.input.read_text()),
        registry=load_registry(),
        output_dir=args.output,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
