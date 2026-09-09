# Virelion-HeartTwin

HeartTwin is the orchestration and integration layer for the Virelion cardiac research software stack. It provides one API/CLI for discovering available services, invoking specialist analyses, exchanging typed state objects, and composing research workflows.

HeartTwin does not duplicate the algorithms implemented by the specialist repositories.

## Integrated services

| Service | Role | Status |
|---|---|---|
| CardiAtlas | biomedical metadata and evidence context | Registered — no adapter yet |
| CardiBench | benchmark definitions and dataset policies | Registered — no adapter yet |
| CardiEval | independent evaluation | Registered — no adapter yet |
| ElectroTrace | ECG/electrophysiology analysis | Registered — no adapter yet |
| MyoTrace | video-based mechanical analysis | Registered — no adapter yet |
| OptiCell | microscopy QC and cell analysis | Registered — no adapter yet |
| CardioScore | MEA-based cardiac safety scoring | Registered — no adapter yet |
| CardiLearn | molecular-state learning | Registered — no adapter yet |
| CardiSim | synthetic cardiac trajectories | Registered — no adapter yet |
| CardiStudio | experimental design, synthetic populations, constraints, and power planning | Registered — no adapter yet |
| DCCP | defensive challenge scenarios, OOD assessment, and recovery scoring | Registered — no adapter yet |
| CardiTrace | provenance and reproducibility | Registered — no adapter yet |
| CardiBridge | typed interoperability | Registered — no adapter yet |
| CardiAgent | challenge generation | Registered — no adapter yet |
| CardiVex | challenge evaluation | Registered — no adapter yet |

Registration in `configs/services.yaml` reflects service discovery configuration only; see `docs/INTEGRATION.md` for what “integrated” requires.

Service availability is configuration-dependent. An unavailable service is reported as unavailable; HeartTwin does not substitute a fabricated result.

## Architecture

```text
inputs / experiments
        ↓
     HeartTwin
        ↓
service registry + adapters
        ↓
CardiStudio / DCCP / CardiAtlas
        ↓
ElectroTrace / MyoTrace / OptiCell / CardioScore
        ↓
CardiacState contracts
        ↓
CardiLearn / CardiSim
        ↓
CardiBench / CardiEval
        ↓
CardiTrace
```

CardiBridge defines cross-service protocol contracts. CardiTrace records provenance. CardiEval remains an independent evaluation boundary.

## Current implementation

The repository currently includes:

- typed cardiac-state contracts;
- observation and provenance records;
- service capability registry;
- health/availability discovery;
- HTTP and local-command adapters;
- unified Python facade;
- CLI;
- service configuration;
- JSON Schemas;
- initial orchestration and contract tests.

Native adapters are added only after the target repository's actual CLI/library/API contract is inspected.

## Quick start

```bash
pip install -e '.[dev]'
hearttwin services
hearttwin doctor
hearttwin demo --output outputs/demo-state.json
```

To obtain local copies of configured services:

```bash
bash scripts/bootstrap_services.sh
```

Configure service endpoints in `configs/services.yaml` using the documented environment variables.

## Python API

```python
from hearttwin import VirelionServices, load_registry

v = VirelionServices(load_registry())
v.design("study-001", specification={})
v.population("study-001", specification={})
v.challenge_assess("study-001", scenario={})
v.electrical("sample-001", input_path="ecg.csv")
v.mechanical("sample-001", input_path="video.mp4")
v.imaging("sample-001", input_path="images/")
v.safety("sample-001", input_path="mea.csv")
v.learn("sample-001", features={})
v.simulate("sample-001", scenario={})
v.evaluate("sample-001", submission={})
```

## Cardiac state model

HeartTwin distinguishes observed, inferred, and simulated values. Missing modalities are represented explicitly rather than interpreted as negative findings. The state contract is intended to become the common integration representation across Virelion services.

## Roadmap

1. Native adapters for each service.
2. Multimodal state assembly and normalization.
3. Experimental-design and challenge-state orchestration.
4. CardiLearn/CardiSim orchestration and temporal state objects.
5. CardiBench/CardiEval evaluation handoffs.
6. CardiTrace run and artifact lineage.
7. Persistent jobs/artifacts, compatibility gates, API, and research UI.

A complete software integration is not equivalent to a clinically validated digital twin.

## Scientific limitations

HeartTwin is research infrastructure. It does not diagnose patients or prescribe treatment. Predictions are not causal claims, service availability is not scientific validation, and model outputs require the validation performed by the underlying specialist repository and study design.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later). See `LICENSE`.

## Citation

Cite the HeartTwin release and the individual service releases used in a workflow.
