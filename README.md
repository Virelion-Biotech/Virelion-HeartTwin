# Virelion-HeartTwin

HeartTwin is the orchestration, state, provenance, interoperability, and cross-service workflow layer for the Virelion cardiac research software stack. It provides one API/CLI surface for service discovery, typed state exchange, multimodal analysis, mechanistic simulation, inverse modelling, virtual interventions, challenge/evaluation workflows, distributed execution, and reproducible integration testing.

HeartTwin does **not** replace specialist algorithms with one monolith. It connects dedicated Virelion repositories through native Python adapters, command adapters, CardiBridge transport, or HTTP service endpoints while preserving explicit scientific boundaries and provenance.

> **Status note — 2026-10-08:** the stack has expanded substantially beyond the original multimodal workflow. The repository now includes a complete mechanistic path, a single-subject superstack continuity workflow, distributed operational rehearsal, cross-physics lineage in `CardiacState`, and a shared optical-stimulation compatibility contract. Several component repositories also received new CPU-verified releases on 2026-10-07/08. Those latest component heads are described below, but they are **not automatically equivalent to the frozen HeartTwin compatibility matrix** until that matrix is deliberately refreshed and requalified.

## What HeartTwin now orchestrates

HeartTwin exposes several complementary execution paths:

1. **Service facade** — direct capability calls through the registry with typed results and provenance.
2. **Multimodal workflow** — measured/modality analyses, benchmark binding, learning, evaluation, challenge generation, interoperability, and tracing.
3. **Mechanistic workflow** — anatomy → electrophysiology → inference → mechanics → flow → virtual intervention → independent evaluation → trace.
4. **Single-subject superstack** — composes the multimodal and mechanistic workflows while allowing only explicit, scientifically defensible cross-domain links.
5. **Operational rehearsal** — runs the stack behind real localhost worker processes with persistent state/artifacts and injected failures.
6. **Cardiac-digital-twin backend** — a built-in CDT-compatible reference integration retained alongside the modular specialist stack.

The installed service registry is packaged in [`hearttwin/services.yaml`](hearttwin/services.yaml). `load_registry(path)` accepts an explicit override.

## Integrated services

| Service | Primary role | HeartTwin connection |
|---|---|---|
| **CardiAnatomy** | patient-specific imaging, geometry, mesh QC, transforms, motion, fibres, scar, downstream readiness gates | Native + HTTP fallback |
| **CardiAtlas** | biomedical metadata and evidence context | Native + HTTP fallback |
| **CardiBench** | benchmark definitions, dataset policy, discovery, cataloguing, admission and result recording | Native + HTTP fallback |
| **CardiEval** | independent evaluation | Native + HTTP fallback |
| **ElectroTrace** | ECG/electrophysiology analysis and calibration preparation | Command adapter |
| **MyoTrace** | video/TIFF mechanical-motion analysis | Command adapter + cross-stack E2E use |
| **OptiCell** | microscopy QC/cell analysis and optical-stimulation metadata compatibility | Command adapter |
| **CardioScore** | MEA-based cardiac safety scoring | Command adapter |
| **CardiLearn** | molecular-state learning/prediction | Native + HTTP fallback |
| **CardiEP** | cardiac electrophysiology forward simulation, fast calibration, numerical-validation gateway | Native + HTTP fallback |
| **CardiInfer** | shared inverse problem, Bayesian/likelihood-free inference, identifiability and UQ | Native + HTTP fallback |
| **CardiMech** | mechanics/electromechanics, EP-to-mechanics handoff, circulation coupling and mechanics-calibration forward model | Command adapter |
| **CardiFlow** | hemodynamics contracts and deterministic 0D Windkessel reference backend | Native + HTTP fallback |
| **CardiSim** | synthetic cardiac trajectories and simulation utilities | Native + HTTP fallback |
| **CardiSimNative** | CDT-compatible reference digital twin built into HeartTwin | Built in |
| **CardiStudio** | experiment design, populations, constraints and power planning | Native + HTTP fallback |
| **DCCP** | defensive challenge scenarios, host mapping and recovery/resilience scoring | Native + HTTP fallback |
| **CardiTherapy** | typed virtual-intervention experiments; narrow CardiEP pacing backend | Native + HTTP fallback |
| **CardiTrace** | provenance and reproducibility sealing | Command adapter |
| **CardiBridge** | typed interoperability, routing, delivery, replay and contract negotiation | In-process + HTTP fallback |
| **CardiAgent** | phenotype-level challenge generation and adaptive challenge construction | Command adapter |
| **CardiVex** | challenge/OOD evaluation | Native + HTTP fallback |

Service registration means the contract exists. It does **not** mean that the underlying scientific method is biologically or clinically validated.

## Architecture

### Canonical orchestration

```text
observations / experiment data / geometry
                │
                ▼
            HeartTwin
                │
                ▼
          CardiacState 1.3
                │
   ┌────────────┼───────────────────────────────┐
   ▼            ▼                               ▼
multimodal   mechanistic                    control plane
analysis      physics                       / challenge
   │            │                               │
   ▼            ▼                               ▼
CardiBench   CardiAnatomy                    CardiStudio
CardiLearn      │                             DCCP
CardiEval       ▼                             CardiAgent
               CardiEP                          │
                  │                              ▼
                  ▼                          CardiBridge
              CardiInfer                         │
                  │                              ▼
                  ▼                           CardiVex
              CardiMech                          │
                  │                              ▼
                  ▼                           CardiEval
              CardiFlow                          │
                  │                              ▼
                  ▼                           CardiTrace
            CardiTherapy
                  │
                  ▼
              CardiEval
                  │
                  ▼
              CardiTrace
```

### Mechanistic chain

The explicit mechanistic workflow is:

```text
CardiAnatomy readiness
        │
        ▼
      CardiEP
 activation / repolarization / pseudo-ECG
        │
        ├──────────────► CardiInfer
        │                  │
        │          posterior / selected parameters
        │                  │
        ▼                  ▼
                 CardiMech
        EP activation → mechanics / PV / strain
                        │
                        ▼
                    CardiFlow
              hemodynamics / afterload
                        │
                        ▼
                  CardiTherapy
            virtual intervention experiments
                        │
                        ▼
                    CardiEval
                        │
                        ▼
                   CardiTrace
```

The path verifies artifact hashes, anatomy identity, canonical state identity, posterior lineage, and explicit handoff semantics at cross-physics boundaries.

## Single-subject superstack continuity

`run_superstack_workflow()` composes the established multimodal and mechanistic workflows instead of inventing a second set of scientific contracts. A repository counts as genuinely connected only when its output becomes a typed canonical artifact, a provenance-linked gate, or an explicit downstream parameter/control input.

Current explicit links include:

- **CardiStudio → benchmark coverage gate** for experimental-design constraints.
- **DCCP → CardiAgent** through a selected host-axis/challenge-severity mapping.
- **ElectroTrace → CardiInfer → CardiEP** through measured calibration evidence and an explicit parameter map.
- **MyoTrace → CardiMech** through measured dominant-frequency timing converted to cycle length.
- **OptiCell → mechanistic execution** as an imaging-QC gate.
- **CardioScore → CardiTherapy** as a safety-evidence gate and therapy-plan provenance input.
- **CardiAnatomy → CardiEP → CardiMech/CardiInfer → CardiFlow → CardiTherapy** with cross-physics hash lineage.
- **CardiBench → CardiLearn → CardiEval** with benchmark/split identity preserved.
- **CardiAgent → CardiBridge → CardiVex** as a real producer/transport/consumer handoff.
- **CardiTrace** independently recomputes and seals the final canonical-state fingerprint.

No automatic transformation is made between unlike biological quantities. Cross-domain mappings must be explicit. See [`docs/SUPERSTACK_CONTINUITY.md`](docs/SUPERSTACK_CONTINUITY.md).

## Canonical `CardiacState`

The current contract is **version `1.4.0`**.

`CardiacState` carries typed collections for:

- observations and biological context;
- anatomy bundles and readiness-linked artifacts;
- Atlas context and benchmark resolution;
- modality analyses;
- EP simulations;
- mechanics simulations;
- flow simulations;
- therapy experiments;
- derived state variables;
- simulations and predictions;
- inference/posterior artifacts;
- evaluation artifacts and validation gates;
- challenge and CardiVex records;
- CardiBridge publications;
- phase transitions;
- trace records and provenance.

`WorkflowState` is the execution view and carries stage-specific payloads plus the canonical `cardiac_state` reference.

`CardiacStateStore` validates unique IDs, prevents dangling provenance references, validates phase history, creates stable artifact IDs, and emits a SHA-256 `state_fingerprint`. Cross-physics identity and lineage are persisted rather than reconstructed heuristically downstream.

The legacy `inferred_state`, `simulations`, `predictions`, and `validation` dictionary fields remain as compatibility mirrors. New code should use the typed collections.

See [`docs/CARDIAC_STATE.md`](docs/CARDIAC_STATE.md) and [`schemas/cardiac-state-1.4.0.schema.json`](schemas/cardiac-state-1.4.0.schema.json).

## Current implementation

HeartTwin currently includes:

- typed cardiac-state and workflow contracts;
- service discovery and capability routing;
- native adapters for CardiAnatomy, CardiAtlas, CardiBench, CardiEval, CardiFlow, CardiLearn, CardiSim, CardiTherapy, CardiVex, CardiStudio and DCCP;
- command adapters for ElectroTrace, MyoTrace, OptiCell, CardioScore, CardiMech, CardiTrace and CardiAgent;
- in-process CardiBridge routing with HTTP fallback;
- a native CDT-compatible cardiac digital-twin backend;
- canonical `CardiacState` reduction for both low-level and explicit workflow execution;
- benchmark/test-group binding between CardiLearn and CardiBench;
- reproducible workflow run IDs and per-step SHA-256 provenance;
- multimodal, mechanistic and full-superstack continuity workflows;
- cross-physics anatomy/state/posterior/artifact lineage checks;
- independent mechanistic regression through CardiEval;
- CardiBench research-intelligence control-plane routing;
- distributed worker rehearsal with persistent jobs and content-addressed artifacts;
- frozen cross-repository integration testing on Python 3.10–3.12;
- a separate current-`main` integration path to expose sibling-repository drift;
- a shared optical-stimulation schema and cross-component canary for CardiSim/OptiCell.

## Recent component release status

The table below summarizes important component-level releases visible on `main` as of **2026-10-08**. These are **component-level verification states**, not a claim that the frozen HeartTwin stack has already been repinned to every listed release.

| Component | Recent release | What changed | Important scientific/validation boundary |
|---|---:|---|---|
| **CardiAnatomy** | 0.5.0 | complete CPU-native anatomy workflow, stronger QC/cache contracts, segmentation/point metrics, finalized-bundle semantics | public mesh fixtures intentionally expose rejection cases; no native patient-image reconstruction model or clinical validation |
| **CardiEP** | 0.3.0 | independently checked CPU numerics, bounded calibration, strict mesh/root/comparison contracts, durable artifacts, synthetic inverse recovery, cross-platform CI | fast Eikonal/pseudo-ECG remains a reference/integration model rather than validated PDE/clinical EP |
| **CardiInfer** | 0.5.0 | repaired inference statistics/contracts, ABC-SMC, Metropolis, MAP-DE, direct CardiEP ABC path, CPU audit | noisy direct-rejection CardiEP ABC interval coverage failed **3/9**; those intervals are not calibrated uncertainty |
| **CardiMech** | 0.3.0 | stricter contracts/QC, constitutive material-point utilities, CPU scientific verification, improved calibration handoff | reference backend is lumped mechanics, not spatial FE; empirical mechanics validation remains open |
| **CardiFlow** | 0.2.0 | corrected 0D timing/integration/coupling contracts, independent ODE references, analytic refinement and synthetic inverse recovery | deterministic 0D afterload only; not CFD or patient-specific flow validation |
| **CardiTherapy** | 0.2.0 | strict experiment lineage, verified artifacts, CPU pacing checks, configured-vs-observed delegate provenance | only activation-root pacing is executable; other therapy classes remain contract-only and fail closed |
| **DCCP** | 0.4.0 | label-blind evaluation, real-count CPU checks, strict artifact/missing-data contracts, optional pinned stack integrations | challenge scenarios are defensive phenotype abstractions; small design-scenario OOD results are not empirical performance claims |
| **CardiAgent** | 0.5.0 | reproduced CVAE failures, diagnosed false-rejection gate, added CPU conditional marginal quantile generator with fresh confirmation | original/support-aware CVAE remains unqualified; quantile model assumes conditional feature independence and is not biological realism |
| **MyoTrace** | 0.5.0 | repaired CPU optical flow, noise-aware event detection, optional signed displacement, independent confirmation | detected unsigned-flow events are **not verified cardiac beats**; motion magnitude is not force/stress without calibration |
| **CardiBridge** | 0.3.1 | leased durable delivery, cancellation-safe idempotency, verified persistence, strict wire/contracts, AsyncAPI checks, broad CI | transport correctness does not establish scientific correctness |

This status is intentionally explicit about failures. A failed validation gate remains evidence, not something to hide behind a successful software test.

## Optical-stimulation compatibility

HeartTwin now carries a shared compatibility contract:

```text
virelion.optical-stimulation/1.0.0
```

The contract describes modality, target, actuator, light parameters, timing, control mode and provenance. CardiSim and OptiCell are tested against the same payload contract, and optional optical stimulation can be carried into cardiac-twin input without silently inventing stimulation biology.

This is a compatibility/schema feature, not proof that a particular optogenetic model or imaging assay is physiologically validated. See [`docs/OPTICAL_STIMULATION_COMPATIBILITY.md`](docs/OPTICAL_STIMULATION_COMPATIBILITY.md).

## Quick start

Base package:

```bash
pip install -e '.[dev]'
hearttwin services
hearttwin doctor
pytest -q
```

Install the component repositories from their live `main` branches:

```bash
for repo in \
  Virelion-CardiAnatomy Virelion-CardiAtlas Virelion-CardiBench Virelion-CardiEval Virelion-CardiLearn \
  Virelion-CardiEP Virelion-CardiInfer Virelion-CardiMech Virelion-CardiFlow Virelion-CardiTherapy \
  Virelion-CardiSim Virelion-CardiVex Virelion-CardiStudio Virelion-DCCP \
  Virelion-ElectroTrace Virelion-MyoTrace Virelion-OptiCell Virelion-CardioScore \
  Virelion-CardiTrace Virelion-CardiBridge Virelion-CardiAgent; do
  python -m pip install "git+https://github.com/Virelion-Biotech/${repo}.git@main"
done
```

Run the synthetic multimodal workflow:

```bash
hearttwin workflow-demo --output outputs/workflow-demo.json
```

Run the production-topology rehearsal:

```bash
hearttwin operational-rehearsal \
  --workdir outputs/operational-rehearsal \
  --case-id HT-OPS-LOCAL
```

## Reproducibility and compatibility matrices

HeartTwin deliberately separates **reproducibility** from **moving-head compatibility**.

### Frozen matrix

`requirements-services.txt` contains the exact cross-repository revisions that were qualified together. `configs/compatibility-lock.json` mirrors that compatibility state and is regression-tested so service-contract upgrades cannot silently drift.

Use the frozen matrix when reproducibility matters:

```bash
python -m pip install -r requirements-services.txt
```

### Live-main matrix

`requirements-services-main.txt` installs the component repositories from their live default branches. The `HeartTwin current-main integration` workflow exercises the moving ecosystem and is designed to reveal sibling-repository drift that a frozen matrix cannot detect.

The October 7–8 component releases listed above postdate parts of the current frozen matrix. They should be promoted into the lock only after a fresh whole-stack qualification, not merely because their individual repositories pass their own tests.

Component compatibility fixes should remain in the component repositories rather than being hidden by HeartTwin-specific fallback code.

## Integration and CI

HeartTwin uses complementary gates rather than one ambiguous green check:

1. **Base CI** — HeartTwin package tests without requiring every optional component.
2. **Frozen-stack integration** — installs the exact pinned component revisions in clean environments and runs native connection smoke tests, workflows and the full HeartTwin suite on Python 3.10–3.12.
3. **Current-main integration** — installs live component branches, runs the suite, the mechanistic continuity canary and the single-subject superstack canary.
4. **Operational rehearsal** — runs real localhost HTTP worker processes, persistent queue/job/artifact state and fault injection to test topology/recovery behavior rather than only in-process calls.

The `CardiacState` tests also validate runtime snapshots against the published Draft 2020-12 JSON schema.

## Operational rehearsal

The distributed rehearsal persists case/stage/job state in SQLite and writes content-addressed artifacts. It exercises the stack behind real worker processes and injects failure modes including:

- worker restart;
- timeout;
- duplicate request;
- queue-lease recovery;
- artifact corruption.

The rehearsal now includes the distributed mechanistic stage and full-superstack continuity rather than only isolated service calls.

See [`docs/OPERATIONAL_REHEARSAL.md`](docs/OPERATIONAL_REHEARSAL.md).

## CardiBench intelligence loop

CardiBench discovery/search/catalog operations are treated as **control-plane research intelligence**, not subject-level biological state. They are routed through the service registry but are not reduced into `CardiacState` as if they were measurements.

The evaluation loop is:

```text
benchmark.resolve
      ↓
   CardiLearn
      ↓
   CardiEval
      ↓
benchmark.result.record
      ↓
   CardiTrace
```

## ARGO empirical EP validation

HeartTwin includes a blinded held-out workflow for the public ARGO post-ischemic VT dataset.

```bash
hearttwin argo-validate /data/ARGODataset_Folder/Pt1

hearttwin argo-prepare \
  /data/ARGODataset_Folder/Pt1 \
  outputs/argo/Pt1 \
  --holdout-fraction 0.20 \
  --seed 42

hearttwin argo-score \
  outputs/argo/Pt1/split.json \
  outputs/argo/Pt1/predictions.json \
  --gates argo-gates.json \
  --output outputs/argo/Pt1/holdout-report.json
```

The split is created at the raw mapping-record level before target extraction. Calibration artifacts contain no held-out target values. The scorer reloads held-out WFDB records and recomputes signal-defined LAT and synchronized 12-lead ECG observables; predictions are cryptographically bound to the exact split through `split_sha256`.

The reconstructed CARTO LAT map remains a secondary concordance target because it is spatially reconstructed/interpolated. ARGO also does not supply the volumetric fibre-resolved anatomy needed for a complete native patient-specific CardiEP reconstruction, so HeartTwin does not fabricate one.

See [`docs/ARGO_EMPIRICAL_VALIDATION.md`](docs/ARGO_EMPIRICAL_VALIDATION.md).

## Cardiac-Digital-Twin integration

HeartTwin retains an audited integration with `juliacamps/Cardiac-Digital-Twin` at commit `816d51fab0837cfe9e20c7d3a318429e9acf0733` as a reference architecture/backend path. The upstream repository is MIT licensed.

See [`docs/CARDIAC_DIGITAL_TWIN_INTEGRATION.md`](docs/CARDIAC_DIGITAL_TWIN_INTEGRATION.md), `hearttwin/cdt_manifest.py`, and `THIRD_PARTY_NOTICES.md`.

## Scientific limitations

HeartTwin is research infrastructure. It does not diagnose patients or prescribe treatment.

A successful software path means that contracts, execution and provenance worked. It does **not** establish that:

- anatomy is clinically accurate;
- EP parameters are physiologically identifiable;
- pseudo-ECGs reproduce measured clinical ECGs;
- mechanics are spatially or materially validated;
- 0D hemodynamics equal CFD or patient physiology;
- posterior intervals are calibrated;
- challenge generators reproduce real disease biology;
- optical-flow events equal true cardiac beats;
- a simulated pacing change predicts patient benefit;
- passing evaluation converts a simulated state into a biologically validated state.

Scientific validation gates are recorded separately with prespecified criteria, evidence IDs and provenance. Service availability must never silently promote a state to biological or clinical validity.

The native pseudo-ECG, Eikonal, lumped-mechanics and 0D-flow backends are integration/reference components. Publication-critical or clinical claims require independently verified numerical backends and empirical validation appropriate to the claim.

## Roadmap

1. Requalify the whole stack and advance the frozen compatibility matrix to the latest component releases only after cross-repository canaries pass.
2. Add externally validated patient-image reconstruction/segmentation paths for CardiAnatomy.
3. Escalate publication-critical EP to independently verified monodomain/bidomain backends with cross-solver convergence and empirical validation.
4. Add spatial FE mechanics and CFD backends with independent numerical benchmarks and measured CMR/echo/hemodynamic validation.
5. Repair/calibrate uncertainty workflows where coverage diagnostics fail, especially direct CardiEP rejection-ABC uncertainty.
6. Validate MyoTrace timing/beat semantics and force calibration against independent mechanical ground truth.
7. Expand CardiTherapy beyond activation-only pacing only when validated intervention-specific forward models exist.
8. Validate CardiAgent/DCCP challenge distributions on independent empirical cohorts and keep failed generator evidence visible.
9. Expand persistent jobs, distributed artifact stores, deployment observability and research-facing UI without weakening scientific gates.

## Further documentation

- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — integration architecture and contracts.
- [`docs/CARDIAC_STATE.md`](docs/CARDIAC_STATE.md) — canonical state contract.
- [`docs/SUPERSTACK_CONTINUITY.md`](docs/SUPERSTACK_CONTINUITY.md) — full-stack continuity rules.
- [`docs/OPERATIONAL_REHEARSAL.md`](docs/OPERATIONAL_REHEARSAL.md) — distributed rehearsal and failure model.
- [`docs/OPTICAL_STIMULATION_COMPATIBILITY.md`](docs/OPTICAL_STIMULATION_COMPATIBILITY.md) — shared optical-stimulation contract.
- [`docs/ARGO_EMPIRICAL_VALIDATION.md`](docs/ARGO_EMPIRICAL_VALIDATION.md) — ARGO EP validation workflow.
- [`docs/REPAIR_AUDIT_2026-09-28.md`](docs/REPAIR_AUDIT_2026-09-28.md) — historical repair audit and unresolved gaps.

## License

GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`).
