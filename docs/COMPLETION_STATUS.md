# HeartTwin completion status — 2026-10-22

This is the scope supported by the recorded runs. Passing software/operational checks does not qualify patient predictions.

| Area | Completed / observed | Remaining gate |
|---|---|---|
| Patient workflow contracts | Subject/hash/frame/unit checks, reviewed artifacts and patient-workflow regressions are implemented; earlier Colab evidence has 195 passing patient-workflow tests | Real source, anatomy and parameter qualification; contracts alone do not establish personalization accuracy |
| Explicit BSP workflow | Named/reference-weighted reciprocal operators, propagation and frozen held-out scoring are synthetically verified | EDGAR channel/reference/index/export conventions and reviewed conductor/geometry |
| Spatial mechanics capacity | Experimental matrix-free profile passed a 32,784-node/172,032-tet constrained, compressible synthetic shell; default guards remain | Actual KIT reference state, geometry, fibres, material/cavity/boundary qualification and solve |
| Solver robustness / reproducibility | Cached material actions, optional block Jacobi, true linear-residual checks; 128 Colab tests without skips; published notebook executed end to end | Broader mesh/material/constraint robustness; no multigrid/domain-decomposition solution |
| Nearly incompressible mechanics | Direct-solver characterization exposes failure; warnings/provenance and raw negative evidence are retained | **Unfixed:** finest tested P1 case had about 80.2% displacement error. A locking-resistant formulation and independent verification are needed |
| Pressure/flow and broader physiology | Research models and coupling infrastructure exist; native cubic EP is phenomenological | Human cellular, conduction, valve/circulation and whole-heart physiological qualification; earlier murine PV model still loses to its waveform baseline |
| Hospital workflow | Synthetic import/model contracts and operational workflows exist | Actual hospital access, reviewed exports and workflow qualification |
| Clinical prediction | No clinical accuracy qualification established | Qualified independent measurements and a prospective validation design |

## Engineering that still does not require patient data

1. Implement and verify a locking-resistant mechanics formulation: stability/pressure modes, incompressible patch/objectivity, consistent tangent and analytical refinement. Freeze numerical acceptance criteria before implementing it.
2. Extend scalable preconditioning and robustness tests beyond the current shell: mesh quality, material contrast, anisotropy, constraints and load ranges. Preserve failures and true-residual checks.
3. Extend the physiological model and verify conservation/coupling and model-specific benchmarks before claiming whole-heart prediction.

The remaining items above are substantial development work. They are not solved by more patient data or by a green CI run. Data/access-dependent qualification is a separate requirement.

[Detailed run history and evidence](NONPATIENT_COMPLETION.md). Compatibility matrix v10 advances only CardiMech to `4855bc13fe411e0dd904c7733e43535930b96ff1`; other v9 component pins are preserved.
