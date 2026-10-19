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

Two validation modes are distinct:

### Within-patient holdout

The same patient may legitimately provide both calibration and validation data when the actual measurements are independent. The same observation or acquisition cannot be reused on both sides.

Example:

- ECG acquisition A: calibration
- ECG acquisition B: withheld validation

### Cohort generalization

Calibration and validation patients must be disjoint. Subject overlap is treated as leakage.

This distinction prevents the previous failure mode in which any appearance of one subject in both roles was rejected, while preserving patient-disjoint testing for cohort-level claims.

## EP readiness gates

The initial pathway fails closed when it lacks:

- patient CT or MRI for ventricular anatomy;
- a digital ECG acquisition;
- imaging source files;
- an imaging coordinate frame;
- ECG source files;
- ECG sampling rate;
- ECG lead definitions; or
- an EP-ready, canonically fingerprinted anatomy bundle at execution time.

Generic fibres, missing scar characterization, standardized torso geometry, or protocol-derived electrode coordinates remain visible limitations rather than being silently promoted to patient-specific inputs.

## Workflow

The executable path is:

```text
PatientCase
    |
    +--> CardiAnatomy anatomy.build (optional when a bundle already exists)
    |        |
    |        +--> anatomy.validate -- target ep
    |                    |
    |                    +--> canonical anatomy bundle fingerprint
    |
    +--> calibration ECG evidence only
    |        |
    |        +--> CardiEP ep.calibrate
    |                    |
    |                    +--> calibrated parameter set
    |
    +--> CardiEP ep.simulate using calibrated parameters
             |
             +--> independent forward prediction

held-out ECG evidence -------------------------------------> separate evaluator
```

The held-out observations are intentionally absent from the confirmation solver request. They are returned separately for downstream evaluation, preventing the confirmation model from conditioning on its own validation measurements.

## Scientific-grade versus plumbing-grade ECG

CardiEP's current native `numpy-eikonal-v1` backend is useful for fast integration and inverse-loop development, but its ECG implementation is explicitly an exploratory pseudo-ECG rather than a physical clinical forward solver.

HeartTwin therefore refuses to treat `numpy-eikonal-v1` as satisfying a scientific-grade lead-field workflow. It may still be used when a workflow explicitly sets `scientific_grade=false` and `observation_model="pseudo_ecg"` for software plumbing and synthetic testing.

The intended scientific path is an external high-fidelity EP backend with an appropriate ECG forward model, such as an openCARP-based monodomain/lead-field adapter. That adapter is not implemented by this change and must not be inferred from the existence of an openCARP backend name in configuration.

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

## Intended scientific-grade run

A scientific-grade configuration should use an explicitly installed and qualified backend, a physical ECG observation model, an EP-ready patient anatomy bundle, and a separately defined held-out evaluator. For example, conceptually:

```python
spec = PatientEPWorkflowSpec(
    patient_id="PSEUDO-001",
    anatomy_acquisition_id="cmr-001",
    calibration_evidence_ids=["ecg-fit"],
    validation_evidence_ids=["ecg-holdout"],
    fast_backend="<qualified fast activation backend>",
    confirmation_backend="<qualified monodomain backend>",
    observation_model="lead_field",
    scientific_grade=True,
    torso_mode="patient_specific",
    electrode_mode="recorded",
)
```

HeartTwin will not silently substitute the native pseudo-ECG when a scientific-grade lead-field workflow is requested.

## Next engineering gates

1. Add an explicit openCARP command/plugin adapter to CardiEP with pinned execution provenance and lead-field outputs.
2. Add a held-out ECG evaluator that computes predefined QRS and waveform endpoints without feeding validation data back into fitting.
3. Integrate one explicit CT/CMR reconstruction backend through CardiAnatomy and validate its segmentation/mesh outputs against independent reference labels and expert review.
4. Add parameter mapping between fast activation and high-fidelity confirmation backends rather than assuming backend parameter names are interchangeable.
5. Persist `PatientCase`, workflow specifications, predictions, residuals, limitations, and validation reports through CardiTrace.
6. Only after repeated-patient evaluation, extend the patient pathway toward mechanics and circulation.
