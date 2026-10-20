# Patient native preparation — 2026-10-20

Tested source: `1e18d2c292bb0c5d93cd8e90cca885d2951ef1b9`.

The user's Edge Colab ran 145 patient/workbench tests: zero failures, errors or
skips. All six validation-script checks passed. The 22 additional tests cover
FHIR patient/encounter/unit/status/holdout boundaries, pagination origin/resource
confinement and loops, immutable imports, reviewed JSON/NIfTI reconstruction,
closed cavity orientation, affine gradients/volume, native patient-plan execution,
direct-versus-reciprocal ECG, gauge/constant-voltage/lead identities, spatial
equilibrium/stimulation, and four-chamber circulation equilibrium/conservation.

All four implementation-commit GitHub workflows passed: CI, frozen integration,
current-main integration, and operational rehearsal. `ci_status.json` records run
IDs. These workflows are supplementary to assistant-run Colab validation.

The downloaded archive is saved unchanged as
`HeartTwin_Patient_Native_Preparation_Results.zip`.
SHA-256: `dbd8fbb2a5593722f42d8f53f99a407e72ec5adb92f36f978943c2263de45947`.
Its internal artifact hashes were verified after transfer. Selected raw logs and
environment metadata are also extracted here. `colab_pass.jpg` is the actual Edge
Colab result view, not a generated illustration.

No private patient data was used. Hospital authentication/configuration,
raw-scan segmentation, anatomical mesh qualification, personalized fibres/cellular
models, spatial electromechanics/FSI and clinical validation remain incomplete.
Live-worker browser end-to-end review remains outstanding. A synthetic test pass
is not a patient-specific accuracy or clinical-benefit claim.

Reproduce using `notebooks/HeartTwin_Patient_Native_Preparation_Validation.ipynb`.
