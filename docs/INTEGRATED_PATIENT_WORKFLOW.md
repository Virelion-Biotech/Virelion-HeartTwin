# Integrated patient-specific workflow: implementation contract

**Status:** engineering specification and gap audit; not a claim of end-to-end clinical qualification.

This document turns the HeartTwin product goal into testable software requirements. The objective is a guided, persistent, source-linked patient workflow that runs the existing specialist implementations on a trusted worker. It must not reduce the stack to a service catalogue, generic JSON launcher, or synthetic-only demo.

## What already exists in this repository

The current source tree already has meaningful building blocks:

- `hearttwin/patient_model.py`: typed patient/evidence/model contracts, source references, coordinate frames and fail-closed validation rules.
- `hearttwin/patient_execution.py`: a patient EP execution path that checks frozen case revisions, evidence roles, source integrity and predeclared held-out acceptance criteria.
- `hearttwin/patient_workspace.py`: geometry preparation with CardiAnatomy readiness checks, a canonical bundle fingerprint, explicit coordinate units/frame and index-base requirements.
- `hearttwin/web.py`: persistent patient cases/revisions, readiness and history routes, patient-specific job validation, artifact hashing and authenticated execution.
- `hearttwin/webui/app.js`: a patient workspace and worker-connected interface, alongside module launchers and results.
- `docs/PATIENT_NATIVE_PREPARATION.md` and `docs/PATIENT_COUPLED_MODELS.md`: substantive research backends and their stated limits.

These components mean the next step is **not to create another disconnected UI or another generic orchestration layer**. It is to join the existing patient-case APIs, reviewed data, compatible artifacts and existing execution paths through a guided user workflow, then prove that workflow against real supported data and a qualified solver setup.

## Important current limitations

1. A registered service or a runnable adapter does not imply that its numerical backend is installed, configured, or scientifically qualified.
2. Geometry viewing and geometry preparation are distinct. A surface is not automatically a volume mesh; coordinate frame, physical units, tissue/cavity labels, node order and mesh fingerprint must match downstream inputs.
3. Patient-specific execution must bind to an immutable case revision and fingerprint. A changed source or evidence role requires a new reviewed revision.
4. Calibration evidence must never leak into the held-out evaluation set. Acceptance criteria must be declared before execution.
5. The native spatial EP and circulation backends have explicitly documented phenomenological/lumped-model assumptions. Coupling them is not equivalent to a validated human ionic model or a fully personalized whole-heart twin.
6. The current workbench remains a trusted-owner worker. It is not a multi-tenant security boundary for shared patient data.
7. A synthetic pass is useful for software and numerical regression but is not evidence of individual-patient accuracy.

## Target user journey

### 1. Create or reopen a pseudonymous case

- Create a case with a safe local identifier and an explicit owner/access policy.
- Display immutable revisions, source acquisitions, provenance, evidence roles, and archived/restored state.
- Never silently overwrite a source, measurement, model parameter, or evidence role.
- Support resume after browser refresh or worker restart by reloading persisted case and job state.

### 2. Import and review source evidence

- Import supported MRI/CT, ECG, pressure/volume observations, and other supported inputs through explicit adapters.
- Show detected metadata and missing required metadata before allowing a downstream run.
- Record source SHA-256, modality, acquisition metadata, physical units, coordinate frame, provenance, reviewer, and intended role.
- Let a researcher review, correct, or reject inferred metadata; do not silently guess anatomical correspondence, units, fibre directions, electrode positions, or clinical semantics.
- Keep calibration, context, excluded and held-out validation evidence visibly distinct.

### 3. Prepare a simulation-ready anatomy bundle

- Invoke the real CardiAnatomy validation/build operations, not a viewer-only import.
- Require a versioned bundle with geometry, element/node identity, physical units, coordinate frame, tissue/cavity labels, required microstructure fields, source links, and canonical fingerprint.
- Show actionable QC failures (e.g. missing cavity labels, invalid connectivity, non-manifold geometry, absent fibre field, incompatible coordinate frames).
- Mark the model as not ready until the required checks for the selected downstream capability pass.

### 4. Run an electrical model and compare ECG

- Use ElectroTrace to characterize the actual recording and prepare supported calibration measurements.
- Select an installed, configured EP backend and explicitly match its mesh, node ordering, units, electrodes/lead field, stimulation and model parameters to the reviewed anatomy.
- Expose identifiable parameters and bounds; do not imply that unidentifiable values have been personalized.
- Execute the existing patient EP path, persist stage artifacts, and compare simulated output with predeclared held-out ECG observations using declared metrics and acceptance criteria.
- Report fit and holdout metrics separately, along with residual plots, failed criteria, uncertainty, warnings, solver configuration and software versions.

### 5. Extend to mechanics and flow

- Only enable each downstream stage if the upstream artifact contract is met and the relevant backend is installed and qualified for that task.
- Require explicit EP-to-mechanics mapping, constitutive assumptions, reference state, loading, boundary conditions, cavity correspondence and flow/circulation parameters.
- Record each transfer as a versioned transformation with input/output hashes and its own QC result.
- Reject silent conversion or guessed biological correspondence.
- Do not label the whole coupled model patient-specific unless the relevant domains have actual patient evidence and independent validation.

### 6. Inspect, compare and reproduce results

- Provide a linked case timeline: acquisitions → reviewed revisions → model bundle → run configuration → stage artifacts → evaluation.
- Provide real plots/3D views from solver artifacts (activation, ECG traces, residuals, strain or flow only when the relevant backend returns them).
- Allow comparison across runs and model revisions; identify exactly which input, parameter or software version changed.
- Preserve failure logs and partial outputs with an explicit incomplete/failed status. Never present a failed or interrupted job as a valid result.
- Export a reproducibility manifest with source hashes, package/commit versions, backend versions, units, parameters, seeds where applicable, acceptance criteria, artifact hashes and evaluation results.

## Canonical artifact envelope

All cross-repository handoffs should expose a small shared envelope while retaining module-native payloads:

```json
{
  "schema": "hearttwin.artifact/v1",
  "artifact_id": "stable-id-within-case",
  "artifact_type": "anatomy_bundle",
  "case": {
    "patient_id": "pseudonymous-id",
    "revision": 1,
    "fingerprint": "sha256-of-canonical-case"
  },
  "source_refs": [
    {"file_id": "source-id", "sha256": "64-character-sha256"}
  ],
  "spatial": {
    "coordinate_frame": "DICOM_LPS",
    "length_unit": "mm",
    "index_base": 0,
    "mesh_sha256": "64-character-sha256",
    "node_order_sha256": "64-character-sha256"
  },
  "measurements": [],
  "parameters": [],
  "quality": {
    "status": "pass",
    "checks": []
  },
  "provenance": {
    "producer": "Virelion-Biotech/Virelion-CardiAnatomy",
    "producer_version": "commit-or-release",
    "created_at": "ISO-8601 timestamp"
  },
  "payload": {}
}
```

This is a design example, not a drop-in replacement for current Pydantic contracts. Before adopting it in runtime code, map every field to existing contracts and introduce a versioned adapter with migration and round-trip tests. The envelope must not duplicate authoritative fields inconsistently or erase domain-specific validation.

## Required engineering deliverables

### A. Contract and adapter qualification

- Inventory actual source code, dependencies, executable entry points, external solver requirements, model assets, licensing and tests for every repository referenced by `hearttwin/services.yaml`.
- Generate a pinned compatibility matrix from actual tested commits; do not infer compatibility from repository names or latest default branches.
- For every edge in a workflow, add contract tests for units, coordinate frame, patient/case revision, mesh and node order, required fields, missing values and artifact fingerprints.
- Reject an adapter edge until its transformation is explicit and tested.

### B. Guided patient workspace

- Add guided screens for source review, anatomy readiness, EP configuration, held-out ECG evaluation, and result inspection.
- Forms should be generated from explicit schemas and backend capability declarations, not hand-authored arbitrary JSON for the common path.
- Keep an advanced configuration mode for research experts, with the same validation as the guided path.
- Display the exact reason a stage is blocked and the action required to unblock it.

### C. Real-data qualification

- Use only appropriately licensed, access-controlled data and model assets.
- Run at least one documented real-data case through supported acquisition → reviewed anatomy → actual solver → held-out comparison.
- Publish a reproducibility package without protected source data, credentials, or patient identifiers.
- Separate software tests, numerical verification, dataset qualification, and independent biological/clinical validation in the results.

## Acceptance criteria for the first integrated milestone

The first milestone is **patient-specific anatomy → electrical simulation → ECG comparison**, not the full whole-heart product.

It is complete only when all of the following pass:

1. A user can create/reopen a pseudonymous case, review source evidence, and create an immutable revision in the UI.
2. A real supported anatomy bundle passes CardiAnatomy readiness and all required mesh/frame/unit checks.
3. An installed, configured EP backend consumes that exact bundle; the run records and verifies the case, mesh and node-order fingerprints.
4. ElectroTrace's measured ECG and the simulated ECG use explicit, compatible physical units, lead definitions and time bases.
5. Calibration and held-out validation evidence are separate and leakage checks pass.
6. Predeclared metrics and acceptance thresholds are evaluated automatically; failure is visible and cannot be re-labelled as success.
7. The UI presents actual ECG traces, residuals, model outputs, QC and provenance, not illustrative data.
8. A worker restart/browser refresh does not lose the case, revision, job status or completed artifacts.
9. A reproducibility manifest reconstructs the run configuration and identifies the exact code/backend versions.
10. CI covers contract rejection cases and a deterministic manufactured numerical regression; real-data qualification evidence is reported separately.

## Work order

1. Finish a source-level audit and pin the real capability/backend matrix.
2. Map the existing patient API and UI to the case/revision/evidence contracts; fix the highest-impact gaps.
3. Add the guided anatomy → EP → ECG path on top of existing patient execution APIs.
4. Add a real-data qualification run and publish its reproducibility report.
5. Extend the same artifact/acceptance model to mechanics and flow after the first path is qualified.

**Non-goal:** do not make every repository a mandatory dependency of every run. Integrate each repository where its outputs have a defined scientific role; preserve modularity and fail closed when a required capability is absent.

**Safety boundary:** this is a research engineering workflow. It must not make diagnostic, prognostic, or treatment claims without separate intended-use validation, appropriate regulatory assessment, and clinical evidence.
