"""Virelion-native cardiac digital-twin primitives.

The architecture is intentionally compatible with the Julia Camps/Zhinuo Wang
Cardiac-Digital-Twin separation of geometry, conduction, propagation,
cellular/repolarisation, observation, discrepancy, and inference while keeping
HeartTwin's public API independent of the upstream research scripts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import csv
import math

import numpy as np


@dataclass(frozen=True)
class EPParameters:
    fibre_speed: float = 0.60
    sheet_speed: float = 0.24
    normal_speed: float = 0.18
    purkinje_speed: float = 2.50
    endo_speed: float = 0.45
    apd_min: float = 240.0
    apd_max: float = 320.0

    def validate(self) -> None:
        positive = {
            "fibre_speed": self.fibre_speed,
            "sheet_speed": self.sheet_speed,
            "normal_speed": self.normal_speed,
            "purkinje_speed": self.purkinje_speed,
            "endo_speed": self.endo_speed,
            "apd_min": self.apd_min,
            "apd_max": self.apd_max,
        }
        bad = [name for name, value in positive.items() if value <= 0]
        if bad:
            raise ValueError(f"Parameters must be positive: {', '.join(bad)}")
        if self.apd_max < self.apd_min:
            raise ValueError("apd_max must be >= apd_min")


@dataclass(frozen=True)
class ScarMap:
    """Node-level pathology state: 0 healthy, 1 border zone, 2 dense scar."""
    labels: np.ndarray
    border_multiplier: float = 0.55
    scar_multiplier: float = 0.08

    def __post_init__(self) -> None:
        labels = np.asarray(self.labels, dtype=int)
        if labels.ndim != 1:
            raise ValueError("Scar labels must be a 1-D array")
        if np.any(~np.isin(labels, [0, 1, 2])):
            raise ValueError("Scar labels must contain only 0, 1, or 2")
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
    """Tetrahedral ventricular mesh plus optional fibre directions and tissue state."""
    node_xyz: np.ndarray
    tetrahedra: np.ndarray
    fibre: np.ndarray | None = None
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
        if self.fibre is not None:
            fibre = np.asarray(self.fibre, dtype=float)
            if fibre.shape != xyz.shape:
                raise ValueError("fibre must have shape (N, 3)")
            norms = np.linalg.norm(fibre, axis=1)
            if np.any(norms == 0):
                raise ValueError("fibre vectors must be non-zero")
            object.__setattr__(self, "fibre", fibre / norms[:, None])
        if self.scar is not None and len(self.scar.labels) != len(xyz):
            raise ValueError("scar labels must match number of mesh nodes")
        object.__setattr__(self, "node_xyz", xyz)
        object.__setattr__(self, "tetrahedra", tet)

    @property
    def n_nodes(self) -> int:
        return int(self.node_xyz.shape[0])

    @property
    def edges(self) -> np.ndarray:
        if self.tetrahedra.size == 0:
            return np.empty((0, 2), dtype=int)
        pairs = []
        for a, b, c, d in self.tetrahedra.tolist():
            pairs.extend(((a, b), (a, c), (a, d), (b, c), (b, d), (c, d)))
        arr = np.sort(np.asarray(pairs, dtype=int), axis=1)
        return np.unique(arr, axis=0)

    @classmethod
    def from_csv(cls, node_xyz_csv: str | Path, tetrahedra_csv: str | Path, fibre_csv: str | Path | None = None, scar_labels_csv: str | Path | None = None, index_base: int = 0) -> "MeshGeometry":
        xyz = _read_numeric_csv(node_xyz_csv, expected_cols=3)
        tet = _read_numeric_csv(tetrahedra_csv, expected_cols=4).astype(int) - int(index_base)
        fibre = None if fibre_csv is None else _read_numeric_csv(fibre_csv, expected_cols=3)
        scar = None
        if scar_labels_csv is not None:
            labels = _read_numeric_csv(scar_labels_csv, expected_cols=1).reshape(-1).astype(int)
            scar = ScarMap(labels)
        return cls(xyz, tet, fibre=fibre, scar=scar)


@dataclass(frozen=True)
class ConductionNetwork:
    """Candidate His/Purkinje root nodes represented as mesh-node indices."""
    candidate_root_nodes: tuple[int, ...]
    selected_root_nodes: tuple[int, ...] | None = None

    def selected(self) -> tuple[int, ...]:
        roots = self.selected_root_nodes or self.candidate_root_nodes
        if not roots:
            raise ValueError("At least one root node is required")
        return tuple(int(x) for x in roots)

    def validate(self, n_nodes: int) -> None:
        for root in self.selected():
            if root < 0 or root >= n_nodes:
                raise ValueError(f"Root node {root} is outside the mesh")


@dataclass(frozen=True)
class EikonalResult:
    activation_time_ms: np.ndarray
    root_nodes: tuple[int, ...]


class EikonalPropagator:
    """Sparse Dijkstra implementation of a mesh-based Eikonal approximation."""
    def simulate(self, geometry: MeshGeometry, conduction: ConductionNetwork, params: EPParameters) -> EikonalResult:
        params.validate()
        conduction.validate(geometry.n_nodes)
        edges = geometry.edges
        adjacency: list[list[tuple[int, float]]] = [[] for _ in range(geometry.n_nodes)]
        tissue_multiplier = geometry.scar.node_multiplier() if geometry.scar is not None else np.ones(geometry.n_nodes)
        for u, v in edges.tolist():
            delta = geometry.node_xyz[v] - geometry.node_xyz[u]
            length = float(np.linalg.norm(delta))
            if length == 0:
                continue
            direction = delta / length
            if geometry.fibre is None:
                velocity = params.normal_speed
            else:
                alignment = abs(float(np.dot(direction, geometry.fibre[u])))
                velocity = params.fibre_speed * alignment + params.sheet_speed * (1.0 - alignment)
            velocity *= 0.5 * (tissue_multiplier[u] + tissue_multiplier[v])
            cost = length / max(velocity, 1e-6)
            adjacency[u].append((v, cost)); adjacency[v].append((u, cost))
        distance = np.full(geometry.n_nodes, np.inf, dtype=float)
        heap: list[tuple[float, int]] = []
        roots = conduction.selected()
        for root in roots:
            distance[root] = 0.0; heappush(heap, (0.0, root))
        while heap:
            current, u = heappop(heap)
            if current != distance[u]: continue
            for v, cost in adjacency[u]:
                candidate = current + cost
                if candidate < distance[v]:
                    distance[v] = candidate; heappush(heap, (candidate, v))
        if not np.all(np.isfinite(distance)):
            raise ValueError("Mesh contains nodes unreachable from selected root nodes")
        return EikonalResult(distance * 1000.0, roots)


@dataclass(frozen=True)
class RepolarizationResult:
    apd_ms: np.ndarray
    repolarization_time_ms: np.ndarray


class RepolarizationModel:
    def simulate(self, activation_ms: np.ndarray, geometry: MeshGeometry, params: EPParameters) -> RepolarizationResult:
        if params.apd_max == params.apd_min:
            apd = np.full_like(activation_ms, params.apd_min, dtype=float)
        else:
            z = geometry.node_xyz[:, 2]; z0, z1 = float(z.min()), float(z.max())
            frac = np.zeros_like(z) if z1 == z0 else (z - z0) / (z1 - z0)
            apd = params.apd_min + frac * (params.apd_max - params.apd_min)
        return RepolarizationResult(apd, activation_ms + apd)


@dataclass(frozen=True)
class ECGObservation:
    lead_names: tuple[str, ...]
    values: np.ndarray
    sample_rate_hz: float
    def __post_init__(self) -> None:
        values=np.asarray(self.values,dtype=float)
        if values.ndim != 2 or values.shape[0] != len(self.lead_names): raise ValueError("ECG values/lead_names mismatch")
        if self.sample_rate_hz <= 0: raise ValueError("sample_rate_hz must be positive")
        object.__setattr__(self,"values",values)


class PseudoECG:
    """Reduced deterministic ECG observation model for research plumbing/tests.

    It is not a validated clinical ECG renderer.
    """
    DEFAULT_LEADS=("I","II","III","aVR","aVL","aVF","V1","V2","V3","V4","V5","V6")
    def __init__(self, electrode_xyz: np.ndarray | None=None, lead_names: Sequence[str]=DEFAULT_LEADS):
        self.lead_names=tuple(lead_names)
        if electrode_xyz is None:
            angles=np.linspace(0,2*math.pi,len(self.lead_names),endpoint=False)
            electrode_xyz=np.column_stack((np.cos(angles),np.sin(angles),0.25*np.sin(2*angles)))
        electrode_xyz=np.asarray(electrode_xyz,dtype=float)
        if electrode_xyz.shape != (len(self.lead_names),3): raise ValueError("electrode_xyz shape mismatch")
        self.electrode_xyz=electrode_xyz
    def calculate(self, geometry: MeshGeometry, activation_ms: np.ndarray, repolarization_ms: np.ndarray, duration_ms:int=600, sample_rate_hz:float=1000.0)->ECGObservation:
        n=int(round(duration_ms*sample_rate_hz/1000.0)); t=np.arange(n)/sample_rate_hz*1000.0
        centroid=geometry.node_xyz.mean(axis=0); directions=self.electrode_xyz-centroid; directions/=np.maximum(np.linalg.norm(directions,axis=1,keepdims=True),1e-12)
        radial=geometry.node_xyz-centroid; radial/=np.maximum(np.linalg.norm(radial,axis=1,keepdims=True),1e-12)
        weights=radial@directions.T; out=np.zeros((len(self.lead_names),n))
        for li in range(len(self.lead_names)):
            signal=np.zeros(n)
            for node in range(geometry.n_nodes):
                signal += weights[node,li]*np.exp(-0.5*((t-activation_ms[node])/8.0)**2)
                signal -= 0.6*weights[node,li]*np.exp(-0.5*((t-repolarization_ms[node])/25.0)**2)
            out[li]=signal
        return ECGObservation(self.lead_names,out,sample_rate_hz)


@dataclass(frozen=True)
class CalibrationSpec:
    bounds: Mapping[str, tuple[float,float]]
    max_iterations:int=32
    step_fraction:float=0.25
    weights:Mapping[str,float]=field(default_factory=lambda:{"activation":1.0,"ecg":0.25})

@dataclass(frozen=True)
class TwinSimulation:
    activation:np.ndarray; apd:np.ndarray; repolarization:np.ndarray; ecg:ECGObservation|None; parameters:EPParameters

class CardiacDigitalTwin:
    """Virelion-native digital-twin facade."""
    def __init__(self,geometry:MeshGeometry,conduction:ConductionNetwork,params:EPParameters|None=None,ecg_model:PseudoECG|None=None):
        self.geometry=geometry; self.conduction=conduction; self.params=params or EPParameters(); self.ecg_model=ecg_model or PseudoECG(); self._propagator=EikonalPropagator(); self._repolarization=RepolarizationModel()
    def simulate(self,*,with_ecg:bool=True,duration_ms:int=600)->TwinSimulation:
        result=self._propagator.simulate(self.geometry,self.conduction,self.params); rep=self._repolarization.simulate(result.activation_time_ms,self.geometry,self.params)
        ecg=self.ecg_model.calculate(self.geometry,result.activation_time_ms,rep.repolarization_time_ms,duration_ms=duration_ms) if with_ecg else None
        return TwinSimulation(result.activation_time_ms,rep.apd_ms,rep.repolarization_time_ms,ecg,self.params)
    def calibrate(self,target_activation:np.ndarray|None=None,target_ecg:ECGObservation|None=None,spec:CalibrationSpec|None=None)->tuple[EPParameters,dict[str,float]]:
        spec=spec or CalibrationSpec(bounds={"fibre_speed":(0.10,1.50),"sheet_speed":(0.05,0.80),"normal_speed":(0.05,0.60)})
        current=self.params; best=_discrepancy(self,current,target_activation,target_ecg,spec.weights); steps={n:(hi-lo)*spec.step_fraction for n,(lo,hi) in spec.bounds.items()}
        for _ in range(spec.max_iterations):
            improved=False
            for name,(lo,hi) in spec.bounds.items():
                for direction in (-1.0,1.0):
                    value=float(np.clip(getattr(current,name)+direction*steps[name],lo,hi)); vals=current.__dict__.copy(); vals[name]=value; proposal=EPParameters(**vals); score=_discrepancy(self,proposal,target_activation,target_ecg,spec.weights)
                    if score<best: current,best=proposal,score; improved=True
            if not improved:
                steps={k:v*0.5 for k,v in steps.items()}
                if max(steps.values())<1e-4: break
        self.params=current; return current,{"objective":float(best),"iterations":float(spec.max_iterations)}

class UpstreamCardiacDigitalTwinAdapter:
    """Explicit bridge to an installed upstream wrapper using module:function."""
    def __init__(self,entrypoint:str):
        if ":" not in entrypoint: raise ValueError("entrypoint must use 'module:function' syntax")
        self.entrypoint=entrypoint; module_name,function_name=entrypoint.split(":",1); module=import_module(module_name); self._callable=getattr(module,function_name)
    def invoke(self,payload:Mapping[str,Any])->dict[str,Any]:
        result=self._callable(payload)
        if not isinstance(result,Mapping): raise TypeError("Upstream CDT entrypoint must return a mapping")
        return dict(result)

def _discrepancy(twin:CardiacDigitalTwin,params:EPParameters,target_activation:np.ndarray|None,target_ecg:ECGObservation|None,weights:Mapping[str,float])->float:
    original=twin.params; twin.params=params
    try: sim=twin.simulate(with_ecg=target_ecg is not None)
    finally: twin.params=original
    score=0.0
    if target_activation is not None:
        target=np.asarray(target_activation,dtype=float)
        if target.shape != sim.activation.shape: raise ValueError("target_activation shape must match mesh node count")
        score += float(weights.get("activation",1.0))*float(np.sqrt(np.mean((sim.activation-target)**2)))
    if target_ecg is not None:
        if sim.ecg is None or sim.ecg.values.shape != target_ecg.values.shape: raise ValueError("target_ecg must have the same shape as simulated ECG")
        score += float(weights.get("ecg",1.0))*float(np.sqrt(np.mean((sim.ecg.values-target_ecg.values)**2)))
    return score

def _read_numeric_csv(path:str|Path,expected_cols:int)->np.ndarray:
    rows=list(csv.reader(Path(path).open("r",newline=""))); parsed=[]
    for row in rows:
        if not row: continue
        try: values=[float(x) for x in row]
        except ValueError: continue
        if len(values)!=expected_cols: raise ValueError(f"Expected {expected_cols} numeric columns in {path}")
        parsed.append(values)
    if not parsed: raise ValueError(f"No numeric rows found in {path}")
    return np.asarray(parsed,dtype=float)
