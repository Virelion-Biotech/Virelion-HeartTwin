# Patient-coupled models and raw hospital imaging

This extension implements missing research software paths. It does not certify a
patient-specific model, supply private hospital access, or establish clinical accuracy.

## Spatial heart / circulation coupling

`patient.coupled.simulate` joins spatial active myocardium and closed-loop
circulation in one nonlinear solve. Its unknowns at each step are free wall
displacements, four cavity pressures, and eight circuit volumes. Equations enforce:

1. Finite-strain wall force equilibrium under fibre active stress and cavity pressure.
2. Exact correspondence of each geometric cavity volume to its circuit chamber volume.
3. Backward-Euler blood-volume balance in LA, LV, SA, SV, RA, RV, PA and PV.

Four ideal diode valves gate mitral, aortic, tricuspid and pulmonary flow. Vascular
pressure uses explicit compliance, unstressed volume and external pressure. The
four chamber pressures are solved as multipliers, not prescribed elastance curves.
The analytical coupled Jacobian includes follower-pressure, volume-gradient,
valve-flow and vascular-compliance derivatives. Newton line search rejects element
inversion and negative circuit volumes. The residual tolerances are explicit.

Electrical voltage is sampled by declared linear interpolation without time shifts
or extrapolation. Element-average voltage drives a declared sigmoidal activation
target with explicit rise/decay kinetics and element-specific maximum fibre stress.
The electrical source must match exact patient ID, mesh SHA, node order and frame.
These are phenomenological stress kinetics; no human cellular calcium model is implied.

The mechanics mesh must include reviewed fibre/sheet fields and four closed,
oriented, nonoverlapping cavity groups whose union is exactly the mesh cavity set.
The cavity manifest includes `subject_id`, `mesh_sha256`, `coordinate_frame`,
`reviewer`, and `faces` keyed LA/LV/RA/RV. Displacement constraints must remove all
six rigid-body modes while retaining cavity volume freedoms. The installed pinned
CardiMech constitutive/mesh/pressure routines are reused; their module byte hashes
are saved with the result.

Required request fields and a reproducible manufactured geometry are given in
`tests/test_patient_coupled.py::coupled_request`. All material coefficients, stress
kinetics, vascular parameters, resistances, initial volumes, pressure guesses,
time samples, constraints and reference-state rationale are explicit. A loaded
patient image is **not automatically a stress-free reference**. The reference-state
assumption must be reviewed and, for patient use, reconciled with prestrain/unloading.

Outputs contain displacement and Cauchy stress fields, element Jacobians, active
stress, pressure/volume/flow series, nonlinear residuals and source pins. The source
EP model is one-way coupled into the mechanics; mechano-electric feedback is not
implemented. The physical model is quasi-static compressible neo-Hookean P1 FEM
with prescribed fibre stress, ideal valves and lumped vessels. It does not implement
three-dimensional blood flow, valve inertia/regurgitation, pericardial contact,
Purkinje anatomy or ion-channel personalization. Near-incompressible P1 locking and
reference-configuration/model mismatch require independent numerical qualification.

## Personalization of the new models

`PatientDomainJob.native_capability` optionally selects:

- `patient.circulation.simulate` with domain `circulation` and explicit template
  backend `native-four-chamber-v1`.
- `patient.coupled.simulate` with domain `mechanics` and explicit template backend
  `monolithic-four-cavity-v1`.

Native calibration requires a frozen encounter. The existing CardiInfer bounded
MAP command loop performs inference, then an independent native forward replay is
saved before holdout observations are opened. Every holdout acceptance gate must
pass. Exact units and a physiology-only path allowlist protect parameter updates:
geometry, solver identity, times and patient sources cannot become fit parameters.
Coupled mesh/cavity/voltage artifacts must be uniquely pinned acquisitions in that
frozen patient case and usable under its encounter/holdout rules. Derived artifacts
must therefore be attached and reviewed in a new case revision before calibration.

Both solvers expose `pressure_mmHg.{chamber}`, `volume_mL.{chamber}` and
`flow_mL_per_s.{from}_to_{to}` arrays with `time_s`. These can be curve targets
using explicit `prediction_unit`. Scalar outputs include `{chamber}_final_pressure`
and `{chamber}_volume_range` with units. A range over an arbitrary transient window
is deliberately not called stroke volume. Fitting does not establish identifiability,
posterior coverage, transportability or clinical accuracy.

## Raw images and segmentation

`patient.images.segment` accepts:

- `json-volume-v1`: `values`, `intensity_unit`, and `voxel_corner_affine_m`.
- `nifti-scalar`: pinned NIfTI-1 image, declared mm and NIFTI_RAS coordinates.
- `dicom-series`: pinned single-frame CT/MR instances, explicit expected hospital
  patient ID, series UID, DICOM_LPS frame and HU/arbitrary intensity convention.

DICOM decoding checks patient/study/series/frame/modality identity, scalar pixels,
orthonormal orientation, consistent spacing and complete uniform slice positions.
It sorts by physical slice position, applies stored-pixel rescale, and constructs
the oblique/sheared physical voxel affine. Pixel array axes are column/row/slice;
DICOM PixelSpacing remains row/column. Duplicate phases/positions, unsupported
enhanced multiframe or intensity LUTs and inconsistent series fail instead of
being silently resampled or combined.

The built-in seeded method uses explicit per-region label, intensity thresholds,
voxel seeds and start-inclusive/stop-exclusive ROI bounds. It selects face-connected
threshold components containing those seeds and rejects overlapping labels. This
is reproducible conditional segmentation, **not autonomous anatomical recognition**.
Its label and provenance artifacts are marked `review_status=pending`. Reviewed
labels then enter `patient.geometry.reconstruct` using an exact-source review.

`patient.images.nnunet` provides learned inference from a local reviewed nnU-Net
model folder. Its pinned model-manifest source must declare every model file SHA,
exact installed `nnunet_version`, `trusted_by`, `training_provenance`, intensity
convention, folds, checkpoint name and allowed label IDs. The adapter supports
single-channel NIfTI models and requires explicit device, tile step, augmentation
and timeout. It checks all model files before and after inference and checks the
output shape, physical affine and label set. Credentials are excluded from the
inference subprocess through an environment allowlist. Neither model packages nor
weights are downloaded implicitly. Supplied checkpoints and custom trainer code
must be trusted; actual learned-model accuracy remains an external dependency.

The nnU-Net regression uses a synthetic process contract, not real trained weights.
The provided contract test therefore does not qualify an anatomical segmentation
model. Install an independently reviewed nnU-Net package/model on the worker for
real inference. Imaging extras are installed via `pip install -e '.[patient-native]'`.

Primary geometry/inference references:

- https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html
- https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/how-to/run-inference.md

## Hospital imaging retrieval

`hearttwin-patient hospital-images P1 config.json --output-dir imports
--expected-revision N --actor owner` retrieves explicitly selected DICOM instances.
The owner workbench also exposes `POST /patients/{id}/hospital-imaging-import`
with `expected_revision` and `config`. Provisioned operators/readers/reviewers
cannot invoke this credential-bearing network path.

Configuration declares an HTTPS `base_url`, exact study/series/SOP UIDs,
`dicom_patient_id`, optional worker `token_env`, encounter, acquisition ID, frame ID,
modality and intensity unit. The client rejects redirects, credential/query-bearing
base URLs, invalid UIDs, unexpected response formats and mismatched downloaded
patient/study/series/instance IDs. Both direct application/dicom and single-instance
multipart responses are accepted. Downloads are size/count bounded. Geometry must
pass before the case revision is saved with optimistic concurrency control.
Original files remain on the trusted worker, hash-pinned; returned metadata omits
hospital URLs and auth values. It does not enroll hospital OAuth accounts, search
unselected patient records or invent an endpoint/token. Hospital-specific setup and
authorization are still needed.

## Qualification status

New tests cover the coupled analytical Jacobian, active spatial contraction,
positive element volumes, cavity/circuit volume agreement, global blood-volume
conservation, native calibration gates, source/identity rejection, oblique DICOM
geometry, seeded connected components, nnU-Net contract integrity, multipart WADO
retrieval, owner-only network access and rejection of unbound plain URI/SHA inputs.

Assistant-run validation is restricted to the user's Edge Colab. No local tests
were run. The first implementation commit passed automatic CI and both component
integration workflows; those environments skipped imaging tests without optional
dependencies. After browser control recovered, the full Edge Colab run passed 170 tests with
zero failures, errors or skips, plus all six validation-script checks. The live
worker authenticated API smoke passed, but Edge blocked the official Colab proxy
with ERR_BLOCKED_BY_CLIENT. Its browser end-to-end review therefore remains
uncompleted; the synthetic worker was stopped afterward. There is no current clinical accuracy
or live-worker browser end-to-end qualification claim. The prior 145-test Colab
record belongs to PR #53 and must not be presented as validation of this extension.
