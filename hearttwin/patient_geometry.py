"""Voxel-conforming tetrahedra from reviewed label images, with physical coordinates."""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .patient_observations import verified_bytes


def write_artifact(root, name, payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (name + "-" + digest + ".json")
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Content-addressed artifact is corrupt")
    path.write_bytes(raw)
    return {"uri": path.as_uri(), "sha256": digest, "media_type": "application/json"}


def reconstruct_labels(payload):
    """Freudenthal six-tet subdivision; no segmentation, smoothing or fibre inference."""
    source = payload["source"]
    raw = verified_bytes(source)
    review = payload.get("review", {})
    if not review.get("reviewer") or not review.get("rationale") or review.get("source_sha256") != source["sha256"]:
        raise ValueError("Reviewed segmentation must be bound to the exact source SHA-256")
    if payload["format"] == "json-labels-v1":
        image = json.loads(raw)
        labels = np.asarray(image["labels"])
        affine = np.asarray(image["voxel_corner_affine_m"], dtype=float)
    elif payload["format"] == "nifti-labels":
        import gzip
        import nibabel as nib

        image = nib.Nifti1Image.from_bytes(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        labels = np.asanyarray(image.dataobj)
        if image.header.get_xyzt_units()[0] != "mm" or payload["coordinate_frame"] != "NIFTI_RAS":
            raise ValueError("NIfTI requires explicit RAS coordinates and declared mm units")
        affine = image.affine.copy()
        affine[:3, 3] -= affine[:3, :3] @ np.full(3, 0.5)
        affine[:3] *= 0.001
    else:
        raise ValueError("Supported formats: json-labels-v1 or nifti-labels")
    if (labels.ndim != 3 or not labels.size or labels.dtype.kind not in "iuf"
            or not np.isfinite(labels).all() or not np.equal(labels, np.floor(labels)).all()):
        raise ValueError("Segmentation must be a finite integer-valued 3D label image")
    if (affine.shape != (4, 4) or not np.isfinite(affine).all()
            or not np.allclose(affine[3], [0, 0, 0, 1]) or abs(np.linalg.det(affine[:3, :3])) < 1e-24):
        raise ValueError("Nonsingular physical voxel affine in metres required")
    selected = payload["tissue_labels"]
    cavity = payload["cavity_labels"]
    if (not selected or any(type(x) is not int for x in selected + cavity)
            or len(set(selected)) != len(selected) or set(selected) & set(cavity)):
        raise ValueError("Explicit distinct tissue and disjoint cavity label IDs required")
    cells = np.argwhere(np.isin(labels, selected))
    if not 1 <= len(cells) <= payload.get("max_voxels", 100000):
        raise ValueError("Selected voxel count is empty or exceeds the reconstruction limit")
    indices, corners, tets, regions, owners = {}, [], [], [], []
    for cell in cells:
        for perm in itertools.permutations(range(3)):
            corner = cell.copy()
            tet = []
            for axis in (None,) + perm:
                if axis is not None:
                    corner = corner.copy()
                    corner[axis] += 1
                key = tuple(map(int, corner))
                if key not in indices:
                    indices[key] = len(corners)
                    corners.append(key)
                tet.append(indices[key])
            tets.append(tet)
            regions.append(int(labels[tuple(cell)]))
            owners.append(cell)
    grid = np.asarray(corners)
    nodes = grid @ affine[:3, :3].T + affine[:3, 3]
    tets = np.asarray(tets)
    det = np.linalg.det((nodes[tets[:, 1:]] - nodes[tets[:, :1]]).transpose(0, 2, 1))
    for e in np.flatnonzero(det < 0):
        tets[e, [1, 2]] = tets[e, [2, 1]]
    faces = defaultdict(list)
    for eid, tet in enumerate(tets):
        for omit in range(4):
            faces[tuple(sorted(np.delete(tet, omit)))].append(eid)
    cavity_faces, exterior = [], []
    for face, adjacency in faces.items():
        if len(adjacency) != 1:
            continue
        eid = adjacency[0]
        face = list(face)
        x = nodes[face]
        normal = np.cross(x[1] - x[0], x[2] - x[0])
        if normal @ (nodes[tets[eid]].mean(axis=0) - x.mean(axis=0)) > 0:
            face[1], face[2] = face[2], face[1]
        exterior.append(face.copy())
        g = grid[face]
        for axis in range(3):
            if np.all(g[:, axis] == g[0, axis]):
                neighbor = owners[eid].copy()
                neighbor[axis] += -1 if g[0, axis] == neighbor[axis] else 1
                if np.all(neighbor >= 0) and np.all(neighbor < labels.shape) and int(labels[tuple(neighbor)]) in cavity:
                    cavity_faces.append([face[0], face[2], face[1]])
                break
    mesh = {"subject_id": payload["subject_id"], "units": "m", "nodes": nodes.tolist(),
            "tetrahedra": tets.tolist(), "cavity_faces": cavity_faces}
    artifact = write_artifact(payload["output_dir"], "tetra-mesh", mesh)
    metadata = {"method": "reviewed-voxel-freudenthal-v1", "source": source, "review": review,
                "coordinate_frame": payload["coordinate_frame"], "mesh": artifact,
                "tissue_label_per_element": regions, "exterior_faces": exterior,
                "voxel_corner_affine_m": affine.tolist(), "volume_m3": float(np.abs(det).sum() / 6),
                "limitations": ["Voxel-conforming stair-step surface", "No inferred fibres, scar or electrodes",
                                "Cavity closure and solver mesh limits require downstream QC"]}
    return {"mesh": artifact, "manifest": write_artifact(payload["output_dir"], "reconstruction", metadata)}
