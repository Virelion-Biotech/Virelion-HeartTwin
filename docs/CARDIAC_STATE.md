# Canonical CardiacState

`CardiacState` is the canonical state representation for HeartTwin. `WorkflowState` is an execution view that carries the canonical state plus stage-specific typed outputs used by the orchestration API.

## Why it exists

HeartTwin integrates heterogeneous services: metadata/evidence context, molecular learning, electrophysiology and mechanics analysis, simulation, challenge generation, OOD evaluation, benchmark resolution, interoperability, and provenance. A common state model prevents every service from inventing a separate representation of the same biological entity.

The design follows the modular separation used by established cardiac digital-twin pipelines: geometry/conduction/propagation/electrophysiology/simulation/evaluation are kept as distinct computational responsibilities and connected through explicit interfaces. HeartTwin's implementation is Virelion-native; the upstream `juliacamps/Cardiac-Digital-Twin` repository is an MIT-licensed architectural reference documented in `THIRD_PARTY_NOTICES.md`.

## State layers

```text
CardiacState
├── identity / biological context
├── observations
│   └── measured inputs + provenance
├── atlas_context
├── benchmarks
├── modality_analyses
├── derived_values
│   └── typed variable + unit + region + uncertainty + evidence status
├── simulation_artifacts
├── prediction_artifacts
├── evaluation_artifacts
├── challenges / vex_observations
├── bridge_publications
├── transitions
├── trace_records
└── provenance
```

The compatibility fields `inferred_state`, `simulations`, `predictions`, and `validation` remain available for older callers. New code should write to the typed collections instead of adding arbitrary dictionaries to the canonical state.

## Provenance

Every HeartTwin-produced typed artifact can link to one or more HeartTwin `Provenance.run_id` values. External benchmark/dataset provenance remains inside the benchmark contract and is not treated as a HeartTwin run ID. The store rejects dangling HeartTwin provenance links.

## Fingerprinting

`CardiacStateStore.snapshot()` produces an immutable-by-convention Pydantic copy and records a SHA-256 `state_fingerprint`. The fingerprint excludes the fingerprint field itself, so the value is stable for a given state snapshot and can be independently recomputed.

## Phase history

The state supports explicit phases such as `baseline`, `injury`, `acute`, `remodeling`, `recovery`, `intervention`, `post_intervention`, `simulated`, and `validated`. `StateTransition` records the trigger, sequence, timestamp, provenance links, and optional details.

Current integration workflows use `baseline -> simulated -> validated` for synthetic infrastructure tests. This is a software execution state, not a clinical inference about a real patient.

## Validation boundary

The state layer validates structural invariants: IDs, typed artifacts, provenance links, state-phase consistency, and JSON serialization. It does not claim biological truth. Scientific validity still requires independent datasets, appropriate experimental design, leakage controls, external validation, and domain-specific evaluation.

## Schema

The versioned JSON schema is `schemas/cardiac-state-1.1.0.schema.json`. The previous 1.0.0 schema is retained for compatibility. New producers should emit `contract_version = 1.1.0`.
