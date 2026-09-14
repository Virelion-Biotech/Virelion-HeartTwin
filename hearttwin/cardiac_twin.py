"""Virelion-native cardiac digital-twin primitives.

Mirrors the audited Cardiac-Digital-Twin separation of geometry, conduction,
propagation, cellular/repolarisation, observation and inference while keeping
HeartTwin's public API independent of research scripts.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping, Sequence
import csv
import math

import numpy as np


@dataclass(frozen=True)
class EPParameters:
    """EP parameters using cm, ms and cm/ms, matching the upstream convention."""

    fibre_speed: float = 0.065
    sheet_speed: float = 0.051
    normal_speed: float = 0.048
    purkinje_speed: float = 0.300
    endo_dense_speed: float = 0.065
    endo_sparse_speed: float = 0.060
    apd_min: float = 240.0
    apd_max: float = 320.0

    def validate(self) -> None:
        values = self.__dict__
        bad = [k for k, v in values.items() if not np.isfinite(v) or v <= 0]
        if bad:
            raise ValueError(f"Parameters must be finite and positive: {', '.join(bad)}")
        if self.apd_max < self.apd_min:
            raise ValueError("apd_max must be >= apd_min")


@dataclass(frozen=True)
class ScarMap:
    """Node-level pathology: 0 healthy, 1 border zone, 2 dense scar."""

    labels: np.ndarray
    border_multiplier: float = 0.55
    scar_multiplier: float = 0.08

    def __post_init__(self) -> None:
        labels = np.asarray(self.labels, dtype=int)
        if labels.ndim != 1 or np.any(~np.isin(labels, [0, 1, 2])):
            raise ValueError("Scar labels must be 1-D and contain only 0, 1 or 2")
        if not (0 < self.scar_multiplier <= self.border_multiplier <= 1):
            raise ValueError("Expected 0 < scar_multiplier <= border_multiplier <= 1")
        object.__setattr__(self, "labels", labels)

    def node_multiplier(self) -> np.ndarray:
        out = np.ones(self.labels.shape[0], dtype=float)
        out[self.labels == 1] = self.border_multiplier
        out[self.labels == 2] = self.scar_multiplier
        return out


@dataclass(frozen=True)
class MeshGeometry:
    """Tetrahedral mesh with optional nodal and edge fibre information."""

    node_xyz: np.ndarray
    tetrahedra: np.ndarray
    fibre: np.ndarray | None = None
    sheet: np.ndarray | None = None
    normal: np.ndarray | None = None
    edge_nodes: np.ndarray | None = None
    edge_fibre_sheet_normal: np.ndarray | None = None
    dense_endocardial: np.ndarray | None = None
    sparse_endocardial: np.ndarray | None = None
    scar: ScarMap | None = None

    def __post_init__(self) -> None:
        xyz = np.asarray(self.node_xyz, dtype=float)
        tet = np.asarray(self.tetrahedra, dtype=int)
        if xyz.ndim != 2 or xyz.shape[1] != 3:
            raise ValueError("node_xyz must have shape (N, 3)")
        if tet.ndim != 2 or tet.shape[1] != 4:
            raise ValueError("tetrahedra must have shape (M, 4)")
        if tet.size and (tet.min() < 0 or tet.max() >= len(xyz)):
            raise ValueError("tetrahedra contain out-of-range node indices")
        for name in ("fibre", "sheet", "normal"):
            value = getattr(self, name)
            if value is None:
                continue
            arr = np.asarray(value, dtype=float)
            if arr.shape != xyz.shape:
                raise ValueError(f"{name} must have shape (N, 3)")
            norms = np.linalg.norm(arr, axis=1)
            if np.any(~np.isfinite(norms)) or np.any(norms == 0):
                raise ValueError(f"{name} vectors must be finite and non-zero")
            object.__setattr__(self, name, arr / norms[:, None])
        if self.edge_nodes is not None:
            edges = np.asarray(self.edge_nodes, dtype=int)
            if edges.ndim != 2 or edges.shape[1] != 2:
                raise ValueError("edge_nodes must have shape (E, 2)")
            if edges.size and (edges.min() < 0 or edges.max() >= len(xyz)):
                raise ValueError("edge_nodes contain out-of-range indices")
            object.__setattr__(self, "edge_nodes", edges)
        if self.edge_fibre_sheet_normal is not None:
            basis = np.asarray(self.edge_fibre_sheet_normal, dtype=float)
            if basis.ndim != 3 or basis.shape[1:] != (3, 3):
                raise ValueError("edge_fibre_sheet_normal must have shape (E, 3, 3)")
            if self.edge_nodes is not None and len(basis) != len(self.edge_nodes):
                raise ValueError("edge basis count must match edge_nodes")
            object.__setattr__(self, "edge_fibre_sheet_normal", basis)

        if self.dense_endocardial is not None or self.sparse_endocardial is not None:
            if self.edge_nodes is None:
                raise ValueError(
                    "Endocardial edge classes require explicit edge_nodes ordering"
                )
            edge_count = len(self.edge_nodes)
            for name in ("dense_endocardial", "sparse_endocardial"):
                value = getattr(self, name)
                if value is None:
                    continue
                arr = np.asarray(value, dtype=bool)
                if arr.ndim != 1 or len(arr) != edge_count:
                    raise ValueError(
                        f"{name} must have shape ({edge_count},)"
                    )
                object.__setattr__(self, name, arr)
            if (
                self.dense_endocardial is not None
                and self.sparse_endocardial is not None
                and np.any(self.dense_endocardial & self.sparse_endocardial)
            ):
                raise ValueError(
                    "dense_endocardial and sparse_endocardial edge classes overlap"
                )

        if self.scar is not None and len(self.scar.labels) != len(xyz):
            raise ValueError("scar labels must match mesh node count")
        object.__setattr__(self, "node_xyz", xyz)
        object.__setattr__(self, "tetrahedra", tet)

    @property
    def n_nodes(self) -> int:
        return int(self.node_xyz.shape[0])

    @property
    def edges(self) -> np.ndarray:
        if self.edge_nodes is not None:
            return self.edge_nodes
        if self.tetrahedra.size == 0:
            return np.empty((0, 2), dtype=int)
        pairs: list[tuple[int, int]] = []
        for a, b, c, d in self.tetrahedra.tolist():
            pairs.extend(((a, b), (a, c), (a, d), (b, c), (b, d), (c, d)))
        return np.unique(np.sort(np.asarray(pairs, dtype=int), axis=1), axis=0)

    @classmethod
    def from_csv(
        cls,
        node_xyz_csv: str | Path,
        tetrahedra_csv: str | Path,
        fibre_csv: str | Path | None = None,
        sheet_csv: str | Path | None = None,
        normal_csv: str | Path | None = None,
        scar_labels_csv: str | Path | None = None,
        index_base: int = 0,
    ) -> "MeshGeometry":
        xyz = _read_numeric_csv(node_xyz_csv, 3)
        tet = _read_numeric_csv(tetrahedra_csv, 4).astype(int) - int(index_base)
        fibre = None if fibre_csv is None else _read_numeric_csv(fibre_csv, 3)
        sheet = None if sheet_csv is None else _read_numeric_csv(sheet_csv, 3)
        normal = None if normal_csv is None else _read_numeric_csv(normal_csv, 3)
        scar = None if scar_labels_csv is None else ScarMap(_read_numeric_csv(scar_labels_csv, 1).ravel().astype(int))
        return cls(xyz, tet, fibre=fibre, sheet=sheet, normal=normal, scar=scar)


@dataclass(frozen=True)
class ConductionNetwork:
    """Candidate root nodes and optional upstream-derived activation times."""

    candidate_root_nodes: tuple[int, ...]
    selected_root_nodes: tuple[int, ...] | None = None
    root_activation_ms: Mapping[int, float] | None = None

    def selected(self) -> tuple[int, ...]:
        roots = self.selected_root_nodes if self.selected_root_nodes is not None else self.candidate_root_nodes
        if not roots:
            raise ValueError("At least one root node is required")
        return tuple(int(x) for x in roots)

    def validate(self, n_nodes: int) -> None:
        for root in self.selected():
            if root < 0 or root >= n_nodes:
                raise ValueError(f"Root node {root} is outside the mesh")
        if self.root_activation_ms:
            missing = [r for r in self.selected() if r not in self.root_activation_ms]
            if missing:
                raise ValueError(f"Missing root activation times for: {missing}")

    def root_times(self) -> np.ndarray:
        roots = self.selected()
        if not self.root_activation_ms:
            return np.zeros(len(roots), dtype=float)
        return np.asarray([float(self.root_activation_ms[r]) for r in roots], dtype=float)


@dataclass(frozen=True)
class EikonalResult:
    activation_time_ms: np.ndarray
    root_nodes: tuple[int, ...]


class EikonalPropagator:
    """Sparse Dijkstra using the upstream anisotropic Eikonal edge metric when available."""

    def __init__(self, legacy_output: bool = False):
        self.legacy_output = legacy_output

    def _edge_cost(
        self,
        geometry: MeshGeometry,
        edge_index: int,
        u: int,
        v: int,
        params: EPParameters,
        tissue: np.ndarray,
    ) -> float:
        """Return upstream-compatible Eikonal propagation cost.

        The published Cardiac-Digital-Twin implementation has three edge
        regimes:

        1. dense endocardial: Euclidean distance / endo_dense_speed
        2. sparse endocardial: Euclidean distance / endo_sparse_speed
        3. ventricular: anisotropic Eikonal metric using edge fibre/sheet/normal

        The native scar/tissue modifier is retained after the base cost.
        """
        delta = geometry.node_xyz[v] - geometry.node_xyz[u]

        if not np.all(np.isfinite(delta)):
            raise ValueError("Mesh contains non-finite coordinates")

        dense = geometry.dense_endocardial
        sparse = geometry.sparse_endocardial

        # Dense endocardial edges use isotropic conduction.
        if dense is not None and dense[edge_index]:
            length = float(np.linalg.norm(delta))
            if length == 0.0:
                return math.inf
            base_cost = length / params.endo_dense_speed

        # Sparse endocardial edges use isotropic conduction.
        elif sparse is not None and sparse[edge_index]:
            length = float(np.linalg.norm(delta))
            if length == 0.0:
                return math.inf
            base_cost = length / params.endo_sparse_speed

        # Ventricular edges use the anisotropic Eikonal metric.
        else:
            basis = geometry.edge_fibre_sheet_normal

            if basis is not None:
                g = np.diag(
                    [
                        params.fibre_speed**2,
                        params.sheet_speed**2,
                        params.normal_speed**2,
                    ]
                )

                metric = basis[edge_index] @ g @ basis[edge_index].T

                try:
                    inverse = np.linalg.inv(metric)
                except np.linalg.LinAlgError as exc:
                    raise ValueError(
                        f"Singular fibre metric on edge {edge_index}"
                    ) from exc

                value = float(delta @ inverse @ delta.T)

                if value < 0 and value > -1e-10:
                    value = 0.0

                if value < 0:
                    raise ValueError(
                        f"Negative Eikonal metric on edge {edge_index}"
                    )

                base_cost = math.sqrt(value)

            else:
                # Existing generic fallback for meshes without an edge basis.
                length = float(np.linalg.norm(delta))

                if length == 0.0:
                    return math.inf

                if geometry.fibre is None:
                    velocity = params.normal_speed
                else:
                    alignment = abs(
                        float(np.dot(delta / length, geometry.fibre[u]))
                    )
                    velocity = (
                        params.fibre_speed * alignment
                        + params.sheet_speed * (1.0 - alignment)
                    )

                base_cost = length / max(velocity, 1e-12)

        return base_cost / max(
            0.5 * (tissue[u] + tissue[v]),
            1e-12,
        )

    def simulate(self, geometry: MeshGeometry, conduction: ConductionNetwork, params: EPParameters) -> EikonalResult:
        params.validate()
        conduction.validate(geometry.n_nodes)
        edges = geometry.edges
        tissue = geometry.scar.node_multiplier() if geometry.scar is not None else np.ones(geometry.n_nodes)
        adjacency: list[list[tuple[int, float]]] = [[] for _ in range(geometry.n_nodes)]
        for i, (u, v) in enumerate(edges.tolist()):
            cost = self._edge_cost(geometry, i, int(u), int(v), params, tissue)
            if math.isinf(cost):
                continue
            adjacency[int(u)].append((int(v), cost))
            adjacency[int(v)].append((int(u), cost))
        roots = conduction.selected()
        root_times = conduction.root_times()
        distance = np.full(geometry.n_nodes, np.inf, dtype=float)
        heap: list[tuple[float, int]] = []
        for root, root_time in zip(roots, root_times):
            distance[root] = float(root_time)
            heappush(heap, (float(root_time), root))
        while heap:
            current, u = heappop(heap)
            if current > distance[u] + 1e-12:
                continue
            for v, cost in adjacency[u]:
                candidate = current + cost
                if candidate < distance[v] - 1e-12:
                    distance[v] = candidate
                    heappush(heap, (candidate, v))
        if not np.all(np.isfinite(distance)):
            raise ValueError("Mesh contains nodes unreachable from selected root nodes")
        if self.legacy_output:
            distance = np.round(distance - float(root_times.min())).astype(np.int32) + 1
        return EikonalResult(distance, roots)


@dataclass(frozen=True)
class RepolarizationResult:
    apd_ms: np.ndarray
    repolarization_time_ms: np.ndarray


class RepolarizationModel:
    def simulate(self, activation_ms: np.ndarray, geometry: MeshGeometry, params: EPParameters) -> RepolarizationResult:
        params.validate()
        z = geometry.node_xyz[:, 2]
        z0, z1 = float(z.min()), float(z.max())
        frac = np.zeros_like(z) if z1 == z0 else (z - z0) / (z1 - z0)
        apd = params.apd_min + frac * (params.apd_max - params.apd_min)
        return RepolarizationResult(apd, activation_ms + apd)


@dataclass(frozen=True)
class ECGObservation:
    lead_names: tuple[str, ...]
    values: np.ndarray
    sample_rate_hz: float

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 2 or values.shape[0] != len(self.lead_names):
            raise ValueError("ECG values/lead_names mismatch")
        if not np.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        object.__setattr__(self, "values", values)


class PseudoECG:
    """Deterministic plumbing model; not a validated clinical ECG renderer."""

    DEFAULT_LEADS = ("I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6")

    def __init__(self, electrode_xyz: np.ndarray | None = None, lead_names: Sequence[str] = DEFAULT_LEADS):
        self.lead_names = tuple(lead_names)
        if electrode_xyz is None:
            angles = np.linspace(0, 2 * math.pi, len(self.lead_names), endpoint=False)
            electrode_xyz = np.column_stack((np.cos(angles), np.sin(angles), 0.25 * np.sin(2 * angles)))
        electrode_xyz = np.asarray(electrode_xyz, dtype=float)
        if electrode_xyz.shape != (len(self.lead_names), 3):
            raise ValueError("electrode_xyz shape mismatch")
        self.electrode_xyz = electrode_xyz

    def calculate(self, geometry: MeshGeometry, activation_ms: np.ndarray, repolarization_ms: np.ndarray, duration_ms: int = 600, sample_rate_hz: float = 1000.0) -> ECGObservation:
        if duration_ms <= 0 or sample_rate_hz <= 0:
            raise ValueError("duration_ms and sample_rate_hz must be positive")
        n = int(round(duration_ms * sample_rate_hz / 1000.0))
        t = np.arange(n, dtype=float) / sample_rate_hz * 1000.0
        centroid = geometry.node_xyz.mean(axis=0)
        directions = self.electrode_xyz - centroid
        directions /= np.maximum(np.linalg.norm(directions, axis=1, keepdims=True), 1e-12)
        radial = geometry.node_xyz - centroid
        radial /= np.maximum(np.linalg.norm(radial, axis=1, keepdims=True), 1e-12)
        weights = radial @ directions.T
        out = np.zeros((len(self.lead_names), n), dtype=float)
        for li in range(len(self.lead_names)):
            signal = np.zeros(n, dtype=float)
            for node in range(geometry.n_nodes):
                signal += weights[node, li] * np.exp(-0.5 * ((t - activation_ms[node]) / 8.0) ** 2)
                signal -= 0.6 * weights[node, li] * np.exp(-0.5 * ((t - repolarization_ms[node]) / 25.0) ** 2)
            out[li] = signal
        return ECGObservation(self.lead_names, out, sample_rate_hz)


@dataclass(frozen=True)
class CalibrationSpec:
    bounds: Mapping[str, tuple[float, float]]
    max_iterations: int = 32
    step_fraction: float = 0.25
    weights: Mapping[str, float] = field(default_factory=lambda: {"activation": 1.0, "ecg": 0.25})


@dataclass(frozen=True)
class TwinSimulation:
    activation: np.ndarray
    apd: np.ndarray
    repolarization: np.ndarray
    ecg: ECGObservation | None
    parameters: EPParameters


class CardiacDigitalTwin:
    """Virelion-native facade for forward simulation and bounded calibration."""

    def __init__(self, geometry: MeshGeometry, conduction: ConductionNetwork, params: EPParameters | None = None, ecg_model: PseudoECG | None = None, legacy_output: bool = False):
        self.geometry = geometry
        self.conduction = conduction
        self.params = params or EPParameters()
        self.ecg_model = ecg_model or PseudoECG()
        self._propagator = EikonalPropagator(legacy_output=legacy_output)
        self._repolarization = RepolarizationModel()

    def simulate(self, *, with_ecg: bool = True, duration_ms: int = 600) -> TwinSimulation:
        result = self._propagator.simulate(self.geometry, self.conduction, self.params)
        rep = self._repolarization.simulate(result.activation_time_ms, self.geometry, self.params)
        ecg = self.ecg_model.calculate(self.geometry, result.activation_time_ms, rep.repolarization_time_ms, duration_ms=duration_ms) if with_ecg else None
        return TwinSimulation(result.activation_time_ms, rep.apd_ms, rep.repolarization_time_ms, ecg, self.params)

    def calibrate(self, target_activation: np.ndarray | None = None, target_ecg: ECGObservation | None = None, spec: CalibrationSpec | None = None) -> tuple[EPParameters, dict[str, float]]:
        if target_activation is None and target_ecg is None:
            raise ValueError("At least one target observation is required")
        spec = spec or CalibrationSpec(bounds={"fibre_speed": (0.02, 0.15), "sheet_speed": (0.02, 0.12), "normal_speed": (0.02, 0.10)})
        current = self.params
        best = _discrepancy(self, current, target_activation, target_ecg, spec.weights)
        steps = {n: (hi - lo) * spec.step_fraction for n, (lo, hi) in spec.bounds.items()}
        iterations = 0
        for iterations in range(1, spec.max_iterations + 1):
            improved = False
            for name, (lo, hi) in spec.bounds.items():
                for direction in (-1.0, 1.0):
                    value = float(np.clip(getattr(current, name) + direction * steps[name], lo, hi))
                    proposal = replace(current, **{name: value})
                    score = _discrepancy(self, proposal, target_activation, target_ecg, spec.weights)
                    if score < best:
                        current, best, improved = proposal, score, True
            if not improved:
                steps = {k: v * 0.5 for k, v in steps.items()}
                if max(steps.values()) < 1e-4:
                    break
        self.params = current
        return current, {"objective": float(best), "iterations": float(iterations)}


class UpstreamCardiacDigitalTwinAdapter:
    """Explicit bridge to an installed upstream wrapper using ``module:function``."""

    def __init__(self, entrypoint: str):
        if ":" not in entrypoint:
            raise ValueError("entrypoint must use 'module:function' syntax")
        module_name, function_name = entrypoint.split(":", 1)
        module = import_module(module_name)
        self._callable = getattr(module, function_name)
        self.entrypoint = entrypoint

    def invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self._callable(payload)
        if not isinstance(result, Mapping):
            raise TypeError("Upstream CDT entrypoint must return a mapping")
        return dict(result)


def _discrepancy(twin: CardiacDigitalTwin, params: EPParameters, target_activation: np.ndarray | None, target_ecg: ECGObservation | None, weights: Mapping[str, float]) -> float:
    original = twin.params
    twin.params = params
    try:
        sim = twin.simulate(with_ecg=target_ecg is not None)
    finally:
        twin.params = original
    score = 0.0
    if target_activation is not None:
        target = np.asarray(target_activation, dtype=float)
        if target.shape != sim.activation.shape:
            raise ValueError("target_activation shape must match mesh node count")
        score += float(weights.get("activation", 1.0)) * float(np.sqrt(np.mean((sim.activation - target) ** 2)))
    if target_ecg is not None:
        if sim.ecg is None or sim.ecg.values.shape != target_ecg.values.shape:
            raise ValueError("target_ecg must have the same shape as simulated ECG")
        score += float(weights.get("ecg", 1.0)) * float(np.sqrt(np.mean((sim.ecg.values - target_ecg.values) ** 2)))
    return score


def _read_numeric_csv(path: str | Path, expected_cols: int) -> np.ndarray:
    rows = []
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            try:
                values = [float(x) for x in row]
            except ValueError:
                continue
            if len(values) != expected_cols:
                raise ValueError(f"Expected {expected_cols} numeric columns in {path}")
            rows.append(values)
    if not rows:
        raise ValueError(f"No numeric rows found in {path}")
    return np.asarray(rows, dtype=float)
