# Scientific audit changes — 2026-10-09

## Behavior

Reject nonfinite validation metrics and empty empirical gate criteria. Add version 1.4 credibility contracts, context-specific cumulative claim checks, data-role conflict rejection, uncertainty records, envelope merging and fingerprint coverage. Preserve canonical flow units.

## Scope and remaining evidence

The envelope is backward-compatible and defaults to unknown; producers must adopt it. Explicit scientific_claims are checked when ServiceResult is constructed and rechecked before canonical state mutation, including direct callers. Admitted claims are retained and rechecked in state snapshots. Structured patient outcomes require an artifact-bound clinical_decision claim. Arbitrary legacy text/statuses and downstream tools are not automatically policed. Declared hashes do not authenticate reports. No scientific evidence is created by these contracts. Empirical claims require listed uncertainty sources; list all material upstream sources in the context.

Integration CI pins the changed components to the audit revisions merged into main. Unchanged component pins are retained. Legacy flow adapters without unit metadata remain unknown rather than receiving inferred units.

## Implementation

- `hearttwin/credibility.py`
- `tests/test_credibility.py`
- `hearttwin/contracts.py`
- `hearttwin/orchestrator.py`
- `hearttwin/state.py`
- `hearttwin/validation_gate.py`
- `tests/test_alignment.py`
- `tests/test_cardiac_state.py`
- `tests/test_cardiac_state_schema.py`
- `tests/test_cross_physics_pipe.py`
- `tests/test_mechanistic_workflow.py`
- `tests/test_validation_gate.py`

## Verification

Regression tests accompany the changes. Repository test results are recorded in the audit completion report and pull request. Software regression checks do not establish numerical, biological, transport or clinical validity.
