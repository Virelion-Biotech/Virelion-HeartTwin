# Virelion-HeartTwin

**One front door for the Virelion cardiac research stack.**

HeartTwin is the orchestration layer and future digital-twin runtime. It composes the specialist Virelion repositories rather than copying their algorithms. A researcher should be able to install HeartTwin, connect the available Virelion services, and use one API/CLI to move from raw observations to a unified cardiac state, learning, simulation, benchmarking, evaluation, and provenance.

## Integrated ecosystem

```text
Research data / experiments
          |
          v
      HeartTwin
          |
  +-------+------------------------------------------------+
  |       |        |        |        |        |             |
Atlas  Electro  MyoTrace OptiCell CardioScore Learn       Sim
  |     Trace      |        |        |        |            |
  +-------+--------+--------+--------+--------+------------+
                          |
                  Unified Cardiac State
                          |
              +-----------+-----------+
              |                       |
           CardiBench              CardiEval
              |                       |
              +-----------+-----------+
                          |
                      CardiTrace
                          |
                     Research run
```

### Services exposed through the gateway

- **CardiAtlas** — evidence, datasets, biological context
- **CardiBench** — benchmark registry and leakage-aware benchmark packages
- **CardiEval** — independent metrics, uncertainty, robustness and statistical comparison
- **ElectroTrace** — electrical/ECG analysis
- **MyoTrace** — contractility/mechanical analysis
- **OptiCell** — microscopy QC and cellular image features
- **CardioScore** — MEA/CiPA-oriented cardiac safety scoring
- **CardiLearn** — molecular-state representation learning and prediction
- **CardiSim** — simulation and scenario generation
- **CardiTrace** — provenance/reproducibility lineage
- **CardiBridge** — typed protocol and compatibility boundary
- **CardiAgent** — challenge generation
- **CardiVex** — challenge/observation characterization

The current public Virelion repositories confirm these roles; for example CardiLearn is a molecular-state learning layer, CardiBench is the benchmark registry, CardiEval independently evaluates submissions, and CardiBridge provides typed/versioned interoperability. fileciteturn7file0 fileciteturn9file0 fileciteturn10file0 fileciteturn8file0

## 0 → 100 roadmap

### 0–20: foundation — implemented in this commit

- strict Pydantic cardiac-state contract
- provenance and deterministic SHA-256 fingerprints
- capability registry
- service health/availability discovery
- HTTP and local-command adapters
- unified Python facade
- unified CLI
- service configuration for the full Virelion stack
- explicit unavailable/error states
- initial tests and JSON Schema

### 20–40: real adapters

Add native adapters for the actual public interfaces of each repository. These adapters must consume/emit the repository's real contracts rather than guessing endpoint names. Where a repository currently has only a CLI/library interface, HeartTwin will use that interface until a formal service API exists.

### 40–60: state fusion

Implement modality-specific normalizers and a real `CardiacStateAssembler`:

```text
molecular ─┐
electrical ├─> normalized observations ─> state vector + missingness mask
mechanical ┤
imaging ───┤
safety ────┘
```

Observed, inferred and simulated values remain separate.

### 60–75: learning + simulation

- CardiLearn representation ingestion
- CardiSim scenario execution
- temporal state/trajectory objects
- uncertainty propagation
- counterfactual research scenarios
- cross-modal consistency checks

### 75–90: validation + provenance

- CardiBench benchmark resolution
- CardiEval submission/evaluation handoff
- CardiTrace run graph
- reproducible run manifests
- dataset/model/service fingerprints
- external-study validation reports

### 90–100: production research platform

- async job execution
- persistent run store
- artifact store
- authentication/authorization
- service version compatibility gates
- web UI/API
- experiment workspace
- multimodal cardiac-state explorer
- reproducible export bundle

**Important:** 100% here means a complete software platform, not a clinically validated digital twin. Biological validity remains an empirical question.

## Quick start

```bash
pip install -e '.[dev]'
hearttwin services
hearttwin doctor
hearttwin demo --output outputs/demo-state.json
```

To obtain the current Virelion service repositories locally:

```bash
bash scripts/bootstrap_services.sh
```

Then configure service endpoints in `configs/services.yaml` using environment variables such as `CARDILEARN_URL` and `CARDISIM_URL`.

## Python API

```python
from hearttwin import VirelionServices, load_registry

v = VirelionServices(load_registry())

# Specialist services through one object
v.electrical("sample-001", input_path="ecg.csv")
v.mechanical("sample-001", input_path="video.mp4")
v.imaging("sample-001", input_path="images/")
v.safety("sample-001", input_path="mea.csv")
v.learn("sample-001", features={})
v.simulate("sample-001", scenario={})
v.evaluate("sample-001", submission={})

# Orchestrate a complete run
run = v.run_twin("sample-001", context={"species": "human"})
```

## Architecture rule

HeartTwin is the **front door**, not the place where every Virelion algorithm is duplicated. Specialist repositories remain independently versioned. CardiBridge is the protocol boundary; HeartTwin is the orchestration/runtime boundary; CardiTrace is the lineage boundary; CardiEval remains independent from model-producing code.

## Scientific safety

HeartTwin is research infrastructure. It does not diagnose patients, prescribe treatment, or establish clinical safety/effectiveness. Missing data are not interpreted as negative findings. Predictions are not causal claims. Service availability is never treated as scientific validity.
