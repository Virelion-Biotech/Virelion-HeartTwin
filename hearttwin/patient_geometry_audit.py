"""Explicit, reversible candidates for exact-coordinate mesh and frame defects.

These operations do not reconstruct anatomy, infer units, fill holes or qualify
a patient mesh. Original inputs remain unchanged and candidate review is pending.
"""

from __future__ import annotations

from collections import Counter
import math
import numpy as np


def _surface(points, faces):
    p = np.asarray(points)
    f = np.asarray(faces)
    if (p.ndim != 2 or p.shape[1] != 3 or p.dtype.kind not in "fiu"
            or not len(p) or not np.isfinite(p).all()):
        raise ValueError("Finite numeric 3D points required")
    if (f.ndim != 2 or f.shape[1] != 3 or f.dtype.kind not in "iu"
            or not len(f) or np.any(f < 0) or np.any(f >= len(p))):
        raise ValueError("Nonempty zero-based integer triangle faces required")
    return p.astype(float), f.astype(int)


def audit_surface(points, faces):
    p, f = _surface(points, faces)
    tri = p[f]
    area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    directed = Counter((int(a), int(b)) for face in f for a, b in zip(face, np.roll(face, -1)))
    edges = Counter(tuple(sorted((int(a), int(b)))) for face in f for a, b in zip(face, np.roll(face, -1)))
    edge_counts = Counter(edges.values())
    duplicate = len(f) - len({tuple(sorted(face)) for face in f})
    inconsistent = sum(count != 1 or directed[b, a] != 1 for (a, b), count in directed.items())
    return {
        "points": len(p), "triangles": len(f),
        "exact_duplicate_coordinate_vertices": len(p) - len(np.unique(p, axis=0)),
        "edge_incidence_counts": {str(k): v for k, v in sorted(edge_counts.items())},
        "edges_without_two_faces": sum(n != 2 for n in edges.values()),
        "inconsistent_directed_edges": inconsistent,
        "duplicate_indexed_triangles": duplicate,
        "zero_area_triangles": int((area2 == 0).sum()),
        "closed_oriented_nondegenerate": all(n == 2 for n in edges.values()) and inconsistent == 0 and duplicate == 0 and bool(np.all(area2 > 0)),
        "scope": "Local topology only; self-intersection, nesting, units and anatomical validity are not established",
    }


def exact_surface_candidate(points, faces):
    """Weld identical coordinates and remove only exactly zero-area faces.

    No tolerance, displacement, hole filling, orientation flip or nonzero face
    deletion is used. Keep explicit original-node and original-face index maps.
    """
    p, f = _surface(points, faces)
    _, first, inverse = np.unique(p, axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first)
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    node_map = remap[inverse]
    candidate_points = p[first[order]].copy()
    candidate_faces = node_map[f]
    tri = candidate_points[candidate_faces]
    keep = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 0
    if not np.any(keep):
        raise ValueError("Candidate would contain no nondegenerate faces")
    face_map = np.full(len(f), -1, dtype=int)
    face_map[keep] = np.arange(int(keep.sum()))
    candidate_faces = candidate_faces[keep]
    after = audit_surface(candidate_points, candidate_faces)
    return {
        "points": candidate_points.tolist(), "faces": candidate_faces.tolist(),
        "old_to_new_node": node_map.tolist(), "old_to_new_face": face_map.tolist(),
        "removed_zero_area_face_ids": np.flatnonzero(~keep).tolist(),
        "before": audit_surface(p, f), "after": after,
        "maximum_coordinate_displacement_native_units": 0.,
        "review_status": "pending",
        "solver_ready": False,
        "operations": ["exact-coordinate welding", "exactly zero-area face removal"],
        "remaining_requirements": ["expert review", "units/frame", "self-intersection/nesting checks", "conductivity/volume meshing where required"],
    }


def rounded_frame_candidate(fiber, sheet, *, max_angular_correction_rad, normal=None):
    """Explicit bounded Gram-Schmidt correction for declared rounded directions."""
    f, s = np.asarray(fiber, dtype=float), np.asarray(sheet, dtype=float)
    bound = max_angular_correction_rad
    if (isinstance(bound, bool) or not isinstance(bound, (int, float))
            or not math.isfinite(bound) or not 0 < bound < math.pi / 2):
        raise ValueError("Declare a finite positive angular correction bound below pi/2")
    if f.ndim != 2 or f.shape[1] != 3 or s.shape != f.shape or not len(f):
        raise ValueError("Aligned nonempty per-element fibre/sheet arrays required")
    fn, sn = np.linalg.norm(f, axis=1), np.linalg.norm(s, axis=1)
    if not np.isfinite(f).all() or not np.isfinite(s).all() or np.any(fn == 0) or np.any(sn == 0):
        raise ValueError("Finite nonzero fibre/sheet directions required")
    f = f / fn[:, None]
    original_sheet = s / sn[:, None]
    projection = np.sum(f * original_sheet, axis=1)
    angular_correction = np.arcsin(np.clip(np.abs(projection), 0, 1))
    if np.any(angular_correction > bound):
        raise ValueError("Fibre/sheet correction exceeds explicit angular bound")
    s = original_sheet - projection[:, None] * f
    norms = np.linalg.norm(s, axis=1)
    if np.any(norms <= np.finfo(float).eps):
        raise ValueError("Fibre and sheet are collinear")
    s /= norms[:, None]
    n = np.cross(f, s)
    if normal is not None:
        original_normal = np.asarray(normal, dtype=float)
        if original_normal.shape != n.shape or not np.isfinite(original_normal).all() or np.any(np.linalg.norm(original_normal, axis=1) == 0):
            raise ValueError("Finite aligned original normal directions required")
        if np.any(np.sum(n * original_normal, axis=1) <= 0):
            raise ValueError("Candidate conflicts with the supplied frame handedness")
    return {"fiber": f.tolist(), "sheet": s.tolist(), "normal": n.tolist(),
            "maximum_angular_correction_rad": float(angular_correction.max()),
            "maximum_abs_dot": float(np.abs(np.sum(f * s, axis=1)).max()),
            "declared_angular_bound_rad": bound, "review_status": "pending",
            "qualification": "Numerical frame repair candidate, not measured patient microstructure"}
