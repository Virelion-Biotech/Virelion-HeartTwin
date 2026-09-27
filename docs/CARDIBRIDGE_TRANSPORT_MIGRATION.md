# CardiBridge transport-layer migration

## Current decision

HeartTwin uses CardiBridge as the typed, durable transport for message workflows that CardiBridge actually contracts today (Agent/Vex/Eval-family messages). HeartTwin retains its thin direct HTTP adapter for ordinary service RPC until CardiBridge owns typed request/response contracts and explicit handlers for those capabilities.

This is intentional. The current CardiBridge production router is fail-closed and dispatches only registered `(message_type, consumer)` handlers. Replacing HeartTwin's HTTP path with a catch-all forwarder would weaken that property.

## Why a generic proxy is not being added

A generic `service.request` envelope plus arbitrary URL forwarding would introduce:

- an untyped escape hatch around CardiBridge's contract registry;
- SSRF / destination-control risk unless destinations are statically allow-listed;
- ambiguous retry and idempotency semantics for non-idempotent services;
- duplicated HTTP authentication/configuration inside CardiBridge;
- loss of capability-specific validation.

That is worse than the current direct, explicit HeartTwin endpoint configuration.

## Promotion criteria

CardiBridge can become the default HeartTwin service transport after all of the following exist:

1. Versioned request and response contracts for each transported capability family.
2. A static consumer registry mapping service identity to an approved handler, not arbitrary URLs from payloads.
3. Defined idempotency behavior per operation.
4. Propagated HeartTwin execution/provenance context and artifact references.
5. Authentication/authorization for gateway callers and downstream consumers.
6. Retry/dead-letter rules that distinguish safe retries from non-idempotent actions.
7. Contract tests proving direct and CardiBridge-routed calls are semantically equivalent.
8. A staged migration flag so direct HTTP remains available during compatibility rollout.

## Current architecture

- Specialist local commands: direct HeartTwin command adapter.
- Native Python integrations: direct typed native adapter.
- Deployment HTTP fallback: HeartTwin's explicit configured endpoint and `path_template`.
- Agent → CardiVex message delivery: CardiBridge router/gateway.
- Missing CardiBridge consumer: hard failure; no automatic no-op consumer is created.

This document is the design gate for the broader transport migration. Implementing a generic proxy before these criteria are met is explicitly out of scope because it would reduce reliability and security rather than improve integration.
