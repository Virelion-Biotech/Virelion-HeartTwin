# Patient-specific ventricular EP in HeartTwin

## Scope

The first patient-specific HeartTwin pathway is deliberately narrow: reconstruct or ingest patient ventricular anatomy, calibrate a limited electrophysiology model using patient electrical measurements, generate an independent forward prediction, and compare that prediction with measurements that were withheld from fitting.

This is research infrastructure. It is not a claim that HeartTwin is a clinically validated whole-heart digital twin.

## Patient specificity is explicit

Every important patient-model input is labelled as one of:

- `MEASURED`
- `ESTIMATED_FROM_PATIENT`
- `POPULATION_PRIOR`
- `ASSUMED`
- `UNKNOWN`

Measured and patient-estimated inputs must link to patient acquisitions or observations. Population priors must identify their reference, assumptions must identify why they were made, and unknown values cannot silently carry a numerical value.

A patient-specific ventricular mesh with population-derived fibres is therefore represented as patient-specific anatomy with non-patient-specific fibre architecture rather than being presented as fully personalized physiology.

## Patient record

`PatientCase` is pseudonymous and separates source acquisitions from derived model inputs. It records encounters, acquisition identity, modality, acquisition time, source objects, units, physiological context, coordinate frames, frame transforms, model-input provenance, and evidence roles.

HeartTwin rejects cross-patient acquisitions or evidence records and unresolved internal references.

## Evidence separation

Two validation modes are distinct.

### Within-patient holdout

The same patient may legitimately provide both calibration and validation data when the actual measurements are independent. The same observation or acquisition cannot be reused on both sides.

Example:

- ECG acquisition A: calibration
- ECG acquisition B: withheld validation

### Cohort generalization

Calibration and validation patients must be disjoint. Subject overlap is treated as leakage.

This distinction permits legitimate within-patient prediction while preserving patient-disjoint testing for cohort-level claims.

## Patient anatomy readiness

The strict patient pathway calls CardiAnatomy with:

```text
anatomy.validate -- target patient_ep
```

The `patient_ep` gate is intentionally stronger than generic solver-level `ep` readiness. In addition to the artifacts required by the EP solver, it requires an unambiguous selected segmentation, volume mesh, coordinate field, and fibre field; SHA-256-pinned selected artifacts; matching subject/study/acquisition lineage; declared physical coordinate frames; a traceable frame path from the reviewed segmentation to the selected model-space artifacts; an expert-reviewed or expert-corrected selected segmentation; and QC covering the selected patient volume mesh.

Passing this gate means the anatomy satisfies software-level patient provenance/readiness requirements. It does not prove segmentation accuracy or clinical validity.

HeartTwin additionally fails closed when the patient record lacks:

- patient CT or MRI for ventricular anatomy;
- a digital ECG acquisition;
- imaging source files;
- an imaging coordinate frame;
- ECG source files;
- ECG sampling rate; or
- ECG lead definitions.

Generic fibres, missing scar characterization, standardized torso geometry, or protocol-derived electrode coordinates remain visible limitations rather than being silently promoted to patient-specific inputs.

## Workflow

The strict executable path is:

```text
PatientCase
    |
    +--> CardiAnatomy anatomy.build (optional when a bundle already exists)
    |        |
    |        +--> anatomy.validate -- target patient_ep
    |                    |
    |                    +--> reviewed + traceable canonical anatomy bundle
    |
    +--> calibration ECG evidence only
    |        |
    |        +--> CardiEP ep.calibrate
    |                    |
    |                    +--> calibrated parameter set
    |
    +--> parameter semantics gate
    |        |
    |        +--> same backend: plumbing replay allowed
    |        +--> different backend: BLOCKED until explicit mapping exists
    |
    +--> CardiEP ep.simulate
             |
             +--> independent forward prediction

held-out ECG evidence -------------------------------------> held-out evaluator
```

The held-out observations are absent from both calibration and confirmation solver requests. They are evaluated only after the prediction has been emitted.

## Scientific-grade versus plumbing-grade ECG

CardiEP's native `numpy-eikonal-v1` backend remains useful for fast integration and inverse-loop development, but its ECG implementation is an exploratory pseudo-ECG rather than a physical clinical forward solver.

HeartTwin therefore refuses to treat `numpy-eikonal-v1` as satisfying a scientific-grade lead-field workflow. It may still be used when a workflow explicitly sets `scientific_grade=false` and `observation_model="pseudo_ecg"` for software plumbing and synthetic testing.

CardiEP now also provides `opencarp-external-v1`, a strict confirmation-only boundary for a user-installed openCARP/carputils experiment. The adapter requires lead-field ECG output, a standard 12-lead result, versioned execution provenance, and an exact request hash; it rejects pseudo-ECG output. The adapter does not vendor openCARP, does not provide the external experiment wrapper itself, and does not establish patient-level validity.

## Cross-backend parameter semantics

HeartTwin does not assume that equal-looking parameter names have equal physiological meaning across solvers. The current strict patient workflow therefore rejects implicit replay when:

```text
fast_backend != confirmation_backend
```

A scientific fast-model to openCARP workflow remains blocked until a versioned parameter-mapping contract exists. That contract must state, at minimum, source and target backends, source/target parameter names, units and conversions, semantic rationale, mapping version, and every source parameter that is intentionally ignored. Nothing may be silently renamed, rescaled, or dropped.

Same-backend replay remains available for plumbing and controlled model-specific workflows.

## Held-out ECG evaluator

`evaluate_heldout_ecg` is downstream-only and has no calibration or solver interface. It refuses evidence that is not explicitly marked `withheld_from_fit`.

It currently supports:

- per-lead RMSE;
- per-lead normalized RMSE;
- per-lead correlation;
- mean normalized waveform error;
- mean correlation;
- QRS-duration error when both waveforms have QRS annotations; and
- predeclared acceptance criteria.

Measured and predicted signals are normalized to common voltage units before comparison. Timing can use a fixed offset declared before evaluation, but the evaluator does not search the held-out waveform for an optimal post-hoc alignment.

No universal "digital twin accuracy" threshold is built in. Acceptance criteria must be declared for the intended validation study.

## Example: integration/plumbing run

```python
from hearttwin import PatientEPWorkflowSpec, prepare_patient_ep_requests

spec = PatientEPWorkflowSpec(
    patient_id="PSEUDO-001",
    anatomy_acquisition_id="ct-001",
    calibration_evidence_ids=["ecg-fit"],
    validation_evidence_ids=["ecg-holdout"],
    fast_backend="numpy-eikonal-v1",
    confirmation_backend="numpy-eikonal-v1",
    observation_model="pseudo_ecg",
    scientific_grade=False,
)

prepared = prepare_patient_ep_requests(
    case=patient_case,
    spec=spec,
    anatomy_bundle=anatomy_bundle,
    parameter_bounds={"isotropic_speed": (0.03, 0.10)},
    initial_parameters={"isotropic_speed": 0.06},
    parameter_units={"isotropic_speed": "cm/ms"},
)
```

This checks lineage and leakage plumbing. It is not an empirical patient-level validation result.

## Intended scientific path

The intended confirmation backend is now available as a software boundary:

```text
confirmation_backend = "opencarp-external-v1"
```

However, a scientific run is not yet end-to-end executable through the strict patient workflow because the qualified fast calibration model and explicit fast-model-to-openCARP parameter mapping are not yet implemented. The external openCARP experiment/wrapper must also be installed, configured, and independently validated for the intended use.

HeartTwin deliberately blocks this incomplete pathway instead of substituting the native pseudo-ECG or copying parameters across solvers.

## Next engineering gates

1. Add an explicit, versioned EP parameter-mapping contract and mapped replay workflow.
2. Implement and qualify the external openCARP/carputils wrapper behind CardiEP's merged `opencarp-external-v1` boundary.
3. Connect predicted and measured ECG artifacts to the held-out evaluator automatically while preserving evidence IDs and hashes.
4. Persist `PatientCase`, workflow specifications, predictions, residuals, limitations, mappings, and validation reports through CardiTrace.
5. Run one complete reviewed patient reconstruction + calibration + withheld prediction with predefined endpoints, then repeat across additional patients.
6. Only after repeated-patient evaluation, extend the patient pathway toward mechanics and circulation.
