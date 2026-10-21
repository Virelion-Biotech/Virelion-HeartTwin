# EDGAR 2026 Bratislava training audit

## Outcome
The missing-waveform blocker has a new source path: the signed-in EDGAR 2026 challenge page exposes downloadable Bratislava training recordings and matching surfaces. Both archives were downloaded and numerically audited in the user's Edge Colab. **This is source/contract qualification, not an electrical accuracy result. No electrical solver ran.**

Page: https://edgar.sci.utah.edu/2026/05/02/2026-ecgi-data-challenge/
The legacy P034 page still lists only documentation and meshes. The new training cohort is **P001, P002, P004, P006, P010, P020, P023, P024 and P027**, not P034. No challenge testing files were downloaded.

| Archive | Bytes | SHA-256 |
|---|---:|---|
| Training geometries | 2,253,471 | dc112818adfcff90219a5f3886f8ad30f5a4adce542e21f3b89e8ae6845f6faf |
| Training signals | 9,204,106 | 824b4de2785fd8e29786854087f1a4143a1932aa9d71238abbe9be6060474462 |

The rerunnable script rejects changed archive hashes and enforces compressed/uncompressed size budgets. Raw source arrays and coordinates are not redistributed in the evidence. Preserve provider acknowledgement and verify applicable rights before any redistribution.

## Diagnosed contracts
- All nine subjects have 128 BSP channels, **six ECG channels**, and geometry. P001 has 1,001 samples. The ECG labels are I, II, III, aVL, aVR, aVF; do not invent precordial leads from the page's generic “12-lead” description.
- Sampling rate, amplitude unit and electrical reference are not exposed as readable scalar fields by the audited SciPy route. The note is a MATLAB MCOS string object, not decoded text. A failed JSON serialization of byte strings was corrected, but decoding that wrapper does **not** decode the underlying MATLAB note or recover its scientific meaning.
- All nine leadlinks arrays have the expected 128 entries, but their observed index ranges admit both zero- and one-based interpretation. Faces being provably zero-based does not independently settle the leadlinks convention. The page describes node/channel correspondence; an authoritative index-base and order check is still needed.
- Coordinate units remain unconfirmed. All supplied geometries are surfaces. HeartTwin's native reciprocal volume-conductor path requires a reviewed tetrahedral domain in metres, conductivity tensors, and its declared nine-electrode/12-lead contract. A 128-channel BSP recording cannot be passed into that interface by renaming it.
- The ablation-derived ts.pacingLoc is a localization target, not a complete invasive activation or potential map. It is not used to fit a source location in this audit.
- Every subject has at least one surface failing the local closed/oriented predicate. This does not mean every surface is unusable or every possible model needs all surfaces.

| Subject | Surfaces requiring local review |
|---|---|
| P001 | EndoEpi |
| P002 | EndoEpi, Llung, LV_AO, RV_PV |
| P004 | EndoEpi, torso |
| P006 | Llung, LV_AO, Rlung, RV_PV |
| P010 | EndoEpi, Llung, Rlung, RV_PV, torso |
| P020 | Llung, Rlung |
| P023 | EndoEpi, Llung, Rlung, RV_PV |
| P024 | EndoEpi, Llung, LV_AO, RV_PV |
| P027 | Llung, LV_AO, RV_PV, torso, EndoEpi |

For P001, Epi, torso, both lungs, LV_AO and RV_PV pass these local checks. EndoEpi has four duplicate triangles, ten overincident edges and maximum edge incidence four; there are no zero-area faces. No geometry was repaired, deleted or relabelled. Self-intersections, anatomical nesting, conductor meshing, conductivity and reference-frame review remain unperformed.

## Errors, fixes and ownership
| Finding | Classification | Action |
|---|---|---|
| Legacy P034 has no linked waveforms | External source availability | Found and audited a different nine-subject training cohort; no claim that P034 is fixed |
| Page says 12-lead but files have six | Source-description / integration mismatch | Preserve actual lead labels; no padding, synthesized V1–V6 or silent montage conversion |
| Note cannot serialize bytes | New audit helper error | Explicitly decode wrapper byte strings; retain failure record; opaque scientific note remains unresolved |
| Lint ISC004 for adjacent URL strings | Diagnostic formatting | Use complete URL literals; final lint passes |
| Missing rate, unit, reference and ambiguous leadlinks | Metadata / cross-repository interface qualification | Block electrical readiness; obtain authoritative note/metadata and mapping |
| Nonmanifold / duplicate-face surfaces | Geometry qualification | Preserve originals and exact statistics; no arbitrary repair |
| 128 BSP signals vs native standard ECG operator | HeartTwin / ElectroTrace / CardiAnatomy / CardiEP integration contract | Qualified BSP observation and forward-operator work still needed, or use a genuinely complete reviewed standard ECG acquisition |

No tested numerical engine defect is established by these data-format checks. Neither missing metadata nor geometric issues can be fixed by removing checks or assigning plausible defaults.

## Execution and retained evidence
Initial audit source: `1a4b025976a00d07b813eaf30df3cd1bb7d3034c`.
Final source: `86579ea4f2e04a4059aa1a33d4716e705104d5c8`.
Final compile, lint and full nine-subject audit replay all exited 0 in Edge Colab (Python 3.13.16, NumPy 2.1.3, SciPy 1.16.3). The [metadata evidence archive](../validation/edgar_2026_training_2026_10_21/edgar_2026_training_audit_evidence.zip) includes report, archive inventories, initial errors and final checks. SHA-256 `1e6a3e207dc97a5b820657c40f1f59ef7a078c71277ea0830e1983b991aaacc8`; Git blob `2fc0818c9899d4b0c97800215d36991e4956ff03`, verified against the Colab-computed blob hash after transfer. Human-readable JSON mirrors are alongside the ZIP.

Next concrete work requires decoding the publisher note with a supported MATLAB reader or getting an authoritative char/text export, confirming electrode index/reference conventions, reviewing the chosen conductor surfaces and building a compatible, qualified forward operator. No clinical or whole-heart validation claim follows from archive acquisition.
