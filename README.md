# Virelion-HeartTwin

HeartTwin is the orchestration and integration layer for the Virelion cardiac research software stack. It provides one API/CLI for discovering available services, invoking specialist analyses, exchanging typed state objects, and composing research workflows.

HeartTwin does not duplicate specialist-service algorithms blindly. CardiSim now includes a native, dependency-light cardiac digital-twin reference backend whose architecture is aligned with the audited `juliacamps/Cardiac-Digital-Twin` research pipeline.

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
| CardiSim | synthetic cardiac trajectories and cardiac digital twin | **Native CDT-compatible backend added** |
| CardiStudio | experimental design, synthetic populations, constraints, and power planning | Registered — no adapter yet |
| DCCP | defensive challenge scenarios, OOD assessment, and recovery scoring | Registered — no adapter yet |
| CardiTrace | provenance and reproducibility | Registered — no adapter yet |
| CardiBridge | typed interoperability | Registered — no adapter yet |
| CardiAgent | challenge generation | Registered — no adapter yet |
| CardiVex | challenge evaluation | Registered — no adapter yet |

Registration in `configs/services.yaml` reflects service discovery configuration; scientific validation is separate.

## Architecture

```text
inputs / experiments
        ↓
     HeartTwin
        ↓
service registry + adapters
        ↓
Observation / Anatomy / Context
        ↓
CardiSim digital-twin core
   ├── mesh geometry
   ├── conduction roots
   ├── Eikonal/Dijkstra propagation
   ├── repolarisation
   └── pseudo-ECG observation
        ↓
calibration / uncertainty-ready state
        ↓
CardiBench / CardiEval
        ↓
CardiTrace
```

CardiBridge defines cross-service protocol contracts. CardiTrace records provenance. CardiEval remains the independent evaluation boundary.

## Current implementation

The repository includes:

- typed cardiac-state contracts;
- observation and provenance records;
- service capability registry with HTTP, command, and builtin adapters;
- native CardiSim cardiac digital-twin backend;
- tetrahedral mesh ingestion and validation;
- fibre-aware Eikonal/Dijkstra propagation;
- explicit healthy/border-zone/dense-scar tissue state;
- deterministic pseudo-ECG observation generation for software integration;
- bounded parameter calibration and synthetic recovery tests;
- optional `module:function` bridge for an externally installed upstream CDT wrapper;
- CLI, service configuration, JSON Schemas, and orchestration tests.

## Cardiac-Digital-Twin integration

See `docs/CARDIAC_DIGITAL_TWIN_INTEGRATION.md` for the architecture, provenance, data contract, calibration strategy, and scientific boundary.

The audited upstream reference is `juliacamps/Cardiac-Digital-Twin` at commit `816d51fab0837cfe9e20c7d3a318429e9acf0733`. The upstream repository is MIT licensed. See `hearttwin/cdt_manifest.py` and `THIRD_PARTY_NOTICES.md`.

## Quick start

```bash
pip install -e '.[dev]'
hearttwin services
hearttwin doctor
pytest -q
```

The native digital-twin capability can be invoked through the Python facade:

```python
from hearttwin import VirelionServices, load_registry

v = VirelionServices(load_registry())
result = v.simulate_cardiac_twin(
    "synthetic-001",
    geometry={
        "node_xyz": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "tetrahedra": [[0, 1, 2, 3]],
        "scar_labels": [0, 0, 0, 1],
    },
    root_nodes=[0],
)
```

## Roadmap

1. Native adapters for remaining services.
2. Multimodal state assembly and normalization.
3. Upstream CDT numerical-equivalence fixtures using the published example dataset.
4. Replace the reference pseudo-ECG with a validated observation model and integrate ElectroTrace.
5. Expand scar from scalar conduction multipliers to calibrated tissue tensors and border-zone cellular models.
6. Add mechanics/hemodynamics and Echo/CMR anatomy backends.
7. Add posterior/uncertainty objects and surrogate acceleration.
8. CardiBench/CardiEval external validation gates.
9. Persistent jobs/artifacts, compatibility gates, API, and research UI.

A complete software integration is not equivalent to a clinically validated digital twin.

## Scientific limitations

HeartTwin is research infrastructure. It does not diagnose patients or prescribe treatment. The native pseudo-ECG and reference Eikonal backend are integration-grade research components, not clinical measurement or validated medical-device algorithms. Predictions are not causal claims, service availability is not scientific validation, and model outputs require independent evaluation.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later). See `LICENSE`.

## Citation

Cite the HeartTwin release and the individual service releases used in a workflow. When the CDT-compatible backend or upstream runtime adapter is used, also cite the upstream `Cardiac-Digital-Twin` work and preserve its MIT notice for any upstream source actually distributed.
