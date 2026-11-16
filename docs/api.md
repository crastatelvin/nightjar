# API reference

Base URL: `http://127.0.0.1:8000`

All `/v1` endpoints require `Authorization: Bearer <NIGHTJAR_API_TOKEN>`, except
the signed alert intake (`POST /v1/intake/webhook`), which is authenticated by an
HMAC signature instead. Health endpoints are unauthenticated so orchestrators can
probe them. Errors use `{"detail": "..."}` with `400`, `401`, `404`, `409`, `413`,
`422`, or `503` as appropriate.

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
the body. Replay memory is per process; run one API replica until the Phase 7
shared lock lands.

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

Server-Sent Events stream of case-file snapshots. Reconnects are supported; the
client should seed from `after_seq` and reconcile by incident id.

Event names:

- `case` — full case file snapshot
- `error` — stream failure

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
`expired`.

### `POST /v1/incidents/{id}/approvals/{approval_id}`

Header `x-nightjar-actor: alice`.

```json
{"approved": true, "comment": "Rotate the key."}
```

- `200` — decision recorded; returns the case file.
- `409` — approval already decided or expired.

Approving the last pending approval executes the plan. Rejecting any pending
approval marks the denied action `denied`, cancels the rest, and fails the
incident. Nothing executes on rejection.

## Authentication and configuration

| Variable | Purpose |
| --- | --- |
| `NIGHTJAR_API_TOKEN` | Bearer token required by `/v1` |
| `NIGHTJAR_CORS_ORIGINS` | Comma-separated browser origins |
| `NIGHTJAR_ORCHESTRATOR` | `inline` (default) or `temporal` |
| `NIGHTJAR_STABILITY_SECONDS` | Stability window after T+0 |
| `NIGHTJAR_APPROVAL_TTL_SECONDS` | Approval expiry |
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
