# HeartTwin completion status — 2026-10-23

This is the scope supported by the recorded runs. Passing software/operational checks does not qualify patient predictions.

| Area | Completed / observed | Remaining gate |
|---|---|---|
| Patient workflow contracts | Subject/hash/frame/unit checks, reviewed artifacts and patient-workflow regressions are implemented; earlier Colab evidence has 195 passing patient-workflow tests | Real source, anatomy and parameter qualification; contracts alone do not establish personalization accuracy |
| Explicit BSP workflow | Named/reference-weighted reciprocal operators, propagation and frozen held-out scoring are synthetically verified | EDGAR channel/reference/index/export conventions and reviewed conductor/geometry |
| Spatial mechanics capacity | Experimental matrix-free profile passed a 32,784-node/172,032-tet constrained, compressible synthetic shell; default guards remain | Actual KIT reference state, geometry, fibres, material/cavity/boundary qualification and solve |
| Solver robustness / reproducibility | Cached material actions, optional block Jacobi, true linear-residual checks; 152 Colab tests without skips; exact MINI contraction qualified with 7.45x paired finest assembly speedup; mixed/direct and original matrix-free paths checked | Broader mesh/material/constraint robustness; no multigrid/domain-decomposition solution |
| Nearly incompressible mechanics | Mixed MINI reduced the unchanged finest shell error from 80.19% to 2.53%; all frozen Colab gates passed. Original P1 failures remain archived | Three coarse mixed meshes still fail the 15% screen; standard/direct profile only; broader mesh/interface stability and scalable mixed solvers remain unqualified |
| Pressure/flow and broader physiology | Research models and coupling infrastructure exist; native cubic EP is phenomenological | Human cellular, conduction, valve/circulation and whole-heart physiological qualification; earlier murine PV model still loses to its waveform baseline |
| Hospital workflow | Synthetic import/model contracts and operational workflows exist | Actual hospital access, reviewed exports and workflow qualification |
| Clinical prediction | No clinical accuracy qualification established | Qualified independent measurements and a prospective validation design |

## Engineering that still does not require patient data

1. Broaden mixed-mechanics qualification beyond the verified homogeneous shell and affine patches: distorted meshes, material contrast/interfaces, finite-strain pressure modes and physiological loads. The demonstrated P1 locking defect now has a qualified synthetic correction; exact incompressibility and broad stability are not established.
2. Extend scalable preconditioning and robustness tests beyond the current shell: mesh quality, material contrast, anisotropy, constraints and load ranges. Preserve failures and true-residual checks.
3. Extend the physiological model and verify conservation/coupling and model-specific benchmarks before claiming whole-heart prediction.

The remaining items above are substantial development work. They are not solved by more patient data or by a green CI run. Data/access-dependent qualification is a separate requirement.

[Detailed run history and evidence](NONPATIENT_COMPLETION.md). Compatibility matrix v12 advances only CardiMech to `aa42b30d228dca182bbb02c93d90f6ae7da9a674`; other v11 component pins are preserved. The new synthetic replay retained all eight frozen gates over 23 converged solves and all three coarse accuracy failures. Timings cover assembly probes, not universal whole-solver speedups.
