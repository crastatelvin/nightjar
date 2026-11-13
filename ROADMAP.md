# Nightjar roadmap

Last updated: 2026-11-13

Nightjar is a verification-first incident response agent for small organizations.
The product thesis is simple: an incident is not resolved because a model says so.
It is resolved when independently observed predicates pass, and the evidence for
that claim is tamper-evident.

## Status

- [x] Typed action catalogue and schema validation
- [x] Deterministic policy engine with an OPA/Rego reference policy
- [x] Hash-chained evidence and timeline ledger
- [x] CornerShop synthetic SMB environment (5 failure modes × 6 variants)
- [x] Closed incident loop: intake → investigate → critique → plan → policy →
      approve → execute → verify → resolve/reopen
- [x] Provider-agnostic agent runtime (mock, OpenAI, Anthropic, Google, Ollama)
- [x] Optional durable Temporal orchestration with approval signals
- [x] CornerShop-30 evaluation plus relapse-6 and injection-10 suites
- [x] Apache-2.0 + DCO, security policy, threat model
- [x] Docker Compose, CI, and release checks
- [x] **Phase 5 — the living case file** (core complete; see below)
- [ ] 0.1 public launch: real connectors, docs site pass, publish pipeline, changelog

## v1 scope

Nightjar handles three incident classes end to end:

1. Site / checkout outage
2. Exposed credential
3. Phishing triage

It does not run arbitrary shell, move money, change code autonomously, manage
Kubernetes, or act as a multi-tenant SaaS. Small teams should be able to read
the action catalogue and know exactly what Nightjar can do.

## Phases

### Phase 1 — Foundation (complete)

Evidence ledger, typed actions, policy engine, verification engine, repository
schema, API, CornerShop lab, and the deterministic mock runtime. This is the
trust layer; everything else is a presentation of it.

### Phase 2 — Closed loop (complete)

Triage, bounded investigation, independent critique, remediation planning,
policy gate, approval workflow, execution, T+0 verification, and stability
verification. Relapse reopens the incident instead of reporting success.

### Phase 3 — Durability and measurement (complete)

Optional Temporal workflow with one-attempt activities and approval signals.
CornerShop evaluation with explicit false-resolution, policy-violation,
injection-resistance, cost, and latency metrics.

### Phase 4 — Ship safely (complete)

Docker images, Compose stack, CI gates for lint/type/test/OPA/eval, security
checklist, threat model, ADRs, governance, and DCO.

### Phase 5 — The living case file (core complete)

The web UI is not a dashboard bolted onto the API. It is the case file: the
artifact a tired operator reads at 03:00 to decide whether to trust the
machine. Every animation must carry information or calm the operator; nothing
decorates for its own sake.

Delivered:

1. Next.js 16 + React 19 + Tailwind 4 shell, rail navigation, command palette.
2. Design system: dark glass, accent aurora, semantic status colors, motion tokens.
3. Command deck: live KPI counters, incident stream, decision queue, proof seal.
4. Case file: lifecycle stepper, findings and claim verdicts, plan and approval
   panel, verification results, evidence ledger, chain integrity, model calls,
   operator notes, SSE live updates with a polling fallback.
5. Fault studio: five scenarios, six variants, injection corpus, relapse toggle,
   live lab audit, reset.
6. Settings: API base, bearer token, actor, connection probe, action catalogue.
7. Ambient canvas, cursor glow, pointer tilt, staggered reveals, tactile press,
   reduced-motion path, responsive layout, keyboard palette.
8. `npm run typecheck` and `npm run build` in CI; standalone image in Compose.
9. Playwright smoke test over all four routes against the real lab, API, and
   standalone build: live stream updates, the approval path, the demo proxy, and
   the connection probe. It runs in CI and uploads traces on failure.

Remaining:

- Screenshot regression baselines, once the layout settles for 0.1.

Performance and motion requirements:

- **120 fps ceiling.** Animation runs on `requestAnimationFrame`; transforms and
  opacity only; no layout or paint work on the main thread. No `setInterval`
  driving visuals. Canvas scenes are DPR-aware and pause when the tab is hidden.
- **Motion budget.** Entrances ≤ 450 ms, interactions ≤ 180 ms, springs settle
  under 600 ms. Nothing loops forever except ambient background with a low CPU
  floor.
- **Micro-detail.** Hover tilt and specular sweep on cards; status badges with
  state-driven pulse; timeline nodes draw themselves; verification predicates
  animate a drawn check/cross; hash chains link with a travelling pulse; KPI
  numbers tween rather than snap; approval decisions produce a directional
  spring response.
- **Alive, not noisy.** A particle/aurora field reacts to the pointer; cursor
  glow is soft and delayed; panels stagger in by 40 ms; motion respects
  `prefers-reduced-motion` by collapsing to opacity-only transitions.
- **State legibility.** Running, awaiting approval, verifying, resolved, and
  reopened are visually distinct at a glance. False resolution is impossible to
  miss: the incident stays red until the lab and predicates agree.
- **Evidence first.** Every claim shows its hash, its source, and a verifier
  result. The UI never presents an unreachable or unverifiable state as green.

### Phase 6 — Public launch

- Publish 0.1 with a pinned changelog and upgrade note.
- Add an anonymous opt-out telemetry toggle (off by default) once the schema
  exists; keep the ledger local-first.
- Expand connectors: Stripe test-mode verification, mail provider adapters,
  Cloudflare and GitHub actions as real, non-simulated operations.
- Operator runbooks for the three v1 incident classes.

## Non-goals for v1

- Arbitrary shell execution or autonomous code edits.
- Money movement, billing changes, or destructive data operations.
- Multi-tenant SaaS control plane.
- Kubernetes-first diagnostics as the default path.
- A proprietary model gateway or hidden evaluation tier.
