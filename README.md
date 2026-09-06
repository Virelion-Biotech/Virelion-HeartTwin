# Virelion-HeartTwin

**The unified research gateway and cardiac digital-twin orchestration layer for the Virelion ecosystem.**

HeartTwin does not replace Virelion's specialist repositories. It composes them behind one stable interface so a researcher can ingest observations, run modality-specific analysis, build a unified cardiac state, invoke learning/simulation, benchmark/evaluate outputs, and preserve provenance in one workflow.

## What this repository is

HeartTwin is a **composition layer**, not a claim of a clinically validated digital twin. Specialist algorithms remain in their source repositories. HeartTwin communicates with them through versioned service contracts and adapters.

```text
                         Virelion-HeartTwin
                                |
                 +--------------+--------------+
                 |     Unified Service API     |
                 +--------------+--------------+
                                |
          +---------------------+----------------------+
          |                     |                      |
       Observe               Learn/Sim              Trust
          |                     |                      |
   Atlas / Trace        CardiLearn / CardiSim   Bench / Eval / Trace
          |
   +------+------+------+------+
   |      |      |      |      |
 Electro  Myo   Opti  Cardio  other services
 Trace   Trace  Cell  Score
          |
          +----------> Unified Cardiac State
```

## Integrated services

| Service | Role in HeartTwin |
|---|---|
| CardiAtlas | knowledge, datasets, evidence and biological context |
| CardiBench | benchmark discovery, manifests and leakage-aware evaluation packages |
| CardiEval | independent model evaluation, statistics and release-grade reports |
| ElectroTrace | electrical/ECG observation and phenotyping |
| MyoTrace | mechanical/contractility observation |
| OptiCell | microscopy QC and visual cellular phenotype |
| CardiScore | MEA/CiPA-aligned cardiac safety scoring |
| CardiLearn | molecular-state representation learning and prediction |
| CardiSim | mechanistic/synthetic cardiac simulation |
| CardiTrace | provenance and reproducibility lineage |
| CardiBridge | typed/versioned interoperability boundary |
| CardiAgent | challenge/task generation |
| CardiVex | challenge/observation characterization |

The adapters are deliberately capability-driven. A service can be local, HTTP, containerized, or absent. HeartTwin reports unavailable capabilities instead of silently substituting or fabricating results.

## Core workflow

```text
input observations
      |
      v
CardiBridge contracts + provenance
      |
      +--> CardiAtlas context
      +--> ElectroTrace electrical features
      +--> MyoTrace mechanical features
      +--> OptiCell visual/QC features
      +--> CardiScore safety features
      |
      v
Unified Cardiac State
      |
      +--> CardiLearn inference
      +--> CardiSim scenarios
      |
      v
prediction / trajectory / scenario
      |
      +--> CardiBench benchmark context
      +--> CardiEval independent evaluation
      +--> CardiTrace lineage
      |
      v
Research report
```

## Scientific boundaries

- Observed, inferred, simulated and externally validated values are represented separately.
- Missing services or modalities are explicit; they are never treated as negative findings.
- HeartTwin does not convert a model score into a clinical diagnosis or treatment recommendation.
- A green software test is not biological validation.
- A prediction is not causal evidence without appropriate experimental validation.

## Quick start

```bash
pip install -e '.[dev]'
hearttwin doctor
hearttwin services
hearttwin demo --output outputs/demo-state.json
```

To connect real Virelion services, edit `configs/services.yaml` or set environment variables for the service endpoints/commands. The default configuration is safe to run without any external services and will show unavailable capabilities explicitly.

## Repository

```text
hearttwin/
  api.py                 public Python API
  cli.py                 unified command line interface
  config.py              service configuration
  contracts.py           versioned cardiac-state contracts
  provenance.py          lineage and content fingerprints
  orchestrator.py        end-to-end workflow engine
  service_registry.py    capability registry and health checks
  adapters/              adapters for every Virelion service
schemas/                  machine-readable contracts
configs/                  default service registry
examples/                 runnable examples
scripts/                  developer utilities
tests/                    unit/integration contract tests
docs/                     architecture and integration guides
```

## Status

This repository is the **integration foundation**. It becomes scientifically meaningful only as the underlying specialist services and their datasets pass their own validation gates.

## License

MIT
