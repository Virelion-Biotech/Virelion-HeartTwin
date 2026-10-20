# Patient-centric workflow software evidence — 2026-10-20

Tested implementation: `a99794a21f3ee3d0b84cf58d48a46732b7cd558a`.
The source was run in the user's connected Edge Colab session, then repeated in
a fresh isolated Colab environment to retain a complete downloadable archive.
No patient dataset or private credentials were used.

## Colab

**123 passed, zero failures, zero errors, zero skips.** All six protocol checks
passed: Python compilation, patient/workbench tests, patient lint, gateway import
ordering, JavaScript syntax and geometry checks. The test suite includes native
scalar/curve circulation inverse loops, native FEM field replay on a pinned
synthetic mesh, measurement import HTTP endpoints, immutable revisions, access
scope, evidence leakage, visit carry-forward, native patient recipes and
calibration-to-simulation handoffs with failed-holdout stop gates.

`HeartTwin_Patient_Centric_Colab_Results.zip` contains the raw logs, JUnit XML,
source/component protocol, installed environment, hash manifest and a standalone
synthetic UI fixture. SHA-256:
`6240e44ae2ca2b3aec130e94e9ca2e738546c8f1b560c237e76d5b0671ee2e06`.
The ZIP and all **28 manifest-listed artifact hashes** were verified after transfer.
`colab_evidence.json` is the receipt with counts, timings, pins and ZIP hash.

## Automatic repository CI

All nine Python 3.10/3.11/3.12 jobs succeeded on the same implementation:

| Workflow | Run | Python 3.12 suite |
|---|---:|---|
| Base CI | 39407287576 | 256 passed, 55 skipped |
| Current-main full-stack integration | 39407287465 | 324 passed, 1 skipped |
| Frozen full-stack integration | 39407287442 | 324 passed, 1 skipped |

`ci_status.json` records all nine job statuses. Full Python 3.12 logs are retained
under `ci/`; component manifests and integration canary results are in those logs.
These are automatic PR checks; the assistant's requested validation runs used Colab.

## UI review and limits

The actual packaged HTML/CSS/app.js was rendered inside Colab with an explicitly
labelled, offline read-only synthetic fixture. Patient selection, visit selection
and task switching were inspected, including the resulting visit-bound workflow
configuration. `patient_ui.jpg` records the reviewed desktop screen.
`colab_pass.jpg` records the completed validation output.

This is not a live-worker browser E2E test. The live Colab workbench proxy previously
returned ERR_BLOCKED_BY_CLIENT and was not bypassed. Keep that deployment review
separate from the passing authenticated HTTP/JavaScript checks.

These results establish software/synthetic behavior only. They do not establish
patient prediction accuracy, clinical benefit, automatic image reconstruction,
lead-field preparation, general anatomical CFD/FSI or personalized regeneration
biology. Native solver restrictions and explicitly assumed/population inputs
remain visible. See `docs/PATIENT_LIFECYCLE.md`.
