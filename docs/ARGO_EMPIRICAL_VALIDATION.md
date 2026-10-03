# ARGO empirical EP validation

HeartTwin includes a blinded empirical validation adapter for the **ARGO** dataset:

- Orrù M, Baldazzi G, Zirolia D, Bertagnolli L, Viola G, Solinas MG, Pani D.
- *Annotated dataset of post-ischemic ventricular tachycardia electrograms (ARGO)*.
- PhysioNet version 1.0.0, DOI **10.13026/8gh2-e660**.
- Original article: PLOS ONE 21(6): e0350993 (2026).

ARGO v1.0.0 contains 1,962 synchronized mapping entries from nine post-ischemic ventricular-tachycardia patients acquired with CARTO 3. Each mapping record contains one bipolar EGM, two unipolar EGMs and a 12-lead surface ECG. The records are 2.5 seconds long at 1 kHz, and the dataset documentation states that only the last beat should be treated as reliable.

HeartTwin uses this dataset for **empirical observable validation**, not as proof of clinical validity.

## Why this is separate from synthetic recovery

Synthetic recovery asks:

> If the model family is true, can the inference machinery recover parameters?

ARGO asks a different question:

> Do model predictions agree with held-out measured electrical observations from real post-ischemic VT mapping procedures?

Both are required. Neither one replaces the other.

## Leakage-resistant study design

The adapter deliberately splits at the **raw mapping-record level** before targets are extracted.

```text
ARGO patient folder
      |
      +--> deterministic point split
      |       |
      |       +--> calibration IDs --> raw Pn WFDB --> calibration targets
      |       |
      |       +--> held-out IDs ----> kept target-blind during fitting
      |
      +--> predictions for held-out IDs only
              |
              +--> hearttwin argo-score
                       |
                       +--> reload raw held-out Pn files
                       +--> recompute measured LAT/ECG
                       +--> score predictions
```

This matters because `MESHcoloring.txt` is a reconstructed/interpolated CARTO map. Randomly hiding vertices from that same reconstructed map would not provide a genuinely independent measurement target.

## Supported raw files

For every patient folder the adapter validates:

- `XYZmesh.txt`: LV triangulated-mesh vertices;
- `ConnectivityList.txt`: one-based triangle connectivity;
- `MESHcoloring.txt`: voltage and LAT map coloring;
- `POS_POINTS.txt`: mapping-point coordinates;
- `AblationPoints.txt`;
- `Pn.hea` / `Pn.dat` WFDB records.

For the official ARGO v1 release it additionally checks the published point counts:

| Patient | Mapping points |
|---|---:|
| Pt1 | 157 |
| Pt2 | 104 |
| Pt3 | 90 |
| Pt4 | 471 |
| Pt5 | 46 |
| Pt6 | 839 |
| Pt7 | 76 |
| Pt8 | 129 |
| Pt9 | 50 |

The built-in direct WFDB reader is intentionally narrow: official ARGO v1 format-32 records, 15 signals, 1 kHz, 2500 samples, with the final 12 channels in the canonical ECG order.

## Validate downloaded data

```bash
hearttwin argo-validate /data/ARGODataset_Folder/Pt1
```

A nonofficial-count escape hatch exists only for test fixtures:

```bash
hearttwin argo-validate fixture/PtSynthetic --allow-nonofficial-count
```

Do not use that flag for a production validation run.

## Create a blinded split

```bash
hearttwin argo-prepare \
  /data/ARGODataset_Folder/Pt1 \
  outputs/argo/Pt1 \
  --holdout-fraction 0.20 \
  --seed 42
```

This writes:

- `split.json`: deterministic calibration/hold-out membership plus a portable `split_sha256`;
- `calibration-targets.json`: only calibration targets.

The held-out measurements are **not written** to the calibration artifact.

The split hash excludes the machine-specific absolute dataset path, so copying the identical patient folder to a different machine leaves the split identity unchanged.

## Signal-defined local activation time

The v1 empirical adapter uses an explicit and reproducible computational LAT definition:

1. detect the reliable final-beat surface-ECG reference using the maximum multilead RMS energy in the allowed final-beat window;
2. within ±200 ms of that reference, compute the bipolar-EGM first difference;
3. define local activation as the sample with maximum absolute bipolar derivative;
4. report LAT in milliseconds relative to the surface-ECG reference;
5. report a peak-to-background derivative ratio as a simple extraction-confidence diagnostic.

This is **not claimed to be identical to CARTO's proprietary LAT annotation**. Therefore HeartTwin treats the reconstructed CARTO LAT as a secondary concordance target, while the held-out raw signal-defined LAT is the primary target in this adapter.

For stronger scientific studies, compare multiple published LAT definitions and perform sensitivity analysis to the extraction rule.

## ECG observable

For each held-out record HeartTwin extracts the synchronized 12-lead ECG around the same reference:

- window: -350 to +350 ms;
- per-lead baseline subtraction;
- one global amplitude normalization across all leads, preserving relative lead amplitude;
- canonical lead order: I, II, III, aVL, aVR, aVF, V1-V6.

Predicted ECGs must provide the same 12-lead order and an explicit relative-time axis. HeartTwin interpolates predictions onto the measured clock and reports:

- mean lead correlation;
- median lead correlation;
- worst per-record lead correlation;
- globally normalized RMSE.

## Prediction contract

After fitting on calibration targets, write a prediction manifest containing **exactly** the held-out point IDs:

```json
{
  "schema_version": "hearttwin-argo-predictions-v1",
  "patient_id": "Pt1",
  "split_sha256": "<from split.json>",
  "records": {
    "P71": {
      "lat_ms": -12.4,
      "ecg": {
        "lead_names": ["I", "II", "III", "aVL", "aVR", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"],
        "relative_time_ms": [-350.0, -348.0, 0.0, 350.0],
        "values": [[0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0]]
      }
    }
  }
}
```

The abbreviated `values` array above only illustrates structure; real ECG rows must each contain exactly one value per time sample.

The scorer rejects:

- a different patient;
- a different `split_sha256`;
- missing held-out records;
- extra calibration records;
- non-finite LAT/ECG values;
- non-monotonic ECG clocks;
- incorrect ECG lead order.

## Score held-out data

```bash
hearttwin argo-score \
  outputs/argo/Pt1/split.json \
  outputs/argo/Pt1/predictions.json \
  --output outputs/argo/Pt1/holdout-report.json
```

Optional gates are supplied as JSON:

```json
{
  "lat_rmse_ms_max": 20.0,
  "lat_abs_bias_ms_max": 10.0,
  "ecg_mean_correlation_min": 0.80
}
```

Then:

```bash
hearttwin argo-score \
  outputs/argo/Pt1/split.json \
  outputs/argo/Pt1/predictions.json \
  --gates argo-gates.json \
  --output outputs/argo/Pt1/holdout-report.json
```

Gate thresholds are study-design choices and should be frozen before looking at the held-out results.

## Cohort-level protocol

The strongest use of ARGO is patient-level external validation, not pooling every mapping point as though the 1,962 records were independent subjects.

Recommended hierarchy:

1. lock extraction rules and acceptance thresholds;
2. for each Pt1-Pt9, create a deterministic within-patient calibration/holdout split;
3. fit the patient-specific model using only calibration data;
4. score held-out mapping points;
5. report patient-level metrics first;
6. aggregate across nine patients with equal-patient weighting or a predeclared hierarchical analysis;
7. report all failed/nonconvergent patients rather than silently excluding them.

Do not report a mapping-point-weighted average alone: Pt6 has far more records than several other patients and would dominate the result.

## Important current limitation

ARGO provides rich electrical recordings and an LV electroanatomical surface reconstruction, but it does **not** provide the volumetric, fibre-resolved ventricular anatomy currently expected by the CardiEP native model.

Therefore the new adapter does not fabricate fibres or a ventricular volume from the CARTO surface. To perform full patient-specific CardiEP fitting on ARGO, one of the following is required:

- independently registered CMR/CT anatomy for the same patient;
- a scientifically justified volumetric reconstruction plus fibre assignment whose uncertainty is explicitly modeled;
- an external EP backend designed directly for the available CARTO surface representation.

Until that exists, ARGO is an empirical electrical-observable validation target and a data-contract test—not a fully closed patient-specific HeartTwin reconstruction.

## Claim boundary

Passing an ARGO holdout study supports a statement such as:

> The model reproduced prespecified held-out electrical observables in the evaluated ARGO post-ischemic VT cohort under the declared extraction and scoring protocol.

It does not support statements of diagnosis, treatment guidance, clinical benefit, prospective performance, or generalization outside this small CARTO-3 post-ischemic VT cohort.


## Cohort automation

HeartTwin can prepare the official Pt1-Pt9 study without manual split bookkeeping:

```bash
hearttwin argo-cohort-prepare /data/ARGODataset_Folder outputs/argo-cohort \
  --holdout-fraction 0.20 --seed 42
```

This creates one blinded split per patient plus `cohort.json`. Predictions are
then placed under `<predictions-root>/PtN/predictions.json` and scored with:

```bash
hearttwin argo-cohort-score outputs/argo-cohort/cohort.json \
  outputs/argo-predictions \
  --gates argo-gates.json \
  --output-dir outputs/argo-reports
```

The cohort report is **equal-patient weighted**. It records every patient report
and the IDs of any patients that fail prespecified gates. HeartTwin deliberately
does not use a mapping-point-weighted aggregate as the primary cohort result,
because the ARGO patient point counts are highly imbalanced.


## Surface-Eikonal baseline

When only the ARGO CARTO surface is available, HeartTwin can now use CardiEP's
separate `surface-eikonal-v1` backend as an **observable-level baseline**. It
fits only the calibration mapping points, predicts activation time over the LV
surface, writes predictions for the held-out point IDs, and immediately invokes
the existing blind scorer.

Because the ARGO public documentation describes the 3-D coordinates but does
not state their physical unit on the dataset page, HeartTwin does **not** guess
the unit. The study operator must verify it from the source/export metadata and
pass it explicitly:

```bash
hearttwin argo-surface-baseline \
  outputs/argo/Pt1/split.json \
  outputs/argo/Pt1/surface-baseline \
  --coordinate-unit mm \
  --speed-min-cm-per-ms <prespecified-lower-bound> \
  --speed-max-cm-per-ms <prespecified-upper-bound> \
  --gates argo-lat-gates.json
```

The speed bounds are also explicit study inputs and should be frozen before
looking at held-out results.

This model is deliberately limited: isotropic surface conduction, a fitted
root, one global speed, and one timing offset. It does not model transmural
propagation, myocardial fibres, scar depth, Purkinje anatomy, or ECG forward
physics. It therefore provides a leakage-resistant ARGO LAT baseline, not a
replacement for volumetric patient-specific CardiEP.


### Full-cohort surface baseline

After `argo-cohort-prepare`, the same surface model can be run independently
for every patient:

```bash
hearttwin argo-surface-cohort \
  outputs/argo-cohort/cohort.json \
  outputs/argo-surface-cohort \
  --coordinate-unit <verified-unit> \
  --speed-min-cm-per-ms <prespecified-lower-bound> \
  --speed-max-cm-per-ms <prespecified-upper-bound> \
  --gates argo-lat-gates.json
```

This command does not pool calibration data between patients. It writes one
prediction manifest and holdout report per patient, then produces the same
equal-patient cohort aggregate used by `argo-cohort-score`.
