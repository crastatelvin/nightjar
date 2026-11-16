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
- `secrets.py` — `env:` / `file:` vault references, fingerprints, and hints, so
  a credential is referenced by an action but never stored with it.
- `capabilities.py` — what each connector can do, what each v1 incident class
  needs, and whether the gap is closed right now.

### Agent roles (`src/nightjar/agents`)

Four roles, each with a typed input and output:

1. **Triage** — classifies the incident and severity from the intake record.
2. **Investigator** — cites evidence, requests bounded read-only probes, and
   forms findings.
3. **Critic** — independently checks every claim against the evidence ledger.
4. **Planner** — proposes typed actions and verification predicates. Its prompt
   carries the deployment's real capability catalogue, and a plan naming an
   action no connector implements fails before approval, not at the provider.

`MockAgentRuntime` is a deterministic rules-based implementation used by CI and
the evaluation baseline. `PydanticAgentRuntime` wraps PydanticAI V2 and
supports OpenAI, Anthropic, Google, and Ollama. Provider selection changes no
domain code, prompt schema, or test.

### Connectors (`src/nightjar/connectors`)

Connectors translate typed actions and probes into provider calls. The
registry refuses ambiguous mappings, duplicate tool names, unknown read tools,
and oversized parameters. `http` is read-only; `lab` is the synthetic
environment; `github`, `cloudflare`, `google`, `microsoft`, `vercel`, `render`,
`fly`, and `stripe` are real adapters; `webhook` sends signed notifications.

Deploy actions are read back rather than trusted. Cloudflare under-attack is
verified by re-reading the zone's security level, and a Pages rollback is
verified against the production deployment list after refusing any target whose
`latest_stage.status` is not `success`. A Vercel rollback refuses a target that
is not `READY` and is verified by deployment state plus the production alias. A
Render rollback pauses autodeploy and requires both the target deploy to be live
and autodeploy to still be off. A Fly restart requires the machine to be
`started` with an `updated_at` at or after the request, because the restart
response arrives before the machine is back and a stale timestamp means the
request never reached the allocator. A cache purge has no reliable read-back in
the Cloudflare API, so it is verified by an HTTP predicate on the purged URL.

Managed-target vocabulary follows the provider: Cloudflare Pages and Vercel use
`projects`, Render uses `services`, Fly uses `apps`. An operator's allowlist
therefore reads the same way as the provider's console.

The planner names an action, not a vendor. `IncidentService` binds the one
configured connector that implements an action before policy runs, so the
provider policy authorizes is the provider that executes. If two connectors
could do it and the plan named neither, the incident fails before approval.

Some fixes have no API. A capability can declare `automated=False`, and the
service then records the human step instead of calling `execute`: the action
becomes `awaiting_manual`, the incident pauses, and verification is what proves
the human acted. Stripe key expiry is the first such step; its probe presents the
key with Basic auth, because Stripe would reject a live key sent as a Bearer
token and a false `401` would look like a revocation.

Attestation is evidence, not authority. A human completing a manual step records
a `human_note` evidence item, but the service then runs the same verification
path; a failed predicate reopens the incident and the step can be attested again
after the real fix.

`validate_plan` also enforces the proof each class needs before policy runs: a
site outage must carry a user-visible HTTP check and a functional check, a
credential exposure must carry a predicate that presents the credential, and
phishing must carry both containment checks. Only `required` predicates count,
so a plan cannot weaken its way to resolution with an optional check.

The Google Workspace connector uses a service account with domain-wide
delegation and impersonates one managed mailbox per request, so Nightjar never
holds a user password or refresh token. Quarantine is a label plus removal from
`INBOX`/`UNREAD`; sender blocking is a Gmail filter. Both are read back by the
verifier, and an existing filter is replayed rather than duplicated.

The Microsoft 365 connector uses an Entra app with admin-consented application
permissions and no user credential at all. Quarantine is a move into a
`Nightjar Quarantine` folder, and Graph answers a move with a **new** message id,
so the action returns `new_message_id` and the verifier tries the original and
the new id, treating a `404` as "this id is gone, try the next one". Sender
blocking is an inbox `messageRules` entry read back by display name and sender.
Session revocation is proven from `signInActivity`: a check that cannot read it
fails closed with the `AuditLog.Read.All` + Entra ID P1 requirement instead of
trusting the API's `true`. Verification never creates the quarantine folder; a
missing folder is a failed predicate, not a side effect. Every check converts a
connector error into a verification failure, so a provider outage is a failed
proof rather than a crashed verifier.

Each connector declares `capabilities()` — the actions it can really perform and
the kinds of target they need — and implements `preflight()`, which answers "can
you do your job right now?" with a verdict, an identity, and the scopes that are
missing. `core/capabilities.py` turns those answers into a report: which actions
have a healthy provider, which are missing, and therefore whether each v1
incident class can actually be closed. `nightjarctl doctor` and
`GET /v1/meta/capabilities` expose the same report.

The lab connector never advertises an action that a configured real connector
already implements, so a plan cannot name `connector: "lab"` to get a synthetic
"revoked" result while the credential is still live at the provider.

### API (`src/nightjar/api`)

FastAPI application with bearer auth, CORS, health/readiness, incident CRUD,
case file, timeline, SSE stream, chain verification, notes, retry,
verify-now, approvals, manual steps, and meta. `IncidentService` is the single
orchestrator used by both the inline pipeline and Temporal activities.

Alert intake is the one mutating route that does not take a bearer token. It is
authenticated by an HMAC-SHA256 over `"<timestamp>.<body>"`, compared in constant
time, with a 300 s timestamp window, a body cap, and a delivery-id replay guard.
An unset secret disables the route with `503`, so a deployment cannot
accidentally accept anonymous incidents. The replay guard is a row in
`intake_deliveries` claimed with `INSERT ... ON CONFLICT DO NOTHING`, so a capture
replayed at a second replica is rejected by the database rather than by one
process's memory; a failed filing releases the claim instead of burning the
delivery id.

Per-incident work is serialized across processes with PostgreSQL session-level
advisory locks on a dedicated connection (a `NullPool` engine, because a pooled
connection commit would silently drop the lock). The stability scheduler takes a
separate advisory lock for each iteration, so only one replica runs due
verifications and a crashed leader costs one tick. A busy incident returns `409`
with `Retry-After: 2`; a background spawn that finds the lock taken simply stops,
because another process already owns that work. `NIGHTJAR_DB_LOCKS=false` keeps
the in-process lock for single-process development.

### Operator CLI (`src/nightjar/cli`)

`nightjarctl doctor` probes every configured provider, prints the capability
table, and exits non-zero when a v1 incident class has no healthy provider.
`--json` is for CI, `--deep` also sends one signed webhook ping.

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

### Managed targets

Every action is resolved to a provider, a target kind, and a target value
(`core/targets.py`): a cache purge lands on `cloudflare/zones/<zone>`, an issue
on `github/repos/<owner>/<repo>`, a message on `google/mailboxes/<address>`.
Policy denies the action unless `NIGHTJAR_MANAGED_TARGETS` contains that
provider and value (or `"*"` for that kind). Revocation and notification have no
destination of their own, so the provider entry alone is enough.

Both engines implement the same rule and the same reason string; parity is
tested with allow, deny, wildcard, provider-only, and empty-target cases. An
empty allowlist means unmanaged development mode, and production refuses to
start that way.

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
9. No action can reach a provider, zone, repository, mailbox, project, or app
   that is not in the operator's allowlist.
10. Inbound intake is disabled without a secret; an accepted delivery is signed,
    fresh, and claimed exactly once across every replica.

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
| `intake_deliveries` | accepted webhook delivery ids, claimed atomically across replicas |

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
