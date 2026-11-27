# Nightjar roadmap

Last updated: 2026-11-24

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
- [x] **Phase 6 — production execution surface** (complete; see the
      [readiness plan](docs/plan/real-world-readiness.md))
- [x] Phase 7 — multi-process and identity
- [ ] Phase 8 — evidence for skeptics (budgets, corpus, nightly workflow,
      backups and runbooks shipped; recorded hosted-model baselines need keys)
- [ ] Phase 9 — 0.1 public launch (artifacts and docs shipped; the tag is pending)

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

- Pixel-diff baselines are deliberately not gated: the background is an animated
  canvas and the type is dense sub-pixel, so a committed baseline would encode
  the rasterizer rather than the product. Screenshots are uploaded per run for a
  human to review, and axe fails the build on critical or serious violations.

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

### Phase 6 — Production execution surface (complete)

The full work order, with endpoints, scopes, verification predicates, file paths,
tests, and estimates, is [docs/plan/real-world-readiness.md](docs/plan/real-world-readiness.md).

The gap was execution, not orchestration. Every v1 action type now has at least
one real provider: containment and revocation on both mail platforms, rollback on
three deploy platforms, restart on two, and a verified manual step where no API
exists. The hardening items below are shipped; the remaining provider work is
live-account validation, not reach.

1. **Safe defaults.** ✅ Production refuses the lab connector, private probes, a
   missing token, an empty target allowlist, and a short stability window.
2. **Connector SDK v2.** ✅ Capability report and an availability-aware planner;
   typed errors, local idempotency, per-provider rate limits, and the redaction
   registry are open.
3. **Credential revocation.** ✅ GitHub PAT/OAuth/App tokens via the
   unauthenticated revocation API, proven dead by a `401` probe. ✅ Microsoft
   sessions are revoked through Graph and proven from `signInActivity`, failing
   closed when the tenant lacks audit access. Google OAuth revocation is open.
   ✅ Stripe has no public revoke API, so it is
   a typed manual step: Nightjar shows the exact Dashboard action and fingerprint,
   pauses the incident, and resolves only after the exposed key returns a `401`
   (Basic auth, because a live key would also reject Bearer). ✅ Manual steps are
   visible in the case file, attestation is recorded as `human_note` evidence,
   and the predicate — not the human's word — decides resolution.
4. **Mail containment.** ✅ Google Workspace labels and filters, and Microsoft 365
   message moves, inbox rules, and session revocation, all read back before the
   incident can resolve. A Graph move returns a new message id and the verifier
   handles both; session proof reads `signInActivity` and fails closed when the
   tenant cannot provide it.
5. **Deploy and runtime.** ✅ Cloudflare Pages, Vercel, and Render rollback, each
   validated against a successful production deployment and read back from the
   provider (Vercel also checks the production alias). ✅ Render and Fly restart.
   Render rollback pauses autodeploy and requires it to still be off at
   verification, because the next push would otherwise undo the fix. Fly restart
   requires the machine to be `started` with an `updated_at` newer than the
   request.
6. **Capability probe.** ✅ `nightjarctl doctor` and `GET /v1/meta/capabilities`
   so an operator sees which providers are live and which scopes are missing
   before an incident.
7. **Managed targets.** ✅ A policy-enforced allowlist of zones, repos, mailboxes,
   projects, and apps, identical in the local and Rego engines. The model may name
   a target; it may not invent one.
8. **Required proof.** ✅ A plan for each class must carry the proof that class
   needs: user-visible and functional checks for an outage, a credential-bearing
   predicate for an exposure, and both containment checks for phishing. Only
   required predicates count, so a plan cannot weaken its way to resolution.
9. **Signed intake.** ✅ Alert sources file incidents through
   `POST /v1/intake/webhook` with an HMAC over the exact body, a fresh timestamp,
   a body cap, and replay rejection. With no secret configured the endpoint is
   disabled, so there is no anonymous way to file an incident.

### Phase 7 — Multi-process and identity

- Postgres advisory locks for per-incident work; scheduler leader election. ✅
- Derived idempotency keys and a replay path, so a duplicated approval decision or
  retried worker cannot mutate a provider twice. ✅
- A shared intake replay store, so a captured delivery replayed at a second
  replica is rejected. ✅
- OIDC authentication with a JWKS cache and asymmetric-only algorithms; the actor
  is the verified token identity, not a header. ✅
- Separation of duties for approvals, opt-in so a lone operator is not locked out
  of their own incident. ✅
- Group-to-role mapping through OIDC approver groups, rate limits shared across
  replicas, request size caps, and request IDs. ✅ (Provider token budgets stay
  per process; the caller-facing API budget can share state in PostgreSQL.)
- SSE over `LISTEN/NOTIFY` with the poll as fallback. ✅

### Phase 8 — Evidence for skeptics

- ✅ Per-incident cost, wall-clock and call ceilings with a hard stop. A stop
  records `incident.budget_exceeded` and fails before any action is planned.
- ✅ Ten-payload prompt-injection corpus across page, deployment, credential,
  mail and intake surfaces; the injection suite reaches every payload.
- ✅ Nightly workflow: the deterministic baseline always runs; hosted providers
  run when keys are configured, behind a per-run cost cap.
- ✅ Chaos tests for timeouts, dropped connections, 429s and 500s: an ambiguous
  failure is never retried, and the ledger stays valid either way.
- ✅ Eval gates for unavailable actions and manual-step visibility before approval.
- ✅ Backup/restore verification, migration upgrade tests, Compose smoke test.
- ✅ Operator runbooks for the three v1 incident classes.
- ◐ Baseline recorder and case-level regression gate shipped, with the
  deterministic baseline committed; recorded hosted-model baselines per provider
  still need provider API keys.
- Nightly live-connector suite against sandbox accounts (needs sandbox tenants;
  this is separate from the hosted-model evaluation above).

### Phase 9 — Public launch

- Publish 0.1 with a pinned changelog and upgrade note. (Artifacts are wired and
  gated on the first `v*.*.*` tag.)
- ✅ Signed images, SBOM, PyPI workflow, CodeQL, Dependabot, code of conduct.
- ✅ Docs pass, demo recording, accessibility gate and screenshot artifacts.
- ✅ Opt-in OpenTelemetry export for operators who run a collector. There is no
  phone-home telemetry and none is planned for v1.

## Non-goals for v1

- Arbitrary shell execution or autonomous code edits.
- Money movement, billing changes, or destructive data operations.
- Multi-tenant SaaS control plane.
- Kubernetes-first diagnostics as the default path.
- A proprietary model gateway or hidden evaluation tier.
