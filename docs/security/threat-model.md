# Threat model

Nightjar is an agent that reads untrusted content and is allowed to change
production-adjacent systems. The threat model assumes the model will eventually be
wrong, the evidence will eventually contain hostile text, and an operator will
eventually be tired. The architecture is built so that none of those alone can
cause an unverified change.

## Assets

| Asset | Why it matters |
| --- | --- |
| Provider credentials (GitHub, Cloudflare, mail) | Nightjar holds them to act; theft is direct compromise |
| The evidence ledger | It is the proof of what happened; tampering destroys accountability |
| Remediation authority | The ability to revoke a key, purge a cache, or roll back a deploy |
| Incident intake content | Often contains customer PII, secrets, or attacker text |
| Operator trust | A false resolution is the product failing at its only job |

## Trust boundaries

```
 untrusted                    trusted                        most trusted
 ─────────                    ───────                        ────────────
 web pages, mail,      →      agents (LLM)             →     policy engine
 alert payloads,              typed findings/plans           executor
 deployment notes,            │                              verifier
 credential metadata          │                              evidence ledger
                              ↓
                        human approval
```

- Everything inside `<untrusted-content>` is data. It is redacted, delimited, and
  never parsed as instructions.
- Agent outputs are **untrusted until validated**: they must satisfy Pydantic
  schemas with `extra="forbid"`, reference registered action types, and pass the
  policy gate.
- The policy engine, executor, verifier, and ledger do not call a model.

## Adversaries

### 1. An attacker who can plant text in evidence

**Goal:** make Nightjar resolve the incident without fixing it, execute an action
outside the plan, or disclose data.

**Surface:** HTTP response bodies, mail bodies, credential descriptions, deployment
metadata, issue titles, incident intake descriptions.

**Controls:**

- Content is wrapped in `<untrusted-content type="...">` and the system prompt
  states plainly that instructions inside are data.
- Secrets are redacted before storage and before any model call.
- Action types come from a fixed catalogue; unknown or prohibited names are
  rejected before policy.
- Required parameters and risk class are validated against the catalogue.
- Policy is deterministic and fail-closed; a model cannot approve its own action.
- The critic role checks findings against evidence independently of the
  investigator.
- The `injection-10` suite asserts the marker reaches evidence but never reaches a
  plan or action, and that the incident still resolves correctly.

**Residual risk:** a novel injection could alter the *content* of a legitimate plan
(e.g. choose a different valid zone) without changing its type. Parameter values
are constrained by connector validation and blast-radius policy, but target
selection remains a model judgement. Approval is the mitigation for R2/R3.

### 2. A compromised or hallucinating model

**Goal:** none; failure is accidental. The impact is the same.

**Controls:** schema validation, fixed action catalogue, policy gate, independent
critic, confidence thresholds, required verification predicates, and reopening when
verification fails. A model that returns nonsense fails closed.

**Residual risk:** a plausible but wrong diagnosis that still passes generic
predicates. This is why predicates are plan-specific and tied to the fault, and why
the stability window exists.

### 3. A malicious or careless operator

**Controls:** bearer token auth, actor recorded on approvals and notes, expiring
approval windows, R3 break-glass requires two approvals, append-only hash-chained
ledger, and no arbitrary shell.

**Residual risk:** Nightjar is currently single-tenant and has one shared token.
The `x-nightjar-actor` header is self-asserted, not cryptographically bound to an
identity. Anyone holding the token can act as any actor name. Deploy behind an
authenticating proxy or add SSO before multi-operator use. This is documented as a
known limitation, not a solved problem.

### 4. A compromised Nightjar host

**Controls:** least-privilege provider tokens, no shell execution, typed actions,
outbound-only connectors, SSRF guard on probes.

**Residual risk:** host compromise yields the tokens and the database. Use scoped
tokens, rotate them, and keep the database on a private network. Nightjar does not
claim to survive a root compromise of its own host.

### 5. SSRF and internal network probing

**Controls:** every HTTP/JSON/DNS/TLS probe passes `validate_url`, which rejects
non-HTTP schemes, missing hostnames, and (unless explicitly enabled) private,
loopback, link-local, multicast, reserved, and unspecified addresses. Redirects are
not followed.

**Residual risk:** DNS rebinding between validation and request. The check resolves
at validation time; a determined attacker controlling DNS could answer differently
for the actual request. Mitigations are network egress policy or an egress proxy.
`allow_private_probes` exists for the lab and must stay off in production.

### 6. Ledger tampering

**Controls:** canonical JSON, SHA-256 content hash, previous-hash chaining, and
independent verification of both evidence and timeline on every case file read.
Any edit, deletion, or reorder breaks the chain and is reported with `broken_at`.

**Residual risk:** an attacker with database write access can rewrite the entire
chain. Anchoring the head hash to an external append-only store is future work.

## Abuse cases explicitly out of scope

- Multi-tenant isolation (single tenant per deployment in v1).
- Money movement and billing changes (not representable).
- Arbitrary command execution (not representable).
- Autonomous code changes (not representable).
- Kubernetes cluster administration (not a v1 incident class).

## Security invariants

1. No model output executes without passing schema validation, the action
   catalogue, and policy.
2. No R2/R3 action executes without an approved, unexpired approval scoped to the
   latest plan.
3. No incident resolves without required predicates passing at T+0 and through the
   stability window.
4. Policy engine errors deny.
5. Evidence and timeline are append-only and hash-chained.
6. Untrusted content is redacted and labelled before it reaches a model.
7. Probes cannot reach private address space unless explicitly enabled.

See [checklist.md](checklist.md) for the deployment checklist and
[../architecture.md](../architecture.md) for component detail.
