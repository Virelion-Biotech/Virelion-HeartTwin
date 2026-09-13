# Cardiac-Digital-Twin numerical-equivalence gate

## Purpose

This gate establishes whether the Virelion CardiSim backend reproduces a pinned upstream `Cardiac-Digital-Twin` forward calculation before any claim of numerical equivalence is made.

## Pinned sources

- Upstream repository: `juliacamps/Cardiac-Digital-Twin`
- Upstream commit: `816d51fab0837cfe9e20c7d3a318429e9acf0733`
- Upstream license: MIT
- Published example data: Zenodo record `14034739`, DOI `10.5281/zenodo.14034739`

The upstream README states that example meshes, ECGs, cellular models and digital-twin inputs are published in the Zenodo data record. The repository's architecture separates geometry, conduction system, cellular, propagation, ECG, simulation, discrepancy, evaluation and sampling/inference modules.

## Why the gate starts with a forward fixture

The published personalization scripts contain stochastic SMC-ABC inference and experiment-specific configuration. The reference workflow also assumes a local `.custom_config` path mapping and a dataset layout containing `clinical_data`, `geometric_data` and `cellular_data`. The forward fixture therefore isolates the deterministic numerical core first.

The upstream Eikonal/Dijkstra implementation constructs anisotropic edge costs from a fibre-sheet-normal metric, injects Purkinje root activation times, runs Dijkstra propagation and returns rounded millisecond LAT values. The fixture records the exact upstream edge ordering, edge fibre basis, node coordinates, tetrahedra, root nodes and root activation times required to reproduce that calculation.

## Reproduction pipeline

```text
Zenodo DOI
   |
   v
fetch_cdt_zenodo.py
   |
   v
published example dataset
   |
   +-----------------------------+
   |                             |
   v                             v
pinned upstream checkout      Virelion HeartTwin
   |                             |
   v                             v
build_cdt_forward_fixture.py  build_native_from_cdt_fixture.py
   |                             |
   +-------------+---------------+
                 |
                 v
        compare_cdt_lat.py
                 |
        PASS ---------------- FAIL
```

## Tolerances

The comparator defaults to exact equality because the first target is a deterministic compatibility fixture. Tolerances can be relaxed only after the numerical difference is characterized and documented. A passing correlation with a non-zero error is **not** sufficient for an equivalence claim.

## Second gate: full personalization

After forward equivalence is established, a second gate should reproduce a fixed `personalise_to_QT.py` or `personalise_to_QRS.py` experiment using the published data, including the SMC-ABC configuration and output parameter population. This is intentionally separate because it tests inference, preprocessing, optimizer behavior and output persistence in addition to the forward solver.

## Scientific boundary

The native backend currently includes a Virelion-native Eikonal/ECG reference implementation and scar-aware tissue abstraction. These are research infrastructure. They must not be presented as a clinically validated model or as numerically equivalent to the upstream solver until the automated fixture gate passes.
