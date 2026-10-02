# HeartTwin operational rehearsal

The operational rehearsal is a deployment-style integration test for HeartTwin.
It is intentionally separate from scientific validation.

It answers:

> Can a HeartTwin case move through a distributed service topology, survive
> common operational failures, preserve identity/state/provenance, and resume
> without duplicating work?

## Run locally

Install HeartTwin and the pinned Virelion services, then run:

```bash
hearttwin operational-rehearsal \
  --workdir outputs/operational-rehearsal \
  --case-id HT-OPS-LOCAL
```

The command writes:

- `journal.sqlite3` — durable case/stage/event journal and job queue.
- `artifacts/` — content-addressed output artifacts.
- `cardibridge.sqlite3` — persistent CardiBridge event state.
- `carditrace/` — CardiTrace output.
- `logs/` — one log file per distributed service worker.
- `rehearsal-summary.json` — machine-readable acceptance summary.

## Topology

The coordinator starts one localhost HTTP process per registered Virelion service,
except HeartTwin's built-in reference CardiacDigitalTwin.

The worker wraps the real service adapter. Native packages stay native inside their
worker process; command-backed services still execute their real adapter command.

The coordinator itself uses an endpoint-only `ServiceRegistry`, so calls cross an
actual HTTP boundary instead of falling back to in-process native dispatch.

CardiBridge is also placed behind its own worker. During the rehearsal its
`agent.challenge` consumer calls the CardiVex worker over HTTP, so that handoff
crosses a second process/network boundary.

## Durable state

### Case journal

`CaseJournal` stores:

- immutable case input fingerprint;
- case status and attempts;
- stage idempotency key;
- stage status/attempts/result/error;
- ordered operational events.

A completed stage with identical input is replayed from the journal. Reusing the
same completed stage with different input fails closed.

### Job queue

`JobQueue` is a SQLite lease-based queue.

The rehearsal deliberately:

1. enqueues a workflow job;
2. lets worker A lease it;
3. simulates worker A dying without acknowledgement;
4. lets the lease expire;
5. requires worker B to reclaim the same job ID;
6. verifies the attempt counter increments;
7. completes the job through worker B.

SQLite `BEGIN IMMEDIATE` claim semantics are covered by a concurrency regression
test to prevent two workers leasing the same queued job.

### Artifact store

`ArtifactStore` writes canonical JSON atomically to content-addressed paths.

The rehearsal deliberately corrupts the final workflow artifact, verifies the
corruption is detected, recreates the artifact, verifies its digest/size again,
and only then allows the case to complete.

## HTTP behavior

Distributed service calls carry a deterministic
`X-HeartTwin-Idempotency-Key` derived from service, capability, and payload.

The real HeartTwin HTTP adapter:

- retries transient connection/timeout/5xx errors;
- does not retry 4xx responses;
- reuses the same idempotency key across retries;
- has bounded retry count, timeout, and backoff settings.

Command-backed services execute argument arrays with `shell=False`.

The rehearsal worker caches successful requests by idempotency key so a repeated
request does not execute twice.

## Fault injection

A successful rehearsal currently requires all of the following:

- every distributed worker starts healthy;
- the wrapped adapter itself is available;
- a forced CardiAtlas HTTP timeout is observed;
- CardiAtlas is killed;
- its endpoint becomes unreachable;
- the worker is restarted on the same endpoint;
- a real call succeeds after restart;
- a duplicate CardiAnatomy request is served from the idempotency cache;
- an expired queue lease is reclaimed by a second worker;
- the distributed multimodal workflow completes;
- electrical, mechanical, imaging, and safety specialist observations execute;
- non-mainline branches execute over HTTP for:
  - CardiAnatomy,
  - CardiEP,
  - CardiInfer,
  - CardiStudio,
  - DCCP;
- replaying the completed workflow stage uses the durable cached result;
- artifact corruption is detected and repaired;
- a newly opened `CaseJournal` sees the completed case and fault events;
- the full HeartTwin test suite still passes after all fault injection.

## Required summary flags

`rehearsal-summary.json` must report:

```json
{
  "status": "ok",
  "timeout_observed": true,
  "worker_restart_recovered": true,
  "duplicate_suppressed": true,
  "artifact_corruption_detected": true,
  "durable_replay_reused": true,
  "queue_lease_recovered": true
}
```

It also records the worker endpoint map, workflow run ID, canonical state
fingerprint, queue job ID/attempts, distributed branch probes, journal counts,
and output artifact record.

## CI

`.github/workflows/operational-rehearsal.yml` runs this topology on Python 3.11
with the exact revisions in `requirements-services.txt`.

It uploads the summary, SQLite journal, and worker logs as GitHub Actions
artifacts even when the job fails.

The workflow is path-scoped, manually dispatchable, and runs weekly.

## What this does not prove

This test establishes operational continuity of the current software topology.
It does not establish:

- clinical validity;
- biological validity;
- distributed performance at production scale;
- GPU-cluster scheduling behavior;
- cloud object-store semantics;
- multi-region failover;
- regulatory compliance;
- security of a public internet deployment.

Those require separate validation/load/security/deployment programs.
