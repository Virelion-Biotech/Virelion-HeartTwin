# Coupled patient research models — 2026-10-20

Tested implementation: `c2aa34f99453713207d10dc26d11b1fe161a7815`.

The user's Edge Colab ran **170 tests, zero failures/errors/skips**. All six
validation-script checks passed. Imaging dependencies were installed explicitly.
The run includes the monolithic four-cavity Jacobian/active mechanics/circulation
checks, native inverse/replay/holdout calibration, oblique DICOM image geometry,
seeded segmentation, nnU-Net adapter contract, multipart DICOMweb retrieval and
owner-only hospital transport boundary, alongside the prior patient/workbench suite.

The nnU-Net regression uses a synthetic subprocess contract. It does not load real
trained anatomical weights or establish segmentation accuracy. The coupled model
is a manufactured synthetic geometry, not a patient heart.

All four implementation-commit GitHub workflows also passed: CI, current-main
integration, frozen integration and operational rehearsal. See `ci_status.json`.

The actual Colab archive is retained unchanged:
`HeartTwin_Patient_Coupled_Results.zip`, SHA-256
`7385a5fd4c099aed0b0eb5e8828a16b4ece53aab7ee72fdd80422a8b686b85b0`.
Its internal artifact hashes were verified after transfer. Selected raw results
and environment metadata are extracted here. `colab_pass.jpg` is the actual Edge
Colab result screen.

The synthetic live worker started and its authenticated API smoke passed. The
official Colab port proxy was blocked in Edge with `ERR_BLOCKED_BY_CLIENT`, so
live-worker browser end-to-end review is **not completed**. No alternate tunnel or
weakened browser/worker protection was used. The synthetic worker was stopped.
See `live_worker_browser_review.json` and `live-worker.log`.

No private patient data, hospital credentials or trained anatomical model weights
were used. Hospital configuration and real patient/model qualification remain
external requirements. The implemented mechanics/circuit models do not include
fluid-field FSI, human ionic personalization, mechano-electric feedback or clinical
qualification. No patient accuracy or clinical-benefit claim follows from this run.

Reproduce: `notebooks/HeartTwin_Patient_Coupled_Validation.ipynb`.
