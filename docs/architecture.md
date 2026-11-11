# Architecture

Nightjar is a control plane, not an agent framework. The agent roles are small,
bounded, and surrounded by deterministic components that own every decision
that matters.

```
                    ┌──────────────────────────────────────────────┐
                    │                  API / UI                    │
                    └───────────────┬──────────────────────────────┘
                                    │ typed HTTP
                    ┌───────────────▼──────────────────────────────┐
                    │            IncidentService                   │
                    │ intake → investigate → critique → plan →     │
                    │ policy → approve → execute → verify → prove  │
                    └──┬─────────┬──────────┬──────────┬──────────┘
                       │         │          │          │
              ┌────────▼──┐  ┌───▼────┐ ┌───▼────┐ ┌───▼────────┐
              │  Agents   │  │ Policy │ │Connector│ │ Verifier  │
              │ triage    │  │ local  │ │registry │ │ HTTP/JSON  │
              │investigate │  │ + Rego │ │ typed   │ │ DNS/TLS    │
              │ critic     │  └────────┘ └─────────┘ │ connector  │
              │ planner    │                          └────────────┘
              └────────────┘
                    │
          ┌─────────▼──────────┐
          │  Evidence ledger   │  hash-chained evidence + timeline
          │  PostgreSQL 18     │  plans, actions, approvals, executions
          └────────────────────┘
```

## Components

### Core domain (`src/nightjar/core`)

- `models.py` — Pydantic models with `extra="forbid"`. Unknown fields are errors.
- `actions.py` — the v1 typed action catalogue, risk classes, required
  parameters, and prohibited names.
- `policy.py` — local fail-closed policy engine and an OPA client. Both
  engines return identical decisions, reasons, matched rules, and approval
  requirements. Parity is enforced by tests.
- `verification.py` — HTTP, JSON, DNS, TLS, and connector checks. All network
  probes pass the same SSRF guard.
- `evidence.py` — canonical JSON hashing, chain sealing, and verification.
- `redaction.py` — secret and PII redaction before evidence is stored or sent
  to a model.

### Agent roles (`src/nightjar/agents`)

Four roles, each with a typed input and output:

1. **Triage** — classifies the incident and severity from the intake record.
2. **Investigator** — cites evidence, requests bounded read-only probes, and
   forms findings.
3. **Critic** — independently checks every claim against the evidence ledger.
4. **Planner** — proposes typed actions and verification predicates.

`MockAgentRuntime` is a deterministic rules-based implementation used by CI and
the evaluation baseline. `PydanticAgentRuntime` wraps PydanticAI V2 and
supports OpenAI, Anthropic, Google, and Ollama. Provider selection changes no
domain code, prompt schema, or test.

### Connectors (`src/nightjar/connectors`)

Connectors translate typed actions and probes into provider calls. The
registry refuses ambiguous mappings, duplicate tool names, unknown read tools,
and oversized parameters. `http` is read-only; `lab` is the synthetic
environment; `github` and `cloudflare` are real adapters; `webhook` sends
notifications.

### API (`src/nightjar/api`)

FastAPI application with bearer auth, CORS, health/readiness, incident CRUD,
case file, timeline, SSE stream, chain verification, notes, retry,
verify-now, approvals, and meta. `IncidentService` is the single orchestrator
used by both the inline pipeline and Temporal activities.

### Worker (`src/nightjar/worker`)

Optional Temporal orchestration. The workflow owns the state machine:
investigate → await approval signal → execute → sleep stability window →
verify. Activities are one-attempt wrappers around the service. The inline
orchestrator remains the default and is what tests and evals use.

### Evaluation (`src/nightjar/evals`, `src/nightjar/lab`)

CornerShop is a synthetic storefront with a CDN cache, deployment pipeline,
checkout dependency, credential store, and mail gateway. The evaluator starts a
private lab, runs the real HTTP API, and scores every case. See
[evals.md](evals.md).

## Evidence ledger

Every evidence item and timeline event is sealed with:

```
content_hash = SHA-256(canonical_json(content))
hash = SHA-256(prev_hash || "\n" || canonical_json(payload))
```

The first entry uses `prev_hash = ""`. Any content edit, deletion, or reorder
breaks chain verification. Evidence and timeline chains are verified
independently and reported on every case file.

## Policy

Policy is deterministic and fail-closed. The reference implementation is
`policies/nightjar.rego`; the local engine mirrors it exactly so Nightjar can
run without OPA. Rules:

| Risk | Meaning | Default |
| --- | --- | --- |
| R0 | Read-only | Allow |
| R1 | Reversible, no customer impact | Allow at confidence ≥ 0.70 and blast radius ≤ 1 |
| R2 | Reversible, customer impact possible | Require one approval |
| R3 | Irreversible | Deny unless break-glass with two approvals |

No evidence, confidence below 0.50, or an active change freeze denies every
mutating action. Denied actions do not report approval reasons or rules.

## Safety invariants

1. Models cannot execute; only the executor does, and only through typed
   actions.
2. Every action has a registered risk and required parameters.
3. Untrusted content is redacted, labelled as data, and never parsed as
   instructions.
4. Approval is required before R2 executes; the approval record is scoped to the
   latest plan and expires.
5. Resolution requires required predicates to pass at T+0 and through the
   stability window.
6. Evidence and timeline are append-only and hash-chained.
7. The executor and investigator are separate roles; the critic is independent
   of the investigator.
8. Policy failures and engine errors deny.

## Data model

| Table | Purpose |
| --- | --- |
| `tenants` | local tenant boundary |
| `incidents` | incident state and counters |
| `timeline_events` | hash-chained operator/system timeline |
| `evidence` | hash-chained observations |
| `plans` | proposed remediation plans |
| `actions` | plan actions, sequence unique per plan |
| `approvals` | expiring human approval records |
| `executions` | before/after state for each executed action |
| `verifications` | predicate results by phase |
| `agent_runs` | provider, model, tokens, cost, latency |
| `notes` | operator annotations |

Actions are scoped to their plan. A retry creates a new plan; older plans remain
in history and the current plan is what policy and execution consider.

## API surface

See [api.md](api.md) for the full endpoint reference.

## Failure handling

- Model errors fail the incident closed; they never produce a plan.
- Policy engine errors deny.
- Verification errors are recorded as failed predicates and reopen.
- Approval expiry cancels pending actions and fails the incident.
- Execution errors record before/after state and fail the incident.
- The stability scheduler retries due verifications and logs crashes without
  marking the incident resolved.
