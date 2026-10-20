# Patient infrastructure and execution

This adds a persistent patient workflow around the existing strict EP path. It does not establish patient prediction accuracy or complete a clinically qualified whole-heart twin.

## Implemented

- SQLite `PatientCaseStore`: immutable, hash-linked revisions; optimistic concurrency; revision-specific reviews; reversible archive/restore; immutable job/request bindings; revision comparison; source/evidence readiness.
- Patient workbench: case creation, acquisition attachment from uploaded files, evidence roles, case editing, revisions, reviews, comparison, archive/restore and patient run submission/results.
- `patient_ep` worker: scientific declared-experiment facade; byte/hash and duplicate-source leakage checks; durable prediction before downstream held-out evaluation; every holdout report retained; optional CardiTrace recording. Failed downstream stages retain earlier artifacts and the queue reports failure.
- `patient_domain` worker: explicit scalar mechanics/circulation targets, unit declarations, bounded MAP fitting through CardiInfer, fresh forward replay, downstream held-out residuals, source hashes and runtime source fingerprints. MAP is not a posterior and does not establish identifiability.
- Camps workspace preparation: complete supplied geometry arrays, matching reviewed bundle/source bytes, explicit coordinates, indexing and electrode ordering; versioned manifest and deterministic staging through CardiEP.
- Provisioned case-scoped reader/reviewer/operator credentials. Owner-only raw uploads, generic jobs and artifacts. No user self-registration or automatic access grants.
- CLI revision management, comparison and consistent SQLite backup.

## Start

Install the selected component revisions as well as HeartTwin. Patient EP requires CardiAnatomy, ElectroTrace, CardiEP and the declared external runtimes; default trace recording also requires CardiTrace. Domain fitting requires CardiInfer and the selected CardiMech/CardiFlow service.

```bash
pip install -e .
hearttwin-web --workspace /private/hearttwin-workspace
```

Open Patients after connecting the worker. Create a pseudonymous case, upload a source file under Data, and attach it as an acquisition. Assign each measurement a context/calibration/withheld-validation role. Enter model inputs with explicit personalization statuses. Select a case revision and supply the reviewed solver configuration before submitting a patient run.

The generated EP configuration intentionally has no default acceptance thresholds, anatomy or parameter map. Replace those fields with an experiment-specific configuration. A blank template must fail; it must never produce a convincing synthetic patient result.

## HTTP

All endpoints retain the gateway Host, Origin and bearer authentication checks.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/identity` | Current provisioned actor/role |
| GET | `/api/patients` | Assigned cases |
| GET/POST | `/api/patients/P1` | Read latest / save revision with expected_revision and reason |
| GET | `/api/patients/P1/revisions/1` | Immutable revision |
| GET | `/api/patients/P1/history` | Revisions and review/lifecycle events |
| GET | `/api/patients/P1/readiness` | Source metadata readiness and current-revision reviews |
| POST | `/api/patients/P1/attach` | Attach an owner-uploaded acquisition, creating a revision |
| POST | `/api/patients/P1/review` | Record decision/rationale for a revision |
| POST | `/api/patients/P1/compare` | Compare older/newer revisions |
| POST | `/api/patients/P1/archive` or `/restore` | Reversible lifecycle state |
| GET/POST | `/api/patients/P1/runs` | List / submit a revision-bound patient workflow |
| GET | `/api/patients/P1/runs/JOB` | Assigned patient job result |

Submit patient jobs with `payload.patient_id` and an explicit positive `payload.case_revision`. The server supplies the case and fingerprint. Attempts to substitute either are rejected. Existing generic job APIs also validate the supplied snapshot against the store.

Case revisions are append-only through the application API. SQLite hashes detect modified rows and broken links, but are not a substitute for independently retained backups or externally signed/timestamped audit heads.

## Domain observations

Each `ScalarTarget` links to acquisition-backed PatientCase evidence and one source file. The source must contain exactly:

```json
{"value": 82.0, "unit": "mmHg"}
```

A target specifies its exact model output path, matching unit, measurement noise SD and predeclared maximum absolute held-out error. Calibration/validation acquisition IDs and source content hashes cannot overlap. The forward template contains no observation arrays; target values are not injected into forward prediction. All fitted parameter paths must identify existing physiological values, with explicit bounds and units. Mechanics and circulation use their native request contracts, not a common guessed physiological model.

Current v1 scope is scalar endpoints. Curves, displacement-field likelihoods, correlated errors, posterior uncertainty and cross-domain physiological calibration require separate qualified extensions. The native source observation unit must match the declared target unit. The selected model's output unit must be reviewed against its native contract; available result unit maps are checked at evaluation.

## Camps workspace

```bash
hearttwin-patient-workspace reviewed-geometry-export.json /private/P1/workspace-v1
```

The JSON supplies `subject_id`, `anatomy_bundle`, `source_bundle_fingerprint`, `coordinate_frame`, `coordinate_unit`, `index_base`, `geometry_source`, `electrode_order`, and `fields` for every Camps geometry array. `geometry_source` is a hash-pinned JSON artifact in the reviewed bundle, containing the exact exported arrays under `fields`. The ten-electrode order is **LA, RA, LL, RL, V1, V2, V3, V4, V5, V6**, as used by the pinned upstream Camps ECG implementation. No rule-based fibres, scar, tags or electrodes are silently manufactured.

The output is a Camps manifest/staged workspace. It explicitly reports `opencarp_ready=false`; this command does not invent lead fields or run a segmentation model. A separately generated, checked heart/torso/electrode openCARP workspace is still required. The existing recorded-electrode profile confirms an ECG conditional on prescribed activation, not independently solved propagation.

## Lifecycle and deployment

```bash
hearttwin-patient --store /private/hearttwin-workspace/patients.sqlite3 list
hearttwin-patient --store /private/hearttwin-workspace/patients.sqlite3 history P1
hearttwin-patient --store /private/hearttwin-workspace/patients.sqlite3 compare P1 1 2
hearttwin-patient --store /private/hearttwin-workspace/patients.sqlite3 backup /backup/patients-2026-10-20.sqlite3
```

Back up the patient database, jobs database and worker artifact/upload volumes together. The CLI backup covers only the patient database. Retention expiry/purge is deliberately not automatic; archive preserves evidence and is reversible. New clinical encounters require new model revisions and new validation. Old predictions remain bound to their original inputs and software source fingerprints.

For provisioned access, set `HEARTTWIN_PATIENT_CREDENTIALS` to a JSON list whose entries have `token_sha256`, `actor`, `role` (`reader`, `reviewer`, `operator`) and explicit `patients` IDs. Store token SHA-256 digests, not plaintext tokens. Only the existing owner can use raw generic execution/upload/artifact endpoints. Reviewers can review assigned cases but cannot edit or execute. These credentials are removed from numerical subprocess environments.

**Operators are trusted compute users.** Scientific adapters invoke commands and read local files; case scoping is an application authorization boundary, not filesystem/container isolation between hostile tenants. Do not deploy this as a public multi-tenant service. Use private storage permissions, encrypted volumes, HTTPS, appropriately managed secrets and independently tested backups. Pseudonymous IDs and trace locator redaction do not de-identify medical source files or arbitrary metadata. Hospital identity mapping and access approval remain outside this store.

## Empirical gates still open

1. Obtain paired imaging/electrical measurements and independent anatomical review.
2. Select and qualify segmentation/meshing, runtime settings, torso/electrodes and lead fields.
3. Run a frozen patient case through calibration, unconditional prediction and prespecified holdout evaluation; compare against baseline models.
4. Repeat with patient-disjoint evaluation for cohort generalization; calibrate uncertainty and assess model mismatch.
5. Extend mechanics to registered curves/fields and measured loading; extend flow to anatomical CFD only with appropriate geometry, boundary conditions and numerical/empirical checks.
6. Validate intervention response and longitudinal prediction before treatment-effect claims.

The Colab lifecycle notebook runs software and optional synthetic domain checks. It cannot satisfy these empirical gates by itself.

## Patient-native workflow completion

The workbench now records visits/physiological state, imports scalar and time-series
measurements, edits provenance-bearing model inputs, previews input bindings and
assembles a 0D circulation task from case inputs. Every action creates an immutable
case revision. Import accepts exact `time_s,value` CSV or JSON with explicit units:
`{"value":80,"unit":"mmHg"}`, `{"values":[80,85],"time_s":[0,0.5],"unit":"mmHg"}`,
or labelled fields using `labels` instead of `time_s`. Original and normalized
files remain separately hash-pinned. There is no implicit unit conversion.
This is a measurement-file import, not an EHR/FHIR or DICOM segmentation importer.
Imaging/ECG acquisitions continue through the installed CardiAnatomy/ElectroTrace
adapters and their existing explicit metadata/readiness gates.

`patient_plan` binds a visit, frozen case revision and ordered native service stages.
Supported scientific stages cover anatomy, ECG/motion analysis, EP, mechanics,
flow, therapy, cellular/twin simulation and imaging/safety adapters. Availability
is checked against the worker; adding orchestration does not install missing
models or broaden a solver's scientific scope.

Each template declares `subject_id` and uses these explicit bindings:

- `{"$input":"input-id","unit":"mmHg"}` copies a case input only with matching units.
  Optional `path` selects an explicit child of a structured input, e.g. `values`.
- `{"$source":{"acquisition_id":"mesh-acq","file_id":"mesh","kind":"volume_mesh"}}`
  passes a hash-verified case source in the native artifact contract.
- `{"$result":"mechanics.outputs.0"}` passes an exact preceding-stage result.

Every numeric literal requires a `literal_rationale` keyed by its exact template
path, e.g. `settings.cycles`. Every stage also needs measured/estimated patient
input, a patient artifact, or a preceding patient-stage result. Assumed and
population inputs are preserved as such in the personalization manifest.
UNKNOWN inputs, unit mismatches, withheld/excluded inputs, duplicate holdout
content, unbound artifact references and forward references are rejected.
Acquisitions from another or unknown visit require an explicit `carry_forward`
rationale keyed by acquisition ID. No previous fit is automatically inherited
when evidence or physiological state changes. The longitudinal comparison states
which inputs/visits/evidence changed and requires an explicit new run.

The circulation builder requires uniquely named circulation inputs:
`proximal_resistance`, `distal_resistance` (mmHg*s/mL), `compliance` (mL/mmHg),
`distal_pressure_mmHg`, `initial_capacitor_pressure` (mmHg), and `inlet_flow`
(mL/s, a structured curve with `values`/`time_s`). It supplies no physiological
defaults. Required native fluid contract constants are marked as not consumed by
the 0D model. Its case reference is not a patient geometry or a claim of CFD.

`patient_domain` now accepts `observation_kind: curve` with `model_time`, or
`field` with `model_labels`, in addition to scalar targets. Curve fit axes come
only from calibration acquisitions. The command forward adapter samples model
outputs with explicit linear interpolation; extrapolation and automatic phase
shifts are forbidden. Fields require exact label correspondence. Native output
units must match; a backend without unit metadata requires `prediction_unit`.
Held-out files are opened only after independent prediction is durably written;
acceptance requires every held-out sample to satisfy the predeclared absolute
error bound. Reports also include RMSE and maximum absolute error. MAP remains a
point estimate; fitting does not establish identifiability or posterior coverage.
Forward templates may use the same `$input`/`$source` bindings when an encounter is
selected. Mechanics anatomy must be a unique hash-pinned artifact in that case.

### Engineering and scientific boundaries

The stack has patient-bound orchestration and provenance across these native
service routes. This does not mean every input is measured or every backend
consumes full anatomy. Automatic image reconstruction, patient lead-field
construction, general anatomical CFD/FSI, personalized cellular/regeneration
biology, and validated patient-benefit prediction are not supplied by this change.
They require qualified models/data beyond an orchestration layer. Native mechanics,
flow and intervention restrictions continue to apply. Configuring specialized
EP/mechanics/therapy stages still requires a reviewed native solver template.

Native FEM field targets can declare `field_quantity` (`displacement_x/y/z`,
`green_strain_xx/yy/zz/xy/xz/yz`, or `cauchy_stress_xx/yy/zz/xy/xz/yz`) and
`field_anatomy_sha256`. These read and verify the emitted `spatial_mechanics_fields`
artifact and compare only explicit `node:N`/`element:N` labels on that exact pinned
mesh. They do not register an image or infer correspondence to a different mesh.

A service plan may also include `patient.calibrate` or `patient.ep` composite
stages. Their templates contain the corresponding domain/strict-EP configuration
plus `subject_id`; case data, revision, fingerprint and visit are inherited from
the parent plan and cannot be substituted. `patient.calibrate` forwards the
parent visit/carry-forward declarations into domain execution; `patient.ep`
checks the anatomy and fit acquisitions against that visit. A downstream binding
such as `{"$result":"fit.inference.diagnostics.best_parameters.r"}` uses the
calibrated parameter directly. Each composite keeps its own durable prediction,
evaluation and binding artifacts. This connects calibration to later native
mechanics, flow or intervention stages without manually retyping a fit.
A composite calibration's declared holdout acceptance must pass before later
stages execute. Failed results remain durable for review; a failed fit is not
silently promoted into an intervention comparison.
