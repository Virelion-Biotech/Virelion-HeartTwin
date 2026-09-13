# Cardiac-Digital-Twin integration

## Decision

HeartTwin integrates the Julia Camps / Zhinuo (Jenny) Wang `Cardiac-Digital-Twin` architecture as the electrophysiology personalization substrate for CardiSim without making the upstream research scripts the HeartTwin public API.

The upstream repository is MIT licensed. The audited upstream reference commit is `816d51fab0837cfe9e20c7d3a318429e9acf0733`.

## Architecture

```text
parameter space
    -> parameter adapter
    -> geometry / conduction
    -> propagation
    -> cellular / repolarisation
    -> observation (ECG)
    -> discrepancy
    -> inference
```

The upstream repository explicitly separates geometry, conduction system, cellular models, propagation, electrophysiology, ECG, simulation, discrepancy, evaluation, sampling, and parameter adaptation. HeartTwin preserves those boundaries while replacing its public API with typed Virelion interfaces.

## Integration layers

### 1. Geometry

`MeshGeometry` accepts ventricular point coordinates, tetrahedra, optional fibre directions, and optional node-level scar labels. CSV loading supports an explicit index base so source datasets can retain their native indexing convention.

### 2. Conduction

`ConductionNetwork` treats candidate/selected root nodes as first-class state. This mirrors the upstream treatment of His/Purkinje activation roots rather than hiding them inside propagation code.

### 3. Propagation

`EikonalPropagator` uses mesh edges and Dijkstra shortest paths. With fibre vectors it applies a reduced fibre/sheet anisotropy model; with scar it modifies local conduction velocity through tissue multipliers.

This is a Virelion-native reference implementation. It is **not** claimed to be numerically identical to the upstream CDT solver.

### 4. Repolarisation

`RepolarizationModel` converts activation time and a spatial APD field into repolarisation time. The initial implementation is deliberately simple so it can serve as a stable integration contract before introducing richer cellular models.

### 5. Observation

`PseudoECG` deterministically renders activation/repolarisation timing into a multi-lead observation. It exists for software integration and synthetic calibration tests only; it is not a validated clinical ECG renderer.

### 6. Inference

`CalibrationSpec` and `CardiacDigitalTwin.calibrate()` provide bounded parameter search with objective weighting. The API is intentionally compatible with later Bayesian, SMC, or surrogate-assisted inference without coupling HeartTwin to a specific optimizer.

### 7. Upstream runtime bridge

`UpstreamCardiacDigitalTwinAdapter` accepts a configured `module:function` wrapper. This is preferable to hard-coding a volatile upstream CLI or path layout. A laboratory can therefore run the original research implementation while HeartTwin sees a stable JSON-like contract.

## MI/scar extension

Scar is a first-class tissue-state axis:

```text
0 = healthy
1 = border zone
2 = dense scar
```

Current propagation uses scalar local velocity multipliers. Future validated models should replace these with spatially calibrated conduction tensors, explicit fibrosis geometry, and tissue-specific cellular/repolarisation models.

## Validation gates

1. **Unit/integration:** deterministic forward output, mesh validation, scar slowing, and registry/API round-trip.
2. **Synthetic inverse:** recover known propagation parameters from synthetic activation maps.
3. **Numerical equivalence:** compare Virelion outputs to a pinned upstream CDT wrapper on the published example data.
4. **External validation:** evaluate on held-out ECG/imaging observations through CardiEval.
5. **Clinical boundary:** no patient-level claims until independent study design and validation support them.

## Provenance

Machine-readable provenance lives in `hearttwin/cdt_manifest.py`. Keep the upstream repository URL, pinned commit, MIT license, and copyright notice when any upstream source is actually copied or redistributed.
