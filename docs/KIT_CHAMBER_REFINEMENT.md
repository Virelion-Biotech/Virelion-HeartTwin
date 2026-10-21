# KIT chamber-law diagnosis and development reassessment

Implementation pin: `ea7cafa7328900cfd4ae02d49e73a3f63faced20`. Original failed benchmark: `0b26c52a23a6b13dd41b90caacd912dfc7ab8db6`. Recorded 2026-10-21. Numerical work ran in the user's Edge Colab, not on the editing host.

## Result and evidence status

The selected candidate reduced LV outer development RMSE from **22.8685465 to 6.6323319 mL**, compared with persistence **8.0613390 mL**. This is a development reassessment of previously seen data from one volunteer. It is not independent or clinical validation, and does not establish a fully personalized whole-heart model.

The numerical values below were recorded in the preceding Colab session. During continuation, three Browser connector requests timed out, including troubleshooting and fresh setup. The final Colab lifecycle process could not be read and the raw four-round bundle, frozen predictions, hashes, plots and independent-check logs could not be exported. They must not be represented as attached evidence. The committed summary JSON is a transcription of the session record, not a substitute for raw results. The companion notebook reproduces the experiment and includes a rescue cell for the existing runtime.

GitHub was independently reachable: CI, frozen-stack integration and current-main integration all completed successfully on the implementation pin. The last fully observed Colab lifecycle run had **185 patient/workbench tests** and all six checks passing. A subsequently added real CardiInfer inverse/replay regression brought the focused chamber suite to **six passing tests**. The final full lifecycle run was launched but its completion is unconfirmed. No new notebook execution is claimed.

## Diagnosed causes and changes

| Observation | Diagnosis | Change or remaining limit |
|---|---|---|
| Original LV outer RMSE 22.87 mL, worse than persistence | Linear passive filling and symmetric activation were restrictive assumptions; solver and transport comparisons did not explain the error | Added opt-in exponential passive filling and asymmetric activation; legacy defaults retained exactly |
| Atrial strength changes had no calibration effect, but changed later LV values | The original calibration window ends before assumed atrial activation | Reject provably inactive chamber emax parameters before inference; inspect calibration axes only. Fitted timing is conservatively excluded from this screen |
| First nonlinear fits hit curvature bounds | Arbitrary bounds and poor Emin/k coordinate scaling | Profile a fixed curvature grid using fitted conditional passive pressure at the first measured LV volume |
| Wrapped activation at time zero | A previous-cycle activation can affect the initial pressure target | Explicit time-zero overlap check and regression |
| Pressure curves accepted with declared volume units | Cross-layer metadata defect, fixed separately in PR #55 | Native output unit checks and replay regressions retained |
| Constant-group statistical outputs were not strict JSON; all-ties rank p-values varied with SciPy | ElectroTrace numerical edge cases, fixed separately in PR #85 | Explicit undefined Welch status and exact all-ties Mann–Whitney result; updated stack pin |
| KIT mesh exceeds CardiMech guards; EDGAR waveform absent and surfaces nonmanifold | Spatial model/input and scale limits, unrelated to the new LV chamber law | No guard bypass, no ECG or full spatial mechanics validation claim |

The native constitutive law is
`P = external + (1-a)*Emin*expm1(k*(V-V0))/k + a*Emax*(V-V0)`.
At k=0 it uses the original linear passive law. The optional peak fraction sets separate activation rise and fall durations. Units are 1/mL for k and dimensionless for peak fraction. Invalid values and excessive exponential arguments are rejected. Legacy KIT LV predictions differed by zero at stored precision in the observed Colab comparison.

The activation check is a narrow structural screen, not proof of parameter identifiability. The real CardiInfer test exercises fitting the new parameter and fresh holdout replay on synthetic pressure data; synthetic software correctness is not patient validation.

## Protocol and all completed rounds

KIT v1.1: <https://zenodo.org/records/10526554>, CC BY-NC 4.0, one male volunteer. The same 35 measured LV timestamps were retained, with 23 original calibration and 12 original outer points. Each family fitted the first 17 calibration points, selected using the next six, then refitted all 23. Phase changes alter model activation, not measurement timestamps. Original outer values had already been seen in earlier work.

| Round | Inner RMSE mL | Refit calibration RMSE mL | Previously seen outer RMSE mL | Interpretation |
|---|---:|---:|---:|---|
| Initial nonlinear + asymmetric law | 8.6614754 | 7.9505233 | 16.2620275 | Curvature reached 0.025 upper bound |
| Expanded-bound refinement, no phase | 8.3043581 | 7.3436990 | 8.7514638 | Curvature again reached 0.05 upper bound |
| Stable stiffness profile, fitted phase **selected** | **3.6349856** | **4.9104948** | **6.6323319** | k=0.075 selected from grid |
| Stable stiffness profile, fixed phase control | 4.9744283 | 7.0213898 | 6.0198379 | Better outer score, worse declared inner criterion; not selected |

Cross-round choice used the lowest inner score, not outer error. This sequential exploration remains development work; a cross-round criterion cannot turn previously seen outer data into an independent test.

Profile grid: [0.005, 0.0125, 0.025, 0.05, 0.075, 0.1] 1/mL, two fixed starts, 55 evaluations per start. Fitted-phase inner RMSEs were [47.4835361, 45.9816562, 33.4453822, 8.3099669, 3.6349856, 5.0985150] mL. Fixed-phase scores were [10.5799655, 8.9975624, 8.1548143, 5.2578341, 5.4716488, 4.9744283] mL. Both earlier rounds and both controls must be retained in the eventual raw export.

Stable coordinate:
`Emin = P_ref*k/expm1(k*(V_ref-V0))`, with the k=0 limit `P_ref/(V_ref-V0)`.
V_ref=221.644518 mL is measured initial LV volume. **P_ref is fitted conditional pressure, not a measurement.**

Selected LV values recorded in Colab: V0=10 mL; Emin=1.679592263864284e-7 mmHg/mL; Emax=1.7609067367380635 mmHg/mL; activation start=1.2334031169105448 s modulo assumed 1.3 s cycle; duration=0.53608890138898 s; k=0.075 1/mL; peak fraction=0.7499999999983654. The peak fraction is at its upper bound. Small Emin is the zero-volume tangent of an exponential law and cannot be compared directly with clinical or old linear-model elastance. These partial values are not a complete replay request.

## Numerical checks and unresolved deviations

The observed selected-candidate independent DOP853 check differed by at most **0.0012198112 mL** across all compartment volumes, with total-volume drift **1.82e-12 mL**. Registry/direct transport difference was zero. The independent script used tighter tolerances and separate incidence/activation algebra. Its raw log and source remain pending export; this is a numerical consistency check, not independent patient validation.

Selected outer residual metrics: MAE 6.2204703 mL, bias +4.4839643 mL, maximum absolute error 9.1299233 mL. Improvement versus original outer RMSE was about 71%.

**Pressure scale remains structurally unidentified.** Multiplying elastances, resistances and pressures by a common factor and dividing compliances by it leaves the volume trajectories unchanged. Factors 0.5 and 2 produced zero volume change; corresponding selected-model peak LV pressures were about 76.68 and 306.73 mmHg. The fit does not identify patient pressure or contractility.

**RV end-systolic error remains about 21.8725 mL**: recorded prediction 87.8108733 mL versus 65.938406 mL observed. Improving LV does not fix RV physiology. The withheld RV target was not fitted merely to claim a pass.

Atrial contraction remains an unmeasured assumption in the relevant part of the waveform. The screen prevents one invalid calibration request but does not learn atrial behavior. More complete cycle coverage and independent pressure/flow data are needed to separate chamber and vascular parameters.

EDGAR electrical validation is still blocked by absent waveform input, unconfirmed coordinate units/reference convention and remaining nonmanifold surface edges. KIT mechanics still needs a reviewed reference state and a scalable solver/mesh strategy. These are separate workstreams, not failures solved by this chamber-law patch.

## Reproduction and recovery

Open `notebooks/HeartTwin_KIT_Chamber_Refinement.ipynb` in Edge Colab. It pins all seven repositories, runs the four families, chooses by inner score, runs lifecycle checks and exports a hash manifest plus ZIP. This newly assembled notebook has not been executed or compiled in Colab during the disconnected continuation.

To recover the existing session without repeating the numerical work, run only its clearly marked rescue cell in the original live notebook. Expected root: `/content/HeartTwin_Diagnosis_20261020`; folders: `results_chamber_laws`, `results_chamber_refinement`, `results_stiffness_profile`, `results_stiffness_fixed_phase`, `results_chamber_final`. Preserve logs, unsuccessful starts, frozen requests/predictions, independent scripts, environment pins, figures and test XML. Do not run all cells in the older real-data notebook.

Remaining evidence task: read final lifecycle status, export and verify raw ZIP/manifest, attach the actual Colab plot and log files, and execute/compile the new notebook in Edge Colab. Do not fill that gap with a fabricated screenshot or reconstructed prediction.
