# API reference

Base URL: `http://127.0.0.1:8000`

All `/v1` endpoints require `Authorization: Bearer <credential>`, except the
signed alert intake (`POST /v1/intake/webhook`), which is authenticated by an HMAC
signature instead. Health endpoints are unauthenticated so orchestrators can probe
them. Errors use `{"detail": "..."}` with `400`, `401`, `403`, `404`, `409`, `413`,
`422`, `429`, or `503` as appropriate.

Two credential styles are supported and they promise different things:

- **Shared token** (`NIGHTJAR_API_TOKEN`). One secret for the deployment.
  `x-nightjar-actor` names whoever the client says is acting; it is an audit label,
  not an authorization decision, because everyone holding the token is equally
  trusted.
- **OIDC** (`NIGHTJAR_OIDC_ISSUER` + `NIGHTJAR_OIDC_AUDIENCE`). The token is a JWT
  verified against the issuer's discovery document and JWKS: algorithm allowlist,
  issuer, audience, expiry, and signature are all checked before any claim is read.
  The recorded actor is the token's `email` (falling back to `preferred_username`,
  then `sub`), and `x-nightjar-actor` is ignored so a caller with a real identity
  cannot claim to be someone else. `NIGHTJAR_OIDC_GROUPS_CLAIM` (default `groups`)
  names the claim holding group membership; group-to-role mapping is not
  implemented yet.

A missing credential is `401` with `WWW-Authenticate: Bearer`; a credential that was
presented and refused is `403`. With no credential configured the API is open, which
is development-only and refused in production.

When `NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES=true`, approving an R2 action as the
person who filed the incident returns `403` and executes nothing. The default is
`false` because a one-person operations team is the target deployment.

## Request ids, budgets, and body limits

Every response carries `x-request-id`. A caller-supplied value of 1–128 characters
from `A-Z a-z 0-9 . _ : -` is echoed unchanged; anything else, or nothing, is
replaced with a fresh 32-character id. The same id is bound to the request's log
context, so the access line, any application error, and the response the caller
saw all carry one value. Quote it in a support ticket.

Each caller has a token bucket:

| Behaviour | Value |
| --- | --- |
| Budget | `NIGHTJAR_API_RATE_LIMIT_PER_MINUTE` (default 120) |
| Burst | `NIGHTJAR_API_RATE_LIMIT_BURST` (default 60) |
| Key | peer address; the last `X-Forwarded-For` entry when that is trusted |

Over budget is `429` with `Retry-After` in whole seconds. Successful responses and
`429`s carry `x-ratelimit-limit`, `x-ratelimit-remaining`, and
`x-ratelimit-reset`, so a client can slow down before it is refused. Health
endpoints are never counted. The budget is charged before the credential is
checked: a refused token costs the same as an accepted one, so guessing tokens is
not a way around the limit.

Two consequences worth knowing:

- The bucket is per process. With N replicas a caller can spend N times the
  configured budget, so the default is sized well below what one replica serves.
- The web UI reaches the API through a server-side proxy, so every browser
  session shares the proxy's address and therefore one budget. Raise
  `NIGHTJAR_API_RATE_LIMIT_PER_MINUTE` when many people watch the same dashboard.

Behind a proxy you control, set `NIGHTJAR_API_TRUST_FORWARDED_FOR=true` to key on
the last `X-Forwarded-For` entry. Leave it off when nothing you control sets that
header: otherwise a caller picks its own address and its own budget.

A body over `NIGHTJAR_API_MAX_BODY_BYTES` (default 1 MiB) is refused with `413`
before it is parsed — by declared `Content-Length` when present, by counting
chunks otherwise. The signed intake has a smaller cap of its own,
`NIGHTJAR_INTAKE_MAX_BODY_BYTES` (default 64 KiB), because it is the only
unauthenticated write path.

## Health

### `GET /healthz`

```json
{"status": "ok"}
```

### `GET /readyz`

```json
{"status": "ready", "checks": {"database": true}}
```

## Metadata

### `GET /v1/meta`

```json
{
  "name": "nightjar",
  "version": "0.1.0",
  "provider": "mock",
  "model": "rules-v1",
  "connectors": ["lab", "http"],
  "tools": ["lab.site_state", "http.get"],
  "action_catalog": {"purge_cache": "r1", "revoke_credential": "r2"}
}
```

### `GET /v1/meta/capabilities`

Query: `probe=true|false` (default `true`). With `probe=true` each configured
provider is asked whether it can work right now; the result is cached for 30
seconds. With `probe=false` nothing touches the network and every action is
reported as `configured`, not `ok`.

```json
{
  "ok": true,
  "environment": "production",
  "probed": true,
  "providers": [
    {"provider": "github", "verdict": "ok", "identity": "token:nightjar-bot",
     "detail": "scopes: repo", "missing_scopes": [], "latency_ms": 142}
  ],
  "actions": [
    {"action_type": "revoke_credential", "provider": "github", "automated": true,
     "target_kinds": ["credential"], "notes": "…", "verdict": "ok"}
  ],
  "missing": ["quarantine_message", "block_sender"],
  "problems": ["no healthy provider for quarantine_message"]
}
```

`ok` is false when any provider fails its preflight or any action required by a v1
incident class has no healthy provider. `nightjarctl doctor` prints the same
report and exits with the same verdict.

## Alert intake

### `POST /v1/intake/webhook`

Files an incident from an alert source without a bearer token. The request is
authenticated by a shared secret over the exact bytes received:

```http
POST /v1/intake/webhook
Content-Type: application/json
X-Nightjar-Signature: t=1763000000,v1=<hex hmac-sha256 of "1763000000.<body>">
X-Nightjar-Delivery: 01a1d3c4

{"title": "Checkout is returning 502",
 "description": "The status page opened at 09:14.",
 "incident_class": "site_outage",
 "severity": "high",
 "metadata": {"source": "uptime-monitor"},
 "dedupe_key": "checkout-502"}
```

The timestamp may instead arrive in `X-Nightjar-Timestamp` with a bare
`v1=<hex>` or bare hex signature. A request passes if any `v1` candidate
matches, so a sender can rotate secrets without dropping deliveries.

```json
{"incident_id": "inc_…", "delivery_id": "01a1d3c4", "deduplicated": false}
```

- `202` — incident filed (or an existing one returned; `deduplicated` is `true`).
- `400` — malformed JSON or a non-object body.
- `401` — missing, stale (default 300 s), or invalid signature.
- `409` — this delivery id was already accepted.
- `413` — body larger than `NIGHTJAR_INTAKE_MAX_BODY_BYTES` (default 64 KiB).
- `422` — the payload does not match the intake schema.
- `503` — `NIGHTJAR_INTAKE_WEBHOOK_SECRET` is unset, so intake is disabled.

The delivery id is `X-Nightjar-Delivery` when present, otherwise the SHA-256 of
the body. Accepted ids live in the `intake_deliveries` table, so the claim is
atomic across replicas: `INSERT ... ON CONFLICT DO NOTHING` lets exactly one
process accept a delivery and answers every other one with `409`. Rows older than
twice the signature tolerance are pruned. If filing the incident fails after the
claim, the claim is released so the sender's retry is not answered with `409`
and the alert is not lost.

## Incidents

### `POST /v1/incidents`

Query: `wait=true|false` (default `false`). With `wait=true`, the request runs
the pipeline until it needs approval or reaches a terminal state.

```json
{
  "title": "Checkout is failing across the storefront",
  "description": "Customers report errors at checkout.",
  "incident_class": "site_outage",
  "severity": "critical",
  "source": "webhook",
  "metadata": {"site_url": "https://shop.example", "service": "web"},
  "dedupe_key": "monitor-alert-123"
}
```

Returns `201` with the incident. `dedupe_key` returns the existing incident
when the key is reused.

The response carries `reported_by`: the verified OIDC identity when OIDC is
configured, otherwise the `x-nightjar-actor` header (default `operator`). That
value is what separation of duties compares against the approver.

### `GET /v1/incidents`

Query: `status`, `limit` (default 50), `offset`.

```json
{
  "items": [
    {
      "id": "019f…",
      "title": "Checkout is failing",
      "incident_class": "site_outage",
      "severity": "critical",
      "status": "awaiting_approval",
      "summary": null,
      "confidence": 0.93,
      "created_at": "2026-11-10T08:00:00Z",
      "updated_at": "2026-11-10T08:00:12Z",
      "resolved_at": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/incidents/{id}`

Returns the full case file: incident, evidence, timeline, plan, actions, action
states, `manual_steps`, approvals, verifications, executions, agent runs, notes,
findings, and chain status. A manual step carries its provider, fingerprint,
instructions, and the probe target, but never the credential value.

### `GET /v1/incidents/{id}/timeline?after_seq=N`

Incremental timeline. `after_seq` returns only later events, so the UI can
append without re-reading the whole ledger.

### `GET /v1/incidents/{id}/chain`

```json
{
  "evidence_valid": true,
  "timeline_valid": true,
  "evidence_checked": 12,
  "timeline_checked": 28,
  "broken_at": null
}
```

`broken_at` names the first evidence item or timeline event whose hash does not
match.

### `GET /v1/incidents/{id}/stream`

Server-Sent Events, one `timeline` event per entry:

```
id: 12
event: timeline
data: {"id": "01a1…", "seq": 12, "event_type": "action.completed", …}
```

Seed with `?after_seq=N` after loading the case normally; each event's `id` is its
`seq`, so a reconnect resumes without gaps. A `: keep-alive` comment is sent after
15 seconds of silence so proxies do not close an idle stream.

A committed write to the timeline wakes every open stream on that incident through
PostgreSQL `LISTEN`/`NOTIFY`, so the usual latency is the round trip rather than
the poll interval. The stream also reads the table every
`NIGHTJAR_SSE_POLL_SECONDS` (default 5) as a fallback: with
`NIGHTJAR_SSE_NOTIFY_ENABLED=false`, or while a dropped listener reconnects, an
event is delayed, never lost.

### `POST /v1/incidents/{id}/notes`

Header `x-nightjar-actor: alice`, body `{"body": "Talked to the owner."}`.

### `POST /v1/incidents/{id}/retry`

Allowed only from `failed`, `reopened`, or `cancelled`. Creates a new plan;
older plans remain in history.

### `POST /v1/incidents/{id}/verify`

Query: `phase=t0|stability`. Runs required predicates immediately and applies
the same resolve/reopen rules.

## Manual steps

A provider with no API for a fix (Stripe key expiry) produces a manual step. The
incident moves to `awaiting_manual` and cannot resolve until the verification
predicate passes.

### `GET /v1/incidents/{id}/manual-steps`

Lists every manual action with its state (`awaiting_manual` or `attested`),
instructions, dashboard URL, fingerprint, and probe URL.

### `POST /v1/incidents/{id}/manual-steps/{action_id}/attest`

Header `x-nightjar-actor: alice`.

```json
{"note": "Expired the checkout key in the Dashboard."}
```

- `200` — attestation recorded as `human_note` evidence and the required
  predicates run; returns the case file. A failed predicate reopens the incident
  and the step can be attested again.
- `404` — the action is not part of this incident.
- `409` — the action is not awaiting a manual step, or it is already attested
  and the incident has not reopened.

Attestation is evidence, not authority: it never resolves an incident by itself.

## Approvals

### `GET /v1/incidents/{id}/approvals`

Lists approval requests with status `pending`, `approved`, `rejected`, or
`expired`. Each record carries `requested_by` (the incident reporter) and, once
decided, `approved_by`, so the two-person control is visible in the case file.

### `POST /v1/incidents/{id}/approvals/{approval_id}`

Header `x-nightjar-actor: alice`.

```json
{"approved": true, "comment": "Rotate the key."}
```

- `200` — decision recorded; returns the case file.
- `403` — separation of duties is on and the caller filed the incident.
- `409` — approval already decided or expired.

Approving the last pending approval executes the plan. Rejecting any pending
approval marks the denied action `denied`, cancels the rest, and fails the
incident. Nothing executes on rejection.

## Authentication and configuration

| Variable | Purpose |
| --- | --- |
| `NIGHTJAR_API_TOKEN` | Shared bearer token required by `/v1` |
| `NIGHTJAR_OIDC_ISSUER` | Verify JWTs from this issuer instead of a shared token |
| `NIGHTJAR_OIDC_AUDIENCE` | Required audience; an issuer without one is refused at startup |
| `NIGHTJAR_OIDC_GROUPS_CLAIM` | Claim holding group membership (default `groups`) |
| `NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES` | Reporter cannot approve their own R2 action (default false) |
| `NIGHTJAR_API_RATE_LIMIT_ENABLED` | Per-caller budget on/off (default true) |
| `NIGHTJAR_API_RATE_LIMIT_PER_MINUTE` | Tokens per minute per caller (default 120) |
| `NIGHTJAR_API_RATE_LIMIT_BURST` | Largest back-to-back burst per caller (default 60) |
| `NIGHTJAR_API_TRUST_FORWARDED_FOR` | Key callers by the last `X-Forwarded-For` entry (default false) |
| `NIGHTJAR_API_MAX_BODY_BYTES` | Largest accepted request body (default 1048576) |
| `NIGHTJAR_SSE_NOTIFY_ENABLED` | Wake case-file streams from `LISTEN`/`NOTIFY` (default true) |
| `NIGHTJAR_SSE_POLL_SECONDS` | Fallback read interval for an open stream (default 5) |
| `NIGHTJAR_CORS_ORIGINS` | Comma-separated browser origins |
| `NIGHTJAR_ORCHESTRATOR` | `inline` (default) or `temporal` |
| `NIGHTJAR_STABILITY_SECONDS` | Stability window after T+0 |
| `NIGHTJAR_APPROVAL_TTL_SECONDS` | Approval expiry |
| `NIGHTJAR_CONNECTOR_MAX_ATTEMPTS` | Attempts per connector call (default 3) |
| `NIGHTJAR_CONNECTOR_RETRY_MAX_WAIT_SECONDS` | Cap on a `Retry-After` wait (default 30) |
| `NIGHTJAR_ALLOW_PRIVATE_PROBES` | Allow loopback probes in labs (default true) |
| `NIGHTJAR_OPA_URL` | Use a remote OPA policy engine |

## curl walkthrough

```bash
TOKEN=devtoken
API=http://127.0.0.1:8000

curl -s -H "Authorization: Bearer $TOKEN" "$API/v1/meta"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title":"Checkout failing","incident_class":"site_outage","severity":"critical"}' \
  "$API/v1/incidents?wait=true"
```

The returned incident is either `awaiting_approval` or terminal. Read the case
file, approve the pending request, then poll until `resolved` or `reopened`.
