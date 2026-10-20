# Patient acquisition and spatial preparation

PR #52 merged as `d17f796c925c32c111f88350a5edb17ae7c136b5`.
This follow-on adds executable research paths. It does not establish a fully
personalized whole-heart model or clinical accuracy.

The subsequent [coupled-model extension](PATIENT_COUPLED_MODELS.md) adds raw-image
processing, configured DICOMweb retrieval and spatial four-cavity mechanics/circuit
coupling. Its full Edge Colab validation is tracked separately and is still pending.

Install `pip install -e '.[patient-native]'`. The CLI
`hearttwin-patient-prepare CAPABILITY request.json` and registry `PatientNative`
expose `patient.geometry.reconstruct`, `patient.leadfield.generate` and
`patient.spatial_ep.simulate`, plus `patient.circulation.simulate`. These are also accepted patient-plan stages, so
`$source`, `$input`, `$result`, encounter, holdout and revision gates apply.
Stage outputs are content-addressed JSON files. Patient plans choose stage output
directories on the worker and hash-check returned artifacts.

## Hospital observations

The owner workbench has an uploaded FHIR Bundle importer at
`POST /patients/{patient_id}/hospital-import`, accepting `expected_revision`,
`upload_id`, and `mapping`. The trusted worker can also collect a hospital Bundle:

```sh
hearttwin-patient --store patients.sqlite3 hospital-import P1 mapping.json \
  --url 'https://hospital.example/fhir/Observation?patient=local-id' \
  --token-env HOSPITAL_FHIR_TOKEN --output-dir hospital-imports \
  --expected-revision 3 --actor owner
```

Alternatively use `--bundle export.json`. No hospital endpoint, account or token
is configured by this change. The client only performs HTTPS GET, bounds page
count/response size, refuses redirects and credential-bearing URLs, and refuses
next-page links outside the same origin/resource path. Pagination must finish
before any case is saved. No tokens or endpoint URLs are written to reports.

Example reviewed mapping (codes here are synthetic, not terminology guidance):

```json
{
  "patient_reference": "Patient/hospital-p1",
  "encounters": {"Encounter/visit1": "baseline"},
  "observations": [{
    "observation_id": "pressure", "acquisition_id": "pressure1",
    "evidence_id": "pressure-e1", "code_system": "http://loinc.org",
    "code": "synthetic-pressure", "unit": "mm[Hg]", "model_unit": "mmHg",
    "variable": "outlet_pressure", "role": "context", "modality": "pressure",
    "domain": "circulation", "input_id": "pressure-input"
  }]
}
```

An optional `component: {"system":"...","code":"..."}` selects exactly one
Observation component; the separate `code_system`/`code` must match that component.
Only final/amended/corrected exact finite valueQuantity observations are admitted.
Inequalities, preliminary measurements, missing/mismatched subjects or encounters,
unmapped units and incomplete pages fail. No terminology inference occurs.
The only spelling alias is explicitly requested UCUM `mm[Hg]` → solver `mmHg`;
no numerical unit conversion is performed. SampledData, DiagnosticReport extraction,
imaging/PACS retrieval, OAuth enrollment and streaming subscriptions are not implemented.
Identical resource/mapping reimport is idempotent; changed content requires a new
acquisition ID and immutable case revision. Source Bundle and normalized quantity
remain hash-pinned. A Bundle shared by fit and holdout acquisitions deliberately
triggers existing shared-source leakage protection: import separately partitioned
exports before constructing a calibration workflow.

FHIR contract: https://www.hl7.org/fhir/R4/observation.html

## Reviewed image reconstruction

Request fields: `subject_id`, `source` (local URI/SHA), `format`,
`coordinate_frame`, explicit integer `tissue_labels`, disjoint `cavity_labels`,
`review: {source_sha256, reviewer, rationale}`, and `output_dir`.
`format=json-labels-v1` accepts a source JSON containing a three-dimensional
`labels` array and `voxel_corner_affine_m` (4×4 affine, grid corners → metres).
`format=nifti-labels` accepts reviewed NIfTI-1 integer labels; header units must be
mm and frame explicitly NIFTI_RAS. Its voxel-center affine is shifted by half a
voxel to obtain corners before conversion to metres. gzip is supported.

Each selected voxel becomes six conforming tetrahedra with shared grid nodes,
physical affine coordinates and positive orientation. Outputs include a native
CardiMech mesh (`subject_id`, `units=m`, `nodes`, `tetrahedra`, `cavity_faces`) and
a reconstruction manifest containing element tissue labels, exterior faces,
physical volume, exact source/review and affine. Cavity faces are identified only
at explicitly labelled cavity interfaces and oriented into the wall.
CardiMech must still check closed cavities, connectedness, element quality and its
5000-node/20000-element limits. This is a stair-step voxel mesh, not a high-quality
curved clinical mesh. Raw-scan segmentation, trained weights, anatomical label
interpretation, scar classification, fibres and electrode registration are not supplied.

## Native reciprocal lead fields

The conductor request declares `subject_id`, `coordinate_frame`, `mesh`,
`electrodes`, `myocardial_element_ids`, `total_conductivity_S_per_m` (one 3×3
SPD tensor per torso element), `intracellular_conductivity_S_per_m` (one tensor per
selected myocardial element), `gauge_node`, and `output_dir`.
Total minus intracellular conductivity must remain SPD in myocardium.
Electrode JSON contains `subject_id`, exact `mesh_sha256`, `coordinate_frame`,
`reviewer` and `node_ids` for RA, LA, LL, V1–V6: nine distinct boundary nodes.
No locations or conductivities are inferred. RL is not an observation electrode.

The solver assembles P1 stiffness `K` over the conductor and intracellular `Ki`
over myocardium. With insulated outer boundary and an explicit gauge, it solves
`K Z = Cᵀ` and saves `L = -Zᵀ Ki`, so `ECG = L Vm` in volts. `C` defines standard
limb, augmented and Wilson-reference chest leads. Artifacts retain tensors,
electrodes, mesh pins, node order, gauge and reciprocal residual. Point electrodes
and conforming nested geometry are modelling assumptions, not measurements.
The synthetic regression compares reciprocity against a separate direct solve,
checks gauge invariance, constant-voltage null response and Einthoven's identity.
Mesh intersections and anatomical segmentation quality need independent review.
This native operator is **not** an openCARP `.dat` lead-field workspace and is not
silently substituted for that adapter's required artifacts.

## Spatial excitation research backend

`patient.spatial_ep.simulate` implements mass-lumped tetrahedral diffusion with
backward Euler and explicit cubic excitation/recovery:

`du/dt = div(D grad u) + (u(u-a)(1-u)-w+I)/tau_u`

`dw/dt = (u-bw)/tau_w`.

Request: patient `mesh`, `subject_id`, `coordinate_frame`, explicit
`diffusion_tensor_m2_per_s`, nodal `initial_u`/`initial_w`, `parameters`
(`a`, `b`, `tau_u_s`, `tau_w_s`, `rest_V`, `amplitude_V`), `dt_s`, `duration_s`,
`stimuli` (`node_ids`, `start_s`, `end_s`, `amplitude`), `output_dir`.
No physiology defaults are supplied. Conservative reaction timestep bounds,
finite-state checks and bounded steps/output size guard execution; users still
need timestep and mesh convergence studies. Vm is an explicitly scaled
phenomenological voltage, not a human ionic model.

Optional `leadfield` plus `leadfield_node_ids` map each heart node to a conductor
node. Coordinates must agree exactly and mapped heart tetrahedra must equal the
lead field's declared myocardial elements. This prevents propagation through
torso tissues. The code saves nodal voltage and derived 12-lead ECG time series.
It does not add Purkinje anatomy, ion-channel personalization, active myocardial
mechanics, valves, four-chamber fluid–structure coupling or a closed-loop reflex model.

## Qualification and remaining work

`patient.circulation.simulate` adds a separate eight-compartment closed loop:
LA → LV → systemic arteries → systemic veins → RA → RV → pulmonary arteries →
pulmonary veins → LA. Four ideal diode valves gate mitral/aortic/tricuspid/pulmonary
flow. Chambers use explicitly supplied time-varying elastance; vessels use explicit
compliance. All eight initial volumes, edge resistances, unstressed volumes,
external pressures, atrial/ventricular activation timings and integration controls
are required; no physiological defaults are inferred. Outputs include per-compartment
pressure/volume, per-edge flow and a blood-volume conservation diagnostic.
The synthetic test uses a manufactured zero-flow equilibrium and an activated LV
to check valve direction and conservation. The request contract is documented by
`circulation_request` in `tests/test_patient_native_preparation.py`.
This is a lumped model; it does not supply spatial active wall mechanics, valve
inertia/regurgitation, reflex control, or automatic coupling to the spatial EP solver.

All assistant-run checks belong in the user's Edge Colab. The existing validation
script includes the new synthetic numerical and hospital-boundary tests.
No patient-specific accuracy, clinical safety, outcome or prospective validation
is established by those checks. Live-worker browser execution remains a separate
outstanding review. The missing hospital configuration, imaging model assets and
reviewed patient datasets cannot be manufactured from software changes.
