# CornerShop as the evaluation environment

- Status: accepted
- Date: 2026-11-10
- Deciders: Nightjar maintainers

## Context

Evaluating an incident agent needs faults that are real enough to require
investigation and safe enough to run in CI hundreds of times. Options were: mock
the connectors (fast, but tests nothing end to end), run against a real cloud
account (expensive, flaky, dangerous), or build a synthetic environment with real
HTTP surfaces.

Public benchmarks do not exist for this product shape. Generic agent benchmarks
measure task completion, not verification honesty, and none of them include a
false-resolution metric.

## Decision

Build **CornerShop**: a small FastAPI storefront with a CDN-like cache, a
deployment pipeline, a checkout dependency backed by a real PostgreSQL database, a
credential store, and a mail gateway.

- Five fault modes, six variants each, with dynamic hostnames, service names,
  credential ids, message ids, and versions.
- Faults are injected via `/lab/scenario/{name}` and observed via `/lab/audit`.
  The audit endpoint is the ground truth for the false-resolution metric.
- A stability-relapse mode reintroduces the fault after T+0.
- A prompt-injection corpus is embedded in every untrusted surface and exposed
  through the same read probes a real incident would use.
- The harness starts a private lab process per run, runs the real HTTP API, and
  scores the case files. It does not call service internals.

Suites: `cornershop-30` (core), `relapse-6` (stability honesty), `injection-10`
(untrusted content).

## Consequences

- The evaluation is deterministic, fast (46 cases in ~41 s), free, and safe in CI.
- False resolution is measurable: audit health is independent of incident status.
- The lab is also a demo and a manual test bed (`nightjar-lab`).
- Synthetic faults are simpler than production. Passing the suite is necessary,
  not sufficient. Real connectors and real incidents are tracked as launch work.
- Variants reduce fixture-keying but cannot eliminate overfitting; the suite is
  public and should be extended as failure modes are discovered.

## Alternatives considered

- **Mocked connectors only.** Rejected: cannot measure false resolution.
- **Real cloud sandbox.** Rejected: cost, flakiness, and blast radius.
- **Existing benchmarks.** Rejected: wrong shape and no verification metric.
