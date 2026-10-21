# External pressure–volume challenge and EDGAR source follow-up

## What changed
Added `scripts/challenge_external_pv.py` and a Colab recipe. The script downloads six exact publisher Git blobs, records hashes and a protocol before opening measurement values, then fits HeartTwin's real chamber-pressure function on mouse 1. Mouse 2 and 3 values remain unopened.

Source: Colebank and Chesler, **Biventricular Interaction During Acute Left Ventricular Ischemia in Mice: A Combined In-Vivo and In-Silico Approach**, DOI https://doi.org/10.1007/s10439-023-03293-z . Repository https://github.com/mjcolebank/Colebank_2023_AcuteIschemia at `2da0cac9b13b1d3b6eb41e7a00ffde3c78e9c8a4`, BSD-3-Clause. Original KIT fits and benchmarks are unchanged.

## Executed challenge
The first numerical run completed in the user's Edge Colab at HeartTwin `0473163bc516edaab3a89c9c7a6dea4fe1e3676b`, Python 3.13.16, NumPy 2.1.3, SciPy 1.16.3. It directly invokes the production `chamber_pressure` function from main `3df859867660079430fafca84e55fe71d4e6c8cb`; no CardiFlow, CardiMech or CardiEP numerical solver is used.

Mouse 1 contains 50 phase-normalized samples per processed beat, with 50 LV and 50 RV beats; systemic pressure has 144 columns. The source catheter recordings are sequential between chambers. We use publisher units mmHg and convert microlitres to mL by /1000, with source cycle length 0.1034 s. The normalized phase axis is not a raw acquisition timestamp.

First 30 beat columns calibrate five prespecified families, next 10 select by pressure RMSE, first 40 refit the selected family, final 10 evaluate. Publisher all-beat means are never fitted: that would leak evaluation beats into training. Both chamber models and outer predictions are frozen before outer pressure scoring. Volumes from evaluated beats are inputs, so this is **conditional pressure prediction**, not a predicted volume trajectory. The 500 values per chamber represent 10 correlated processed beats of one animal, not 500 independent experiments or independent patient validation.

| Chamber | Conditional pressure RMSE (mmHg) | Training-waveform baseline RMSE (mmHg) | MAE (mmHg) | Bias (mmHg) |
|---|---:|---:|---:|---:|
| LV | 1.364515705155647 | 0.6452217698034626 | 1.1074189997090123 | -0.010320986666462197 |
| RV | 0.7621425826575355 | 0.2900619186737981 | 0.5713277337370785 | 0.22878403882678075 |

**Negative result:** both fits lose to the repeating training waveform. No predictive superiority or clinical pass is established. This useful baseline has no volume dependence, so its advantage does not identify a unique physiological cause or prove every use of the chamber law inadequate.

Both selected families are asymmetric **linear passive** models; the KIT nonlinear law was not assumed to transfer to another species. LV inner errors across symmetric-linear, asymmetric-linear and asymmetric kVref=1,3,6 are 1.74025, 1.42841, 1.59020, 2.07101, 2.79534 mmHg. RV errors are 0.95052, 0.69344, 0.72988, 0.89114, 1.22744. All 24 trials converged and selected refits have no active bound.

## Error diagnosis
Independent vectorized constitutive evaluation agrees with native results to 2.84217e-14 mmHg (LV) and 2.13163e-14 (RV). Source Git hashes match all six expected blobs. Pressure residuals vary by phase. These observations support formula and conversion consistency for this narrow challenge. They do not validate registry routing, a circulation trajectory, other component engines, flow, human pressure scale, or clinical accuracy.

The model fixes unstressed volume and external pressure at zero, uses restricted sinusoidal activation and treats publisher-normalized cycles as its phase convention. Those are explicit model assumptions and possible adequacy limits, not patient measurements. No new fit was selected against the outer errors. The pressure scale of the original KIT subject remains unidentified; mouse measurements cannot supply missing measurements for that human.

Initial lint reported import ordering and late-bound loop-variable warnings. Calls happened synchronously within each chamber iteration, so no numerical late-binding defect was demonstrated. Defaults now bind vref and chamber explicitly. Initial failure is retained. Final source `d9e6681b9a4ec96707aed3f7884162277156f536` completed replay with exit 0 in Edge Colab. The frozen-model JSON matched the initial run byte for byte (SHA-256 `6b40c54ccef54ce4eeed6f02688c87ca0241d060211157fb788f1ca5cdb805e7`). Final compile and lint both passed. Raw first-run/replay results, optimizer trials, frozen predictions, diagnosis, plot and failure/check logs are retained in [external_pv_evidence.zip](../validation/external_pv_2026_10_21/external_pv_evidence.zip). The transfer matched the Colab-computed Git blob hash. Archive SHA-256: `8763753eedf6fb80431984da9025b30ff19f8ebc034d2c3af3ee5008c09311e9`. The final consolidated recipe is not claimed to have run end to end solely from its assembly.

## New EDGAR path
On 2026-10-21 the user's signed-in Edge session still showed only Documentation and Meshes on the legacy P034 page:
https://edgar.sci.utah.edu/2025/07/10/bratislava_2020_p034/

The same live session exposed **2026 ECGI Data Challenge**:
https://edgar.sci.utah.edu/2026/05/02/2026-ecgi-data-challenge/

Its Bratislava training links are:
- Geometry: https://edgar.sci.utah.edu/datasets/2026_ECGI_Challenge/Training_Data/Bratislava_Data/Geometries/TrainingGeometries.zip
- Signals: https://edgar.sci.utah.edu/datasets/2026_ECGI_Challenge/Training_Data/Bratislava_Data/Signals/AllTrainingSignals.zip

The page describes torso BSP recordings, 12-lead ECGs, patient-number pairing, torso.leadlinks, and ts.pacingLoc as an ablation-derived PVC location. It does not establish that these are the same P034 acquisition, that CMS/WCT assumptions transfer, that coordinate units are known, or that ablation location is a complete invasive electrical map. The page inconsistently calls the collection four sources while listing five; Bordeaux is explicitly not included this year.

No challenge test data are downloaded by the supplied audit. Training archive validity, patient overlap, actual units/reference, channel identity, topology and redistribution rights must be checked before electrical solving. Raw EDGAR recordings/coordinates are not committed by this work. A listed waveform link is not a completed electrical validation. Edge control recovered on continuation. Both training archives were downloaded, hash-verified and audited for all nine subjects. The cohort does not contain P034. All nine have 128 BSP channels and six ECG leads; sampling/reference/unit decoding, leadlinks conventions and geometry qualification remain blockers. See [EDGAR_2026_TRAINING_AUDIT.md](EDGAR_2026_TRAINING_AUDIT.md) for results and raw metadata evidence.

## Remaining work and ownership
| Blocker | Responsible area | Required evidence |
|---|---|---|
| Human RV waveform, pressure/flow accuracy | Independent data and HeartTwin model | Qualified same-subject acquisitions and frozen evaluation protocol; external mice are a development challenge only |
| Conditional pressure model loses baseline | HeartTwin constitutive model / assumptions | Calibration-only candidate design, noise/bias model and evaluation beyond this processed acquisition |
| EDGAR waveforms and channel/reference mapping | Source + ElectroTrace/CardiAnatomy/CardiEP integration | Audit new training archives, explicit time/unit/reference contracts, then geometry-qualified forward tests |
| KIT spatial solve exceeds limits | CardiMech + anatomy/reference-state integration | Scalable assembly/solver or reviewed mesh hierarchy and convergence study; do not remove guards alone |
| Full patient lifecycle | HeartTwin plus all services and real hospital integration | Live-worker/browser review, real import and reconstruction qualification, new patient/condition evidence |

The model still lacks a validated noise model, uncertainty qualification and a fully personalized whole-heart demonstration. Software CI and small constitutive errors cannot close those gaps.
