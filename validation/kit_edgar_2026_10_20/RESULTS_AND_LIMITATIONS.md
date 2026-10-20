# KIT and EDGAR real-data results — 20 October 2026

These are two separate human subjects. KIT supports an exploratory volume-fit benchmark and a mesh compatibility audit. EDGAR P034 supports a surface/electrode audit; its inspected download page does not supply the documented waveform recordings. Neither establishes clinical accuracy or a fully personalised whole-heart model.

## What was executed

All numerical work ran in the user's Edge Colab. Source `351aaa776f414087afb0fd49575080805e34ff81`; CardiMech `5dc14d886ccbda5a1398b863f9ae7821bbffa18b`. The initial KIT run used `492a83a2a3b4f9627892f77ff7f958a72ffe511e`. The later source removes the obsolete initial EDGAR login status and adds the independent EDGAR audit; the KIT fitting method, bounds, starting points and holdout partition are unchanged.

The reusable notebook is [HeartTwin_KIT_EDGAR_Real_Data.ipynb](../notebooks/HeartTwin_KIT_EDGAR_Real_Data.ipynb). Retained logs, numerical outputs, source receipts, assumptions and hashes are in [validation/kit_edgar_2026_10_20](../validation/kit_edgar_2026_10_20/).

## KIT: real measurements, conditional reduced circulation

Source: [Gerach et al., Zenodo 10526554, version 1.1](https://zenodo.org/records/10526554). Publisher MD5 checksums and independent SHA-256 hashes were verified for Data.zip, LabelIDs.txt, M.vtu, Surfaces.zip and TetGen.zip. EP.vtu was not needed for this mechanics/circulation audit. Source licence is CC BY-NC 4.0; source archives are not redistributed here. Derived measurement files retain that attribution and noncommercial restriction.

The source is one male volunteer, not a disease cohort. There are 35 finite LV endocardial-volume samples, 35 epicardial enclosed-volume samples, and only two RV endocardial-volume samples. Epicardial enclosed volume is not blood-pool volume. AVPD and AVPV traces retain their own time grids; regional wall-thickening data have 17 labelled segments. Missing RV values were not filled. No phase shift, time warping or assumed simultaneous acquisition was introduced.

### Fit and holdout

The benchmark calls HeartTwin's actual `simulate_closed_loop` implementation: eight closed-loop compartments, elastance chambers, compliant vessels and ideal diode valves. It does not use the 3D spatial mechanics solver.

The first 23 LV samples are calibration data; the final 12 samples are a temporal holdout. Initial LV and RV volumes are supplied measurements. The second RV volume is excluded from fitting and evaluated as an additional chamber target. Two fixed optimiser starts are compared by calibration SSE only, then predictions are saved and hash-frozen before the scoring code opens the holdout values. No hyperparameters or model assumptions are retuned to improve the holdout result.

This is a retrospective split after inspection of the complete raw table, not preregistered or prospectively blinded validation. Samples are correlated and belong to one acquisition/volunteer. The standalone research runner does not claim to have passed the stricter PatientCase shared-source evidence gate: calibration and holdout derive from one publisher archive.

Only three parameters are fitted: LV maximum elastance, LV activation duration, and LA-to-LV resistance. All remaining chamber/vessel parameters, other initial compartment volumes, valve resistances and the 1.3-second cycle length are explicit analyst assumptions, not patient measurements. No burn-in or periodic steady-state condition is asserted. This limited experiment tests one conditional configuration, not all possible personalised calibrations.

| Quantity | Result |
|---|---:|
| LV calibration RMSE, 23 samples | 13.72774 mL |
| LV temporal holdout RMSE, 12 samples | 22.86855 mL |
| Persistence baseline holdout RMSE | 8.06134 mL |
| Maximum LV holdout absolute error | 35.76061 mL |
| Withheld RV volume observed | 65.938406 mL |
| Withheld RV volume predicted | 87.810397 mL |
| Withheld RV absolute error | 21.871991 mL |
| Total-volume conservation drift | 1.82e-12 mL |
| Maximum LV change with tighter integration tolerances | 0.000626 mL |

The reduced model is worse than persistence on the declared temporal holdout. Both optimiser starts settle near the same fitted values, with mitral-edge resistance at its imposed lower bound (0.001 mmHg s/mL). Optimiser termination is not proof of a uniquely identified or physiologically correct fit.

### Pressure nonidentifiability

Multiplying all chamber elastances, external pressures and edge resistances by the same positive factor, while dividing vessel compliances by that factor, leaves volume dynamics unchanged. The numerical checks at factors 0.5 and 2 reproduce exactly the same volume trajectories at stored precision, despite LV peak pressures of approximately 76.07 and 304.29 mmHg. These are alternative model outputs, not patient pressure measurements.

Volume-only fitting therefore cannot establish absolute pressure, elastance and resistance scales in this model. A tighter integration result and excellent volume conservation establish numerical consistency only; they do not fix physiological nonidentifiability. No confidence intervals, posterior uncertainty or clinical acceptance threshold are claimed.

### Spatial mechanics compatibility

The supplied official TetGen mesh contains 32,505 nodes and 128,976 tetrahedra; all tetrahedral determinants are positive. The current CardiMech loader rejects the size preflight with `Mesh requires 4..5000 finite 3D nodes`; its element ceiling is also 20,000. No limits were disabled.

All four supplied cavity STL surfaces have two faces per edge and consistent local orientation. Their geometric enclosed volumes are approximately LA 70.409, LV 200.563, RA 98.548 and RV 188.512 mL. This does not establish cavity-to-wall registration or expert qualification. The LV mesh cavity volume differs from the first cine volume (221.645 mL); the mesh is derived at diastasis and must not be silently treated as an unloaded end-diastolic reference.

The rounded TetGen fibre/sheet vectors have a maximum normalised absolute dot product of approximately 1.35e-5, above the current mechanics contract's 1e-10 orthogonality tolerance. Some sheets are not unit length. This is a conversion/qualification requirement, not proof the publisher's model is wrong. No vectors were silently repaired. `meshio 5.3.5` failed to read M.vtu's compressed encoding; the accompanying official TetGen export was audited instead.

A full coupled run still needs a scalable, reviewed mesh conversion; chamber/material/boundary mappings; qualified fibre/sheet handling; constitutive parameters; a defensible loaded reference state; circulation initial conditions; and an activation/voltage model. Spatial displacement, AVPD, regional thickening, stresses and ECG were not predicted. No mesh-convergence claim is made.

## EDGAR Bratislava P034

Source: [signed-in P034 dataset page](https://edgar.sci.utah.edu/2025/07/10/bratislava_2020_p034/). The page was inspected in the user's authenticated Edge session. Its two download links, Documentation-7.zip and Geometries-7.zip, were downloaded and SHA-256 pinned. The archives contain seven surface MAT files, electrode positions, interpolation mappings, a numeric pacing-site position and supporting documentation/images.

The documented recordings are 128 torso channels plus limb signals R/L/F, 1 kHz, mV, referenced to CMS. The README explicitly distinguishes CMS from Wilson's central terminal. Run1 is RV-apex pacing at 100 bpm; Run2 contains spontaneous PVCs. These facts describe recordings that were not present in either downloaded archive.

The page lists no waveform download. The audited MAT files contain only surface `pts` and `fac`, not `potvals`. No unlisted URL was guessed, and no simulated waveform was substituted. Consequently ECG error, beat generalisation and pacing-site localisation accuracy remain **not run**, with absent metrics rather than invented zeros.

The audit preserves explicitly documented zero-based face and triangle indices. It checks edge incidence/orientation, duplicate and degenerate faces, 128 numbered electrode positions, and the supplied interpolation operator. The publisher's mapping illustration uses the second triangle vertex as origin: `position = p2 + a*(p1-p2) + b*(p3-p2)`. This gives weights `[a, 1-a-b, b]` in the published vertex order; no permutation or registration is fitted.

The electrode coordinate residual is RMS 0.00112576 and maximum 0.00323834 native coordinate units. All 128 rows have nonnegative barycentric weights; the maximum constant-field preservation error is 1.11e-16. This checks the supplied interpolation operator, not a physiological forward model.

| Surface | Edges without exactly two faces | Inconsistent directed edges | Zero-area triangles |
|---|---:|---:|---:|
| EndoEpi | 6 | 12 | 8 |
| Epi | 0 | 0 | 2 |
| Right lung | 2 | 4 | 0 |
| Left lung | 1 | 2 | 0 |
| Torso | 0 | 0 | 0 |
| RV_RA | 3 | 6 | 0 |
| LV_LA | 0 | 0 | 0 |

No duplicate indexed triangles were found. The incidence/orientation counts can describe related defects and must not be added as independent defect totals. The audit does not establish absence of self-intersections, correct nesting, image registration or source accuracy. These surfaces need qualification before assuming a valid conductor domain; no repair was applied.

Quantitative topology and coordinate-residual results are retained in EDGAR_status.json. Coordinate units are not explicitly declared in the inspected P034 MAT/readme metadata, so residuals and volumes remain in native units. No metre/mm conversion, standard V1–V6 montage, or channel alignment is invented.

These surfaces do not directly supply the tetrahedral torso/myocardium conductivity model required by HeartTwin's reciprocal FEM lead-field implementation. The readme identifies the pacing position from CT; the broader catalogue's MRI description is insufficient to establish the modality of every supplied geometry. Run2 ablation reference is verbal plus a CARTO image, not a precise numerical target. No cine mechanics or chamber pressures were found in the downloaded files.

EDGAR attribution: Institute of Measurement Science, Slovak Academy of Sciences and National Institute for Cardiovascular Diseases, Bratislava; Aras et al. 2015, doi:10.1016/j.jelectrocard.2015.08.008; Svehlikova et al., CinC 2019, doi:10.22489/CinC.2019.303, and the additional references on the source page/readme. Source archives/images/meshes are not redistributed; no explicit licence identifier was supplied in the inspected materials.

## What resolves the remaining gaps

1. **KIT:** improve the model only against calibration data or a new prospectively defined experiment; preserve this failed baseline. Qualify spatial conversion, reference state and boundary conditions before claiming mechanics validation. Add pressure/flow measurements or constrained independent evidence to address scale ambiguity.
2. **EDGAR P034:** obtain Run1/Run2 waveform arrays and their montage/time/reference metadata from the maintainers. Confirm geometry units and conductivity assumptions, then freeze a beat/site/channel split before fitting. A screenshot of a CARTO ablation is insufficient for a numeric location-error claim.
3. **Both:** use new subjects/conditions for independent validation and uncertainty assessment. Do not combine these two people's measurements into one patient.

No hospital integration, trained segmentation model, clinical benefit or whole-heart predictive accuracy was validated by this work.
