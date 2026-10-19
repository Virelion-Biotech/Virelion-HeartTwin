# Patient-specific ventricular EP in HeartTwin

## Scope

The first patient-specific HeartTwin pathway is deliberately narrow: reconstruct or ingest patient ventricular anatomy, calibrate a limited electrophysiology model using patient electrical measurements, generate an independent forward prediction, and compare that prediction with measurements withheld from fitting.

This is research infrastructure. It is not a claim that HeartTwin is a clinically validated whole-heart digital twin.

## Patient specificity is explicit

Every important model input is labelled as one of:

- `MEASURED`
- `ESTIMATED_FROM_PATIENT`
- `POPULATION_PRIOR`
- `ASSUMED`
- `UNKNOWN`

Measured and patient-estimated inputs must link to patient acquisitions or observations. Population priors must identify their reference, assumptions must identify why they were made, and unknown values cannot silently carry a numerical value.

A patient-specific ventricular mesh with rule-based fibres is therefore represented as patient-specific anatomy with non-patient-specific fibre architecture rather than being presented as fully personalized physiology.

## Patient record

`PatientCase` is pseudonymous and separates source acquisitions from derived model inputs. It records encounters, acquisition identity, modality, acquisition time, source objects, units, physiological context, coordinate frames, frame transforms, model-input provenance, and evidence roles.

HeartTwin rejects cross-patient acquisitions/evidence and unresolved internal references.

## Calibration and validation are different roles

HeartTwin separates two validation modes.

### Within-patient holdout

The same patient may provide both calibration and validation data when the actual measurements are independent. The same observation or acquisition cannot be reused on both sides.

Example:

```text
ECG acquisition A -> calibration
ECG acquisition B -> withheld validation
```

### Cohort generalization

Calibration and validation subjects must be disjoint. Subject overlap is treated as leakage.

This distinction permits legitimate within-patient prediction without weakening patient-disjoint cohort evaluation.

## Patient anatomy readiness

The strict patient pathway calls CardiAnatomy with:

```text
anatomy.validate --target patient_ep
```

The `patient_ep` gate is stronger than generic solver-level `ep` readiness. It requires an unambiguous selected segmentation, volume mesh, coordinate field, and fibre field; SHA-256-pinned selected artifacts; matching subject/study/acquisition lineage; physical coordinate frames; traceable frame relationships from reviewed segmentation into model space; expert-reviewed or expert-corrected segmentation; and QC covering the selected patient volume mesh.

Passing this gate means the bundle satisfies software-level patient provenance/readiness requirements. It does not prove segmentation accuracy or clinical validity.

HeartTwin also fails closed when the patient record lacks required source files, imaging coordinate declarations, ECG sampling rate, or ECG lead definitions.

Generic fibres, unavailable scar characterization, standardized torso geometry, and protocol-derived electrode coordinates remain visible limitations.

## Workflow

```text
PatientCase
    |
    +--> CardiAnatomy anatomy.build (optional)
    |        |
    |        +--> anatomy.validate --target patient_ep
    |                    |
    |                    +--> reviewed + traceable anatomy bundle
    |
    +--> calibration ECG evidence only
    |        |
    |        +--> CardiEP ep.calibrate
    |                    |
    |                    +--> calibrated parameter set
    |
    +--> parameter semantics gate
    |        |
    |        +--> same backend: direct model-specific replay
    |        |
    |        +--> different backend:
    |                 EPParameterMappingSpec required
    |                 every source parameter mapped or explicitly ignored
    |                 units + mapping fingerprints recorded
    |
    +--> CardiEP ep.simulate
             |
             +--> unconditional forward prediction

held-out ECG evidence -------------------------------------> held-out evaluator
```

The held-out observations are absent from both calibration and confirmation solver requests. They are evaluated only after a prediction has been emitted.

## Cross-backend parameter semantics

Matching parameter names are not evidence that two solvers represent the same physiological quantity or use the same units and conventions.

HeartTwin therefore provides:

- `EPParameterMappingSpec`
- `EPParameterMapRule`
- `IgnoredEPParameter`
- `apply_ep_parameter_mapping`

The mapping contract binds source and target backend IDs, source and target parameter names, units, affine conversion, rationale, intended use, intentionally ignored source parameters, and deterministic fingerprints.

Strict patient execution now supports heterogeneous calibration and confirmation backends **only** when such a mapping is supplied. Execution fails when a calibrated parameter is neither mapped nor explicitly ignored, units disagree, required source values are absent, mapped values are non-finite, or backend identities disagree.

HeartTwin does not bundle a guessed fast-model-to-openCARP mapping. A concrete mapping must be justified for the selected fast model and selected openCARP experiment.

## ECG model boundary

CardiEP's native `numpy-eikonal-v1` backend remains useful for software/integration work and inverse-loop development, but its ECG output is exploratory pseudo-ECG. HeartTwin refuses to treat that output as a scientific-grade lead-field model.

CardiEP also provides `opencarp-external-v1`, a strict confirmation-only boundary for a user-installed openCARP/carputils experiment. It requires:

- a lead-field ECG forward model;
- standard 12-lead output;
- positive sample-rate metadata;
- openCARP and wrapper versions;
- exact request hashing; and
- no pseudo-ECG output.

The external wrapper remains an explicit user-installed component. Registering the backend does not qualify any particular openCARP experiment for patient use.

Official openCARP documentation recommends carputils for reproducible multi-step experiments and supports direct parameter-file execution. The HeartTwin/CardiEP adapter deliberately uses a JSON wrapper boundary because patient geometry preparation, torso/electrode handling, simulation, lead-field construction, and artifact collection are multi-step operations.

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

Measured and predicted signals are converted to common voltage units. Timing may use a fixed offset declared before evaluation, but the evaluator does not search the held-out waveform for a post-hoc optimal alignment.

No universal "digital twin accuracy" score is built in.

## Patient-model report

`build_patient_model_report` produces a machine-readable summary for the workbench and audit trail. It exposes:

- personalization status by input and domain;
- patient-derived fractions by domain;
- calibration and validation evidence split;
- anatomy/EP readiness;
- calibration and confirmation backends;
- mapping identity/fingerprint when supplied;
- held-out ECG validation when available;
- explicit limitations; and
- a conservative claim ceiling.

The claim ceiling can be `blocked`, `software_plumbing_only`, `patient_specific_research_prediction`, or `heldout_patient_evaluation_available`. None of these states implies clinical qualification.

## Plumbing example

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

This exercises lineage and leakage plumbing. It is not an empirical patient-level validation result.

## Intended scientific path

The intended high-fidelity confirmation boundary is:

```text
confirmation_backend = "opencarp-external-v1"
```

A scientific-grade end-to-end workflow still requires a qualified fast ECG/inverse model, a reviewed mapping from that model into the selected openCARP experiment, an installed and independently checked openCARP wrapper/experiment, patient data, expert anatomical review, and held-out patient/cohort validation.

## Next engineering gates

1. Select and qualify a fast/inverse EP model suitable for patient ECG calibration.
2. Define and scientifically review its mapping into a selected openCARP experiment using the existing mapping contract.
3. Implement/qualify the external openCARP/carputils experiment wrapper behind `opencarp-external-v1`.
4. Materialize measured and predicted ECG artifacts automatically into the held-out evaluator while preserving evidence IDs and hashes.
5. Persist patient cases, mapping fingerprints, predictions, residuals, limitations, and validation reports through CardiTrace.
6. Run one complete reviewed patient reconstruction + calibration + withheld prediction, then repeat across additional patients before broader claims.
