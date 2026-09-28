# Repair audit — 28 September 2026

## Scope and interpretation

Reviewed the 23 repositories available under Virelion-Biotech, refreshed upstream changes, reproduced failures, and exercised installed components together on Python 3.12. This is a code/integration audit, not an exhaustive proof of correctness or a biological validation study. Passing synthetic fixtures establishes software behavior only. Newer upstream fixes were retained rather than overwritten.

## Confirmed defects repaired

- **HeartTwin evaluation integrity:** requires caller-supplied reference labels; no substitution of predictions for missing truth. Features must be explicit and cannot include known outcome aliases or identifiers. Benchmark splits are frozen before fitting, biological groups cannot cross partitions, and evaluation requires exactly the held-out sample IDs. These checks cannot certify the scientific provenance of labels or exclude all confounding.
- **HeartTwin integration:** repaired Atlas/Bench payload mismatches and CardiVex API imports/baseline construction. The packaged service registry works outside the checkout. Large adapter payloads use stdin instead of exceeding environment-variable limits. All six command adapters support this protocol.
- **HeartTwin interpretation:** removed classifier-score-based disease-preset selection. A simulation preset is required. Vex domain names now match its actual vocabulary and map named CardiSim variables, including the initial state, rather than repeating an aggregate health score with arbitrary coefficients. Mapping evidence is extrapolated; the fabricated uncertainty value was removed. Classifier evaluation does not promote a simulated state to validated.
- **CardiBench:** explicit held-out groups produce exact partitions; overlap, duplicate/blank sample IDs, missing/extra assignments and invalid partitions are rejected. New materialization hashes bind individual sample metadata, not only aggregate label counts. Existing archived manifests are not silently regenerated.
- **CardiBridge:** JSON consumer results are persisted with completion, allowing duplicate delivery to recover the original result across router restarts. HeartTwin uses deterministic message identity and rejects receipts without actual Vex observations. Non-JSON handlers retain legacy duplicate-receipt behavior.
- **MyoTrace:** fixed a double inversion that assigned the fetal endpoint higher maturity when the adult reference was numerically lower. Linear and logarithmic descending references have regression coverage.
- **Intelligence:** repaired queries referring to absent SQL columns; checkpoint writes occur after database commit and failed adapters roll back their cursor advances. State is tied to the database and exact query window. Partial failures no longer report complete success. Evidence validation rejects excerpts absent from supplied text and full-text depth claims when only metadata/abstract text is available. This is an excerpt-grounding check, not automated proof that a claim follows from an excerpt.
- **CardiAtlas:** corrected the FTP bucket for early GEO series accessions.
- **Autocrawler:** preserved inline XML text in PubMed titles/abstracts, including negations that were previously truncated. Added an offline regression test and CI.
- **Operational checks:** fixed optional-dependency test assumptions in CardiAgent/DCCP, added CARDIAC-BREACH's lockfile and reproducible npm installs, and added CI for existing Biosafety-Assessment tests. HeartTwin's bootstrap no longer hard-resets service checkouts. CDT checksums stream data rather than reading whole archives into memory.

## Verification and coverage

| Repository | Verification / disposition |
|---|---|
| HeartTwin | 37 tests passed, 1 external CDT test skipped; real command adapters exercised with synthetic ECG/video files; wheel built and packaged registry loaded outside checkout. |
| CardiAtlas | 87 tests passed, including early-series URL regression. |
| CardiBench | 36 tests passed, including split and fingerprint regressions. |
| CardiBridge | Full suite passed, including durable result recovery after restart. |
| CardiTrace | Full suite passed; large trace payload exercised through HeartTwin. |
| CardiEval | Full suite passed with warnings on degenerate synthetic resamples; upstream audit-tool fixes retained. |
| CardiLearn | 121 passed, 7 optional tests skipped; no research-stage implementation changes. |
| CardiSim | Full base suite passed; optional tests skipped. Newer upstream simulation/test fixes retained. |
| CardiVex | 141 tests passed; synthetic translation coefficients remain uncalibrated. |
| CardiStudio | Full suite passed; required coverage gate passed. |
| DCCP | 75 tests passed with CardiSim installed. |
| CardiAgent | 42 passed, 5 skipped without the optional ML stack. ML CI includes the relevant artifact tests. |
| ElectroTrace | 209 tests passed after installing its optional test dependencies. |
| MyoTrace | 18 tests passed; configured F-rule lint passed. |
| OptiCell | Full suite and Streamlit page tests passed after installing declared UI dependencies. |
| CardioScore | 393 passed, 1 skipped with optional mixed-effects dependencies installed. |
| Intelligence | Full suite plus new database/checkpoint/source-grounding regressions passed; configured lint passed. External discovery services were not live-crawled. |
| Biosafety-Assessment | 22 tests passed; this is not an independent validation of biosafety recommendations. |
| CARDIAC-BREACH | Clean npm install, syntax checks for 44 JS files, 2 unit tests, and build passed. Local browser tests blocked by a corrupt Chromium download. GitHub Pages deployment reaches configure-pages but fails with 404: Pages is not enabled/configured for Actions; repository administration is required. |
| Autocrawler | PubMed inline-text regression passed. Live crawling, geocoding and paid extraction were not executed. |
| VB-101 | Discovery scaffold reviewed; phase scripts/workflow are not a completed executable discovery pipeline. |
| VB-204 | Research scaffold reviewed; README corrected to distinguish planned layers from implemented software. |
| .github | Organization documentation; no executable product tests. |

## Remaining product and scientific gaps

1. **Patient-specific calibration is not implemented in the reviewed multimodal workflow.** File-backed specialist outputs are recorded, but are not used to fit the simulation. A successful run does not demonstrate that a patient's physiology has been reconstructed.
2. **Shared identity/alignment remains caller-supplied.** The workflow does not establish that ECG, video, omics and clinical records belong to the same subject/time point or have compatible units and measurement conditions. The input schema and provenance need an explicit alignment/quality gate before meaningful multimodal inference.
3. **End-to-end empirical validation is still missing.** Tiny synthetic classification fixtures are integration checks. Their scores and bootstrap intervals do not establish external cohort performance or patient-level uncertainty. MyoTrace fusion confidence and CardiVex translation weights are heuristic, not empirically calibrated confidence estimates.
4. **Published CDT numerical equivalence has not been established by this pass.** The large-download memory defect is repaired; the external reference dataset/personalisation/comparison workflow still needs a successful complete run. No numerical equivalence claim follows from the base test suite.
5. **VB-101/VB-204 need substantial research implementation and evidence.** Empty/stub layers cannot be repaired by inventing parameter values, simulation results or target rankings. Their specified scientific gates remain outstanding.
6. **Live deployment behavior remains partly untested.** Hosted endpoints, authenticated external harvesters, optional GPU/ML paths, and CARDIAC-BREACH browser gameplay need their own environment-specific verification. GitHub matrix results must be checked separately from the local Python 3.12 results above.

The immutable component revisions in `requirements-services.txt` identify the software combination exercised here. They do not lock every transitive dependency or certify biological performance.
