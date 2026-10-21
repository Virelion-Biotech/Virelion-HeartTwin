# Software completion using synthetic data

This change completes two software blocks without acquiring or consuming patient data:

1. Explicit named/reference-weighted BSP reciprocal operators, native propagation and immutable held-out metric evaluation, including a real native-registry capability and CLI entrypoint. The legacy standard-ECG path remains tested. See [SYNTHETIC_BSP_WORKFLOW.md](SYNTHETIC_BSP_WORKFLOW.md).
2. Bounded spatial mechanics assembly and residual-only Newton line searches in CardiMech [PR #4](https://github.com/Virelion-Biotech/Virelion-CardiMech/pull/4), pinned into HeartTwin's compatibility matrix version 8.

## Executed evidence
All numerical checks ran in the user's Edge Colab. Final implementation sources are HeartTwin `4ac8b01c30c6d746254ab7cea75019e6fee42bf4` and CardiMech `47753cb5141f07c27b6cc64d64aaab11f7630e48`; later evidence/docs commits preserve those bodies. CardiMech's subsequent Ruff formatting correction preserves all three changed Python syntax trees (static AST comparison); its final CI is checked separately.

- **195 HeartTwin patient-workflow regressions passed, no skips.**
- **100 CardiMech tests passed, no skips**, including CardiInfer integration.
- Changed-source compile and lint passed in both repositories.
- Independent synthetic 128-channel reciprocal/forward comparison, gauge invariance, explicit reference transformation, CLI and registry routes passed.
- Synthetic 3,078-node/12,288-tetrahedron stress comparison: peak traced assembly allocations approximately 94 MB to 14 MB for both material models. Residuals and fields matched exactly; largest tangent differences were 2.84e-14 and 4.26e-14.
- Source blobs were checked against exact published commits before final tests. The initial checkout refusal correctly protected uncommitted lint edits; the patch and stash were preserved before switching to the identical published source.

The [verified raw evidence archive](../validation/synthetic_completion_2026_10_21/synthetic_stack_completion_evidence.zip) retains logs, JUnit XML, stress results/generator, implementation copies and initial failures. SHA-256 `689ed652c865a40e7fd8c4067ccb8f9c96066d43213ffdc51b5568bbc91f8275`, Git blob `8e7faee23a99e11ef25bb3fb08a243881652ecb4`. Both repository uploads matched the Colab-computed blob. No successful local download event is implied.

## Diagnosed failures
| Failure | Cause | Resolution |
|---|---|---|
| Python venv creation failed | Colab ensurepip bootstrap returned nonzero | Use virtualenv in an isolated task environment |
| Ruff module could not locate executable | Inherited system-site module had no binary in isolated search paths | Install Ruff into the isolated environment |
| Patient mechanics capability unavailable | Isolated bin directory omitted from PATH | Set explicit task PATH; preserve product availability checks |
| CardiInfer integration skipped initially | Declared component absent from initial environment | Install exact stack requirement pins and rerun; final suites have no skips |
| New-source lint findings | Test import ordering and exception type | Correct imports and use TypeError for invalid electrode mapping type |
| Required CI formatter rejected new CardiMech files | Lint checks did not include the separate formatting gate | Applied exact Ruff 0.17.1 diff; static ASTs unchanged; retain failed runs 39633514655 and 39659960671 in CardiMech's format_correction.json |
| Evidence read timed out / truncated | Large single-line browser output exceeded read/transfer limits | Read visible DOM text in bounded chunks and verify the uploaded blob hash |

None of these setup/transfer failures establishes a clinical or component numerical defect. No pre-existing failed benchmark result was erased.

## What remains
| Area | Status after this change |
|---|---|
| Standard ECG and explicit BSP software contracts | Implemented and synthetically verified |
| Reference-aware frozen BSP scoring | Implemented; explicit held-out declarations do not prove independent acquisition |
| Spatial assembly memory | Reduced on the tested synthetic mesh; total solver memory is not qualified |
| Full KIT spatial capacity | Still unfinished: 5,000-node/20,000-tet guards remain; scalable solve and larger-domain convergence qualification are engineering work, not a patient-data-only blocker |
| Human pressure/flow and waveform accuracy | Requires qualified independent measurements; the earlier murine PV model still loses to its waveform baseline |
| EDGAR solve readiness | Actual export metadata, electrode indexing/order, reviewed geometry and a compatible conductor remain unqualified; this change does not assign defaults to those exports |
| Broader physiology / clinical use | Native cubic propagation is phenomenological, P1 mechanics may lock near incompressibility, and whole-heart clinical qualification remains absent |
| Real hospital workflow qualification | Synthetic contracts are tested; actual hospital/import/model qualification still requires the relevant access and data |

The new [Colab recipe](../notebooks/HeartTwin_Synthetic_Stack_Completion.ipynb) preserves implementation pins and synthetic generators. Compilation of its cells is recorded separately; constituent runs do not equal an end-to-end execution of a newly assembled notebook.
