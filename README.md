# Virelion-HeartTwin

HeartTwin is the orchestration and integration layer for the Virelion cardiac research software stack. It provides one API/CLI for service discovery, specialist analysis, typed state exchange, cross-service workflows, provenance, and reproducible evaluation.

HeartTwin does not duplicate specialist-service algorithms blindly. It can use installed Virelion component packages natively, existing HeartTwin command adapters, CardiBridge transport, or external HTTP services where a deployment endpoint is configured.

## Integrated services

| Service | Role | Current connection |
|---|---|---|
| CardiAtlas | biomedical metadata and evidence context | Native + HTTP fallback |
| CardiBench | benchmark definitions and dataset policies | Native + HTTP fallback |
| CardiEval | independent evaluation | Native + HTTP fallback |
| ElectroTrace | ECG/electrophysiology analysis | HeartTwin command adapter |
| MyoTrace | video-based mechanical analysis | HeartTwin command adapter + E2E test |
| OptiCell | microscopy QC and cell analysis | HeartTwin command adapter |
| CardioScore | MEA-based cardiac safety scoring | HeartTwin command adapter |
| CardiLearn | molecular-state learning | Native + HTTP fallback |
| CardiSim | synthetic trajectories | Native + HTTP fallback |
| CardiSimNative | CDT-compatible reference digital twin | Built into HeartTwin |
| CardiStudio | experimental design, populations, constraints, power | Native + HTTP fallback |
| DCCP | defensive challenge scenarios and resilience scoring | Native + HTTP fallback |
| CardiTrace | provenance and reproducibility | HeartTwin command adapter |
| CardiBridge | typed interoperability and delivery | In-process + HTTP fallback |
| CardiAgent | phenotype-level challenge generation | HeartTwin command adapter |
| CardiVex | challenge/OOD evaluation | Native + HTTP fallback |

Service registration in `configs/services.yaml` is separate from scientific validation. Native connections require the corresponding Virelion package to be installed; HTTP environment variables remain available as deployment fallbacks.

## Architecture

```text
observations / experiment data
            │
            ▼
        HeartTwin
            │
            ▼
      CardiacState  ◄── canonical typed state
            │
   ┌────────┼──────────────────┐
   ▼        ▼                  ▼
CardiAtlas CardiLearn       modality adapters
   │        │                  │
   └────────┼──────────────────┘
            ▼
        CardiBench
            │
            ▼
     CardiSim / CDT
            │
      ┌─────┴─────┐
      ▼           ▼
 CardiAgent     specialist analyses
      │           │
      └─────┬─────┘
            ▼
        CardiBridge ─────► CardiVex
            │
            ▼
        CardiEval
            │
            ▼
        CardiTrace
```

`CardiacState` is the canonical biological/computational state object. `WorkflowState` is the execution view and carries stage-specific typed payloads alongside a `cardiac_state` reference. The low-level `HeartTwin.run()` API remains backward compatible and reduces any recognized typed service results into the canonical state.

## Canonical CardiacState

The current state contract is version `1.1.0`. It provides typed collections for observations, Atlas context, benchmark resolutions, modality analyses, derived state variables, simulations, predictions, evaluations, challenges, CardiVex observations, bridge publications, phase transitions, trace records, and HeartTwin provenance.

Each `StateValue` can carry a domain, variable, value, unit, anatomical region, temporal information, observed/inferred/simulated status, confidence, structured uncertainty, method, and provenance links. Simulation, prediction, and evaluation results have dedicated artifact models rather than requiring arbitrary dictionaries.

The old `inferred_state`, `simulations`, `predictions`, and `validation` dictionary fields remain as compatibility mirrors. New code should use the typed collections.

`CardiacStateStore` is the reducer/validator for the canonical state. It enforces unique IDs, prevents dangling HeartTwin provenance links, validates phase history, creates stable artifact IDs, and emits a SHA-256 `state_fingerprint` from the canonical snapshot.

See `docs/CARDIAC_STATE.md` and `schemas/cardiac-state-1.1.0.schema.json` for the contract and migration details.

## Current implementation

The repository includes:

- typed cardiac-state and workflow contracts;
- native adapters for CardiAtlas, CardiBench, CardiEval, CardiLearn, CardiSim, CardiVex, CardiStudio, and DCCP;
- command adapters for ElectroTrace, MyoTrace, OptiCell, CardioScore, CardiTrace, and CardiAgent;
- in-process CardiBridge routing with HTTP fallback;
- native CDT-compatible cardiac digital-twin backend;
- canonical CardiacState reduction for both low-level and explicit workflow execution;
- explicit benchmark/test-group binding between CardiLearn and CardiBench;
- reproducible workflow run IDs and per-step SHA-256 provenance;
- a full multimodal workflow ending in CardiEval and CardiTrace;
- cross-repository GitHub Actions integration testing on Python 3.10–3.12.

## Quick start

Base package:

```bash
pip install -e '.[dev]'
hearttwin services
hearttwin doctor
pytest -q
```

Install the component repositories for the complete native stack:

```bash
for repo in \
  Virelion-CardiAtlas Virelion-CardiBench Virelion-CardiEval Virelion-CardiLearn \
  Virelion-CardiSim Virelion-CardiVex Virelion-CardiStudio Virelion-DCCP \
  Virelion-ElectroTrace Virelion-MyoTrace Virelion-OptiCell Virelion-CardioScore \
  Virelion-CardiTrace Virelion-CardiBridge Virelion-CardiAgent; do
  python -m pip install "git+https://github.com/Virelion-Biotech/${repo}.git@main"
done
```

Run the synthetic end-to-end workflow:

```bash
hearttwin workflow-demo --output outputs/workflow-demo.json
```

The integration matrix installs the actual repositories from clean environments and fails hard if a required native service cannot be imported. Component compatibility fixes are kept in the component repositories rather than hidden by HeartTwin fallbacks.

The workflow is computational test infrastructure. Its generated states are not patient measurements, and passing software integration tests does not establish clinical or biological validity.

## Cardiac-Digital-Twin integration

See `docs/CARDIAC_DIGITAL_TWIN_INTEGRATION.md` for the architecture, provenance, data contract, calibration strategy, and scientific boundary.

The audited upstream reference is `juliacamps/Cardiac-Digital-Twin` at commit `816d51fab0837cfe9e20c7d3a318429e9acf0733`. The upstream repository is MIT licensed. See `hearttwin/cdt_manifest.py` and `THIRD_PARTY_NOTICES.md`.

## Validation

There are two CI layers:

1. `CI` runs the HeartTwin package tests without optional component installations.
2. `HeartTwin integration` installs the actual component repositories into a clean environment, runs native connection smoke tests, runs the multimodal workflow, then runs the complete HeartTwin test suite across Python 3.10, 3.11, and 3.12.

The CardiacState tests additionally validate the published Draft 2020-12 JSON schema against runtime snapshots.

## Roadmap

1. Expand modality-specific typed observation contracts and artifact references.
2. Replace the reference pseudo-ECG with a validated observation model and integrate it with ElectroTrace outputs.
3. Expand scar modelling to calibrated tissue tensors and border-zone cellular models.
4. Add mechanics/hemodynamics and Echo/CMR anatomy backends.
5. Add posterior/uncertainty objects and surrogate acceleration.
6. Add external CardiBench/CardiEval scientific validation gates.
7. Add persistent jobs/artifacts, compatibility gates, API, and research UI.

A complete software integration is not equivalent to a clinically validated digital twin.

## Scientific limitations

HeartTwin is research infrastructure. It does not diagnose patients or prescribe treatment. The native pseudo-ECG and reference Eikonal backend are integration-grade research components, not clinical measurement or validated medical-device algorithms. Predictions are not causal claims, service availability is not scientific validation, and model outputs require independent evaluation.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).
