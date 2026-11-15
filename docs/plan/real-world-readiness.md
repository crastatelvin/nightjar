# Real-world readiness plan

- Status: execution plan
- Date: 2026-11-13
- Target: Nightjar 0.1 — a self-hosted incident responder that closes the loop against real provider accounts
- Related: [ROADMAP.md](../../ROADMAP.md), [architecture](../architecture.md), [threat model](../security/threat-model.md)

This document is the gap between "the loop is real against the lab" and "the loop
is real against your Cloudflare, GitHub, Google, Microsoft, Vercel, Render, Fly,
and Stripe accounts." It is written as a work order: every item names the file it
touches, the test that proves it, and the condition that lets us call it done.

---

## 0. What "fully working" means

Nightjar 0.1 is done when all five statements are true and demonstrated in CI or a
recorded operator session:

1. **A site outage is fixed and proven fixed against a real CDN and a real deploy
   platform.** Cache purged, deployment rolled back, HTTP and TLS predicates pass,
   a stability window passes, the ledger verifies.
2. **An exposed credential is revoked and proven dead.** The old credential is
   rejected by the provider, the rejection is recorded as evidence, and the
   incident cannot resolve without that predicate.
3. **A phishing report is contained and proven contained.** The message is out of
   the inbox, the sender is filtered for the future, and both facts are read back
   from the provider.
4. **Every action that Nightjar cannot automate is marked as such in the plan,
   before approval, and still verified after a human performs it.** No silent
   "executed" for a manual step.
5. **A fresh operator can install it, run `nightjarctl doctor`, see exactly which
   providers are live and which scopes are missing, and file an incident — without
   reading the source.**

Acceptance is not a demo video. It is `nightjarctl doctor` output plus three
recorded incidents, one per class, with chain verification green.

---

## 1. Ground truth today

| Capability | State | Evidence |
|---|---|---|
| Closed loop, typed actions, policy, approvals | Real | 46/46 eval cases |
| Hash-chained evidence and timeline | Real | `tests/test_evidence.py`, chain endpoint |
| `purge_cache`, `enable_under_attack` | Real HTTP; under-attack is read back | `connectors/cloudflare.py` |
| `open_issue` | Real HTTP | `connectors/github.py` |
| `notify` | Real HTTP, HMAC-signed when configured | `connectors/webhook.py` |
| `http.get` probe | Real, SSRF-guarded | `connectors/http.py` |
| `revoke_credential` (GitHub) | **Real HTTP**, proven by a 401 probe | `connectors/github.py` |
| `quarantine_message`, `block_sender` (Google) | **Real HTTP** | `connectors/google_workspace.py` |
| `quarantine_message`, `block_sender` (Microsoft) | **Lab only** | `connectors/lab.py` |
| `rollback_deployment` (Cloudflare Pages, Vercel, Render) | **Real HTTP**, read back from production | `connectors/cloudflare.py`, `connectors/vercel.py`, `connectors/render.py` |
| `restart_service` (Render) | **Real HTTP**, verified live and not suspended | `connectors/render.py` |
| `restart_service` (Fly) | **Real HTTP**, verified by state and `updated_at` | `connectors/fly.py` |
| Credential-liveness verification | Real | `CredentialCheck`, `tests/test_github_revocation.py` |
| Capability discovery | Real | `nightjarctl doctor`, `GET /v1/meta/capabilities` |
| Fail-closed production defaults | Real | `Settings._refuse_unsafe_production` |
| Multi-process safety | **Missing** | In-process `asyncio.Lock` only |
| Real-model evaluation | **Missing** | All numbers are mock |
| OIDC / RBAC / rate limits | **Missing** | One shared token, self-asserted actor |

Two defaults are actively dangerous in production and are fixed first:

```python
# src/nightjar/api/settings.py
lab_enabled: bool = True
allow_private_probes: bool = True
api_token: str | None = None
```

A naive deployment gets a lab connector that claims to revoke credentials, a
prober that can reach `169.254.169.254`, and no authentication.

---

## 2. Design rules for the execution surface

These rules decide every connector question below.

1. **The action is the contract, not the provider.** The model emits
   `revoke_credential`, never "call this URL". Provider choice is configuration,
   resolved by `ConnectorRegistry.connector_for()`.
2. **No connector may accept a URL, host, or path from the model.** Targets come
   from `NIGHTJAR_MANAGED_TARGETS`. A model can name a target alias, never an
   arbitrary endpoint.
3. **A 2xx is not proof.** Every action has a read-back predicate. `202 Accepted`
   from GitHub's revocation API means "request queued", not "token dead".
4. **Idempotency is local, because providers do not offer it.** Cloudflare,
   GitHub, Vercel, Render, and Fly have no idempotency header. Nightjar derives a
   key, records it, and reads before writing on retry.
5. **Secrets are references, never values.** An exposed credential enters the
   system as `CredentialRef(fingerprint, provider, hint)`. The raw value lives in
   the vault for the single probe that must present it, and is redacted from every
   log, event, and evidence record.
6. **A missing capability is a first-class outcome.** `ActionUnavailable` is
   returned at plan validation, before approval. The planner is told the
   available catalogue.
7. **Manual steps are typed.** When no API exists (Stripe secret keys), the action
   becomes `MANUAL_STEP` with instructions and a verification predicate. It is
   visible in the UI and blocks resolution until verified.
8. **Every mutation is reversible or declared irreversible.** `ActionSpec.rollback`
   is required for R2 actions. If a connector cannot describe an undo, the action
   is R3 and denied in v1.
9. **Fail closed on ambiguity.** Unknown provider response, unknown status,
   timeout during a mutation: stop, record, and require a human. Never infer
   success.
10. **The verifier is the only authority.** Connector return values are evidence,
    not truth. `IncidentService._verify()` decides.

---

## 3. Connector SDK v2

The current `Connector` protocol is three methods. Production needs preflight,
typed errors, rate limiting, idempotency, and a capability report.

### 3.1 Interface

`src/nightjar/connectors/base.py`:

```python
@dataclass(frozen=True)
class Capability:
    action_type: ActionType
    provider: str
    automated: bool
    required_scopes: tuple[str, ...]
    target_kinds: tuple[str, ...]        # "zone", "repo", "mailbox", "project"
    notes: str = ""

@dataclass
class ActionResult:
    changed: bool
    provider_request_id: str | None
    observed: dict[str, Any]              # safe to persist; no secrets
    replay: bool = False                  # idempotent replay, no new mutation

class Connector(Protocol):
    name: str
    action_types: frozenset[ActionType]
    tool_names: frozenset[str]

    async def preflight(self) -> CapabilityReport: ...
    async def execute(self, action: ActionSpec) -> ActionResult: ...
    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult: ...
    def verification_checks(self) -> dict[str, CheckFn]: ...
```

`CapabilityReport` carries `provider`, `identity`, `scopes_ok`, `missing_scopes`,
`expires_at`, and `verdict: ok | degraded | unconfigured`. It powers
`nightjarctl doctor` and `GET /v1/meta/capabilities`.

### 3.2 Error taxonomy

`src/nightjar/connectors/errors.py`:

| Error | HTTP | Retry | Effect |
|---|---|---|---|
| `AuthError` | 401 | no | action fails, incident reopens, doctor marks provider red |
| `PermissionError` | 403 | no | action fails with missing-scope hint |
| `NotFoundError` | 404 | no | action fails; target may have been deleted |
| `ConflictError` | 409 | no | read back state, treat as possibly applied |
| `RateLimited` | 429 | yes | honor `Retry-After`, then backoff |
| `TransientError` | 5xx, timeout | yes | max 3 attempts, jitter |
| `ValidationError` | 400/422 | no | planner bug; record and stop |
| `PropagationPending` | — | yes | verifier-only; not an action failure |

`ConnectorError.retryable` stays for compatibility; new code raises subclasses.

### 3.3 Idempotency

`src/nightjar/connectors/idempotency.py`:

```python
def action_key(incident_id, plan_id, action) -> str:
    canonical = json.dumps(action.params, sort_keys=True, separators=(",", ":"))
    return sha256(f"{incident_id}|{plan_id}|{action.id}|{action.action_type}|{action.target}|{canonical}")
```

- Stored in `action_executions.idempotency_key` with a unique index.
- Before executing, `Repository.find_execution_by_key()` returns an existing
  `EXECUTED` row → return `ActionResult(replay=True)` with the stored observation.
- Before mutating, connectors with a read endpoint do a state check
  (`purge_cache` is naturally idempotent; `open_issue` searches open issues by
  title prefix; `block_sender` lists rules first; `rollback` reads current
  deployment).
- On timeout, the executor marks the action `UNKNOWN`, then a reconciliation pass
  reads provider state. `UNKNOWN` never resolves an incident.

### 3.4 Rate limits

`src/nightjar/connectors/ratelimit.py`: per-provider async token bucket, process
local, conservative defaults.

| Provider | Budget | Notes |
|---|---|---|
| Cloudflare | 4 req/s | global 1200 req/5 min |
| GitHub | 1 req/s | 5000 req/h authenticated |
| Google | 10 req/s | per-user quota units; Gmail modify is expensive |
| Microsoft Graph | 8 req/s | 10k req/10 min per app per tenant |
| Vercel | 2 req/s | 429 returns `Retry-After` |
| Render | 1 req/s | 429 returns `Retry-After` |
| Fly | 1 req/s per action | burst 3 |
| Stripe | 4 req/s | 100 req/s live, but probes are rare |

`429` is never retried before `Retry-After`.

### 3.5 Secret handling

`src/nightjar/core/secrets.py`:

- `CredentialRef`: `provider`, `fingerprint = sha256(raw)[:16]`, `hint` (last 4),
  `account`, `kind`.
- `SecretVault` protocol with `EnvVault` (default) and `FileVault`
  (`NIGHTJAR_VAULT_PATH`, 0600, age-encrypted optional).
- `redact.py` gains a `SecretRegistry` so any registered raw value is replaced
  with `«redacted:<fingerprint>»` in logs, evidence, and model prompts.
- The exposed credential is passed to exactly one place: the probe that must
  present it. It is never written to the database.

### 3.6 Capability probe

New CLI: `src/nightjar/cli/doctor.py`, entry point `nightjarctl`.

```
$ nightjarctl doctor
provider     identity                  scopes                      verdict
cloudflare   token:abc… (zone: example) cache_purge, zone_settings  ok
github       app:nightjar-ir            issues:write, pats:write    ok
google       sa:nightjar@…              gmail.modify, settings      degraded (no DWD)
microsoft    app:nightjar               Mail.ReadWrite, User.Revoke degraded (no consent)
stripe       —                          —                           unconfigured
webhook      https://hooks.example/…    —                           ok (signed ping 200)
```

Exit code 0 only when every provider required by the enabled incident classes is
`ok`. `--json` for CI. This is the first thing the README tells an operator to run.

---

## 4. Capability matrix

`A` = automated, `M` = manual step with machine verification, `—` = not supported.

| Action | Provider | Mechanism | Mode | Risk | Verification predicate |
|---|---|---|---|---|---|
| `purge_cache` | Cloudflare | `POST /zones/{zone}/purge_cache` | A | R1 | `http` asset version + `cloudflare.cache_status` |
| `enable_under_attack` | Cloudflare | `PATCH /zones/{zone}/settings/security_level` | A | R2 | `cloudflare.security_level` |
| `rollback_deployment` | Cloudflare Pages | `POST /accounts/{a}/pages/projects/{p}/deployments/{d}/rollback` | A | R2 | `cloudflare.pages_production` |
| `rollback_deployment` | Vercel | `POST /v1/projects/{p}/rollback/{d}` | A | R2 | `vercel.production_deployment` |
| `rollback_deployment` | Render | `POST /v1/services/{s}/rollback` + pause autodeploy | A | R2 | `render.deploy_live` |
| `restart_service` | Fly.io | `POST /v1/apps/{app}/machines/{m}/restart` | A | R2 | `fly.machine_started` |
| `restart_service` | Render | `POST /v1/services/{s}/restart` | A | R2 | `render.deploy_live` |
| `revoke_credential` | GitHub PAT/OAuth/App | `POST /credentials/revoke` | A | R2 | `credential.rejected` |
| `revoke_credential` | Google OAuth | `POST https://oauth2.googleapis.com/revoke` | A | R2 | `credential.rejected` |
| `revoke_credential` | Microsoft session | `POST /users/{upn}/revokeSignInSessions` | A | R2 | `microsoft.sessions_revoked` |
| `revoke_credential` | Stripe secret key | Dashboard only | **M** | R2 | `credential.rejected` (401) |
| `quarantine_message` | Google Workspace | `messages.modify` remove `INBOX`, add quarantine label | A | R1 | `google.message_quarantined` |
| `quarantine_message` | Microsoft 365 | `POST /users/{upn}/messages/{id}/move` | A | R1 | `microsoft.message_folder` |
| `block_sender` | Google Workspace | `settings/filters.create` | A | R2 | `google.filter_present` |
| `block_sender` | Microsoft 365 | `mailFolders/inbox/messageRules` | A | R2 | `microsoft.rule_present` |
| `open_issue` | GitHub | `POST /repos/{r}/issues` | A | R1 | `github.issue_exists` |
| `notify` | Webhook | signed `POST` | A | R1 | `webhook.delivery_accepted` |

Providers are selected per action with `params.connector`. When two connectors
claim the same action type and neither is named, the registry raises ambiguity —
that behaviour stays.

---

## 5. Provider specifications

Every section names: auth, endpoint, request, response, scopes, idempotency,
verification, failure modes, and configuration.

### 5.1 GitHub — credential revocation and issues

**Auth.** Two modes, because GitHub splits the problem.

1. *Third-party revocation* (`POST /credentials/revoke`) is **unauthenticated**.
   Authenticated calls return `403`. Accepts `ghp_`, `github_pat_`, `gho_`,
   `ghu_`, `ghr_`; up to 1000 credentials per request; 60 requests/hour per IP.
   Response `202 Accepted` means the request was queued, **not** that the token
   existed or was revoked.
2. *Organization-owned fine-grained PATs* require a GitHub App:
   `POST /orgs/{org}/personal-access-tokens` with
   `{"action": "revoke", "pat_ids": [123, ...]}`. Only GitHub Apps can call it.
3. *Own installation token*: `DELETE /installation/token` with the installation
   token. *Own OAuth token*: `DELETE /applications/{client_id}/token` with basic
   auth `client_id:client_secret` and `{"access_token": "..."}`.

**Request (mode 1).**

```http
POST https://api.github.com/credentials/revoke
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2026-03-10
Content-Type: application/json

{"credentials": ["ghp_…"]}
```

**Verification.** `CredentialCheck` presents the token to
`GET https://api.github.com/user`:

| Response | Meaning | Predicate |
|---|---|---|
| `401` | token dead | pass |
| `200` | token still live | fail |
| `403` | token valid but restricted / rate limited | fail, retry once |

This is the only honest proof. It is also why `202` must never be treated as
success.

**Scopes.** Mode 1: none. Mode 2: GitHub App permission
`Organization personal access tokens: write`. Mode 3: `admin:org` not needed for
installation tokens.

**Failure modes.** IP rate limit (60/h) → `RateLimited`; GitHub Enterprise Server
uses `POST /api/v3/credentials/revoke` with authentication, different contract →
explicitly unsupported in v1.

**Config.** `NIGHTJAR_GITHUB_TOKEN`, `NIGHTJAR_GITHUB_APP_ID`,
`NIGHTJAR_GITHUB_APP_PRIVATE_KEY`, `NIGHTJAR_GITHUB_INSTALLATION_ID`,
`NIGHTJAR_GITHUB_ORG`.

### 5.2 Stripe — assisted revocation

Stripe has **no public API to expire, delete, or rotate a secret or restricted
key**. Key lifecycle is Dashboard-only. Nightjar must not pretend otherwise.

**Implementation.** `revoke_credential` with `params.connector="stripe"` is a
manual step, not an automated action:

1. The connector advertises `automated=False`. The service never calls
   `execute`; it records the exact Dashboard path
   (`Developers → API keys → ⋯ → Expire key`), the key name, and the fingerprint,
   sets the action to `awaiting_manual`, and pauses the incident. A manual step
   is never reported as executed.
2. The exposed key is identified by fingerprint and last four characters, never
   printed. The raw value is read from `credential_ref` only to compute those.
3. A `credential` predicate presents the key to
   `GET https://api.stripe.com/v1/balance` using HTTP Basic (the key is the
   username). Bearer auth would be rejected even for a live key, which would fake
   a revocation.

| Response | Meaning | Predicate |
|---|---|---|
| `401` | key expired/deleted | pass |
| `403` | key valid, lacks `balance` read | **fail** — still live |
| `200` | key valid | fail |

`403` is the trap: a restricted key without balance permission looks like a
permission problem, not a live key. The predicate must treat only `401` as
revoked. If the key is a restricted key, the probe uses the cheapest endpoint its
known scopes allow; if no probe can be constructed, the incident stays unresolved
and asks for a human attestation, which is recorded as `HUMAN_NOTE` evidence.

Optional secondary evidence: Activity Logs API (`Stripe-Version:
2026-09-30.preview`) filtered to `api_key_deleted`. Preview-only; never required.

**Recommendation surfaced by doctor.** Use restricted keys with least privilege,
or managed keys where the hosting platform rotates them. Nightjar can verify a
rotation but cannot perform one.

**Config.** `NIGHTJAR_STRIPE_SECRET_KEY` (for read-only probes only),
`NIGHTJAR_STRIPE_API_URL`.

### 5.3 Google Workspace — Gmail containment

**Auth.** Service account with domain-wide delegation; impersonate the mailbox
owner. Scopes:

| Scope | Used for |
|---|---|
| `https://www.googleapis.com/auth/gmail.modify` | read message, modify labels, create labels |
| `https://www.googleapis.com/auth/gmail.settings.basic` | create/read filters |

`gmail.modify` is a restricted scope: it requires app verification or an internal
app. Document that in the setup guide; an unverified app cannot use it outside the
test tenant.

**Quarantine message.**

```http
POST https://gmail.googleapis.com/gmail/v1/users/{email}/messages/{id}/modify
{"addLabelIds": ["Label_NightjarQuarantine"], "removeLabelIds": ["INBOX", "UNREAD"]}
```

Create the label once per mailbox:

```http
POST /gmail/v1/users/{email}/labels
{"name": "Nightjar Quarantine", "labelListVisibility": "labelShow",
 "messageListVisibility": "show"}
```

**Verification.** `GET /gmail/v1/users/{email}/messages/{id}?format=minimal` →
`labelIds` contains the quarantine label and not `INBOX`.

**Block sender.** Gmail API has no "block sender" call. The API-expressible
equivalent is a filter:

```http
POST /gmail/v1/users/{email}/settings/filters
{"criteria": {"from": "bad@example.com"},
 "action": {"addLabelIds": ["Label_NightjarQuarantine"], "removeLabelIds": ["INBOX"]}}
```

This affects **future** mail only and does not reject SMTP. True blocking is an
Admin console routing/compliance rule, which is not in the Gmail API. The plan
text must say "filtered", not "blocked", and the UI must show the distinction.
Optionally also trash existing matches by listing `q=from:` and calling
`messages.modify` with `addLabelIds: ["TRASH"]` — an explicit
`params.purge_existing=true` flag, default false.

**Verification.** `GET .../settings/filters` contains a filter whose
`criteria.from` equals the sender. Deletion of the filter is the rollback.

**Failure modes.** `403` with `insufficientPermissions` → DWD misconfigured or
scope not granted in Admin console. `404` → message already deleted. `429` →
per-user quota; back off.

**Config.** `NIGHTJAR_GOOGLE_SERVICE_ACCOUNT_JSON`,
`NIGHTJAR_GOOGLE_IMPERSONATE`, `NIGHTJAR_GOOGLE_WORKSPACE_DOMAINS`.

### 5.4 Microsoft 365 — quarantine, rules, sessions

**Auth.** Entra app registration with **application** permissions and admin
consent:

| Permission | Used for |
|---|---|
| `Mail.ReadWrite` | read, move messages, create folders |
| `MailboxSettings.ReadWrite` | inbox message rules |
| `User.RevokeSessions.All` | revoke sign-in sessions |

Scope the app to specific mailboxes with an application access policy
(`New-ApplicationAccessPolicy`) so a compromised Nightjar cannot read every
mailbox in the tenant.

**Quarantine message.**

1. Resolve or create `Nightjar Quarantine`:
   `POST /users/{upn}/mailFolders` `{"displayName": "Nightjar Quarantine"}`.
2. Move: `POST /users/{upn}/messages/{id}/move`
   `{"destinationId": "<folder id>"}`.

**Verification.** `GET /users/{upn}/messages/{id}?$select=parentFolderId` equals
the quarantine folder id. `404` after a move means the id changed — Graph returns
a new message id after move; the connector must store the new id from the move
response and the predicate must accept either id mapping.

This is a real trap: `message: move` creates a new copy and removes the original.
The action result must carry `new_message_id`, and the predicate resolves
`$result.new_message_id`.

**Block sender.** `POST /users/{upn}/mailFolders/inbox/messageRules`:

```json
{
  "displayName": "Nightjar: bad@example.com",
  "sequence": 1,
  "isEnabled": true,
  "conditions": {"fromAddresses": [{"emailAddress": {"address": "bad@example.com"}}]},
  "actions": {"moveToFolder": "<quarantine folder id>", "stopProcessingRules": true}
}
```

**Verification.** `GET .../messageRules` contains a rule with that display name
and sender. Deletion is the rollback.

**Revoke sessions.** `POST /users/{upn}/revokeSignInSessions` → `200 true`.
Propagation is a few minutes. Verification cannot be a token probe in general
(there is no universal token to present), so the predicate is:

- `GET /users/{upn}?$select=signInActivity` and compare
  `lastSignInDateTime` to the revocation timestamp, **or**
- a `ConnectorCheck` that confirms the Graph call returned `true` and then waits
  `NIGHTJAR_GRAPH_PROPAGATION_SECONDS` (default 180) before a stability check.

Document that session revocation does not revoke already-issued refresh tokens
immediately and does not disable the account. If the credential class requires
account disable, that is a separate R3 action and is out of v1.

**Failure modes.** `403 Authorization_RequestDenied` → missing admin consent.
`404` → mailbox or message gone. `429` → `Retry-After`.

**Config.** `NIGHTJAR_MS_TENANT_ID`, `NIGHTJAR_MS_CLIENT_ID`,
`NIGHTJAR_MS_CLIENT_SECRET`, `NIGHTJAR_MS_MAILBOXES`.

### 5.5 Cloudflare — cache, security level, Pages

Existing `purge_cache` and `enable_under_attack` are correct but under-verified
and under-scoped. Add Pages rollback and read-back checks.

**Token permissions (least privilege).**

| Operation | Permission | Scope |
|---|---|---|
| purge cache | `Cache Purge` | Zone |
| security level | `Zone Settings Write` | Zone |
| Pages rollback | `Cloudflare Pages Write` | Account |
| read zone list | `Zone Read` | Zone |

**Pages rollback.**

```http
POST /accounts/{account_id}/pages/projects/{project_name}/deployments/{deployment_id}/rollback
Authorization: Bearer …
```

Response is the deployment object. Rollback points production at a previous
**successful** deployment; the target must be validated before the call
(`GET .../deployments/{id}` → `latest_stage.status == "success"`).

**Verification.** `GET /accounts/{a}/pages/projects/{p}/deployments?env=production`
→ first item `id` equals the target id, plus an application-level version marker
(`http` predicate on `/version` expecting the target build id). The second half
matters: the API can say production moved while the origin still serves stale
content.

**Failure modes.** `404` deployment not found or deleted; `400` target is not a
successful production deployment; `403` token lacks Pages Write.

**Config.** `NIGHTJAR_CLOUDFLARE_ACCOUNT_ID`, `NIGHTJAR_CLOUDFLARE_ZONE_ID`,
`NIGHTJAR_CLOUDFLARE_PAGES_PROJECT`.

### 5.6 Vercel — instant rollback

**Auth.** Bearer token, team-scoped with `teamId` query parameter when applicable.

**Rollback.**

```http
POST https://api.vercel.com/v1/projects/{projectId}/rollback/{deploymentId}
Authorization: Bearer …
```

`POST /v10/projects/{projectId}/promote/{deploymentId}` is the general promotion
path; rollback is the incident-specific one and keeps the rollback record.

**Verification.**

1. `GET /v13/deployments/{deploymentId}` → `readyState == "READY"` and
   `target == "production"`.
2. `GET /v2/deployments/{deploymentId}/aliases` contains the production domain.
3. `http` predicate on the production URL expecting the build marker.

**Failure modes.** `404` deployment deleted (rollback impossible; must redeploy a
commit); `409` project transferring; `429` rate limit.

**Config.** `NIGHTJAR_VERCEL_TOKEN`, `NIGHTJAR_VERCEL_TEAM_ID`,
`NIGHTJAR_VERCEL_PROJECT_ID`.

### 5.7 Render — rollback and restart

**Auth.** `Authorization: Bearer <api key>`, scoped tokens supported. Minimum
scopes: `services:read`, `deploys:write` (rollback), `services:write` (restart,
autodeploy toggle).

**Rollback.**

```http
POST https://api.render.com/v1/services/{serviceId}/rollback
{"deployId": "dep-…"}
```

Critical operational detail: **API rollback does not disable autodeploys.** The
next push can reintroduce the bad commit. The action therefore performs two
mutations:

1. `PATCH /v1/services/{serviceId}` `{"autoDeploy": "no"}` (default
   `params.pause_autodeploy=true`).
2. `POST /v1/services/{serviceId}/rollback` `{"deployId": "…"}`.

Re-enabling is a separate, explicit action after the incident. The plan text and
UI must say autodeploy was paused, and the rollback plan must include re-enabling.

**Restart.** `POST /v1/services/{serviceId}/restart`. Render implements restart as
a zero-downtime deploy of the same commit; it applies to all instances.

**Verification.** `GET /v1/services/{serviceId}/deploys/{deployId}` →
`status == "live"` and `commit.id` equals the target. Plus an `http` health
predicate.

**Failure modes.** `400` build artifact expired (roll back further or redeploy);
`429`; `404` service or deploy gone.

**Config.** `NIGHTJAR_RENDER_API_KEY`, `NIGHTJAR_RENDER_SERVICE_ID`.

### 5.8 Fly.io — machine restart

**Auth.** `Authorization: Bearer <fly api token>` against
`https://api.machines.dev`.

**Restart.**

```http
POST https://api.machines.dev/v1/apps/{app_name}/machines/{machine_id}/restart
  ?timeout=30s&signal=SIGTERM
```

**Verification.** `GET /v1/apps/{app}/machines/{id}` → `state == "started"` and
`updated_at` at or after the request timestamp. A restart request returns before
the machine is back, and a machine can report `started` with the old timestamp
because the request never reached the allocator, so both facts are required. The
connector records `requested_at` in its result and the plan references it as
`$result.requested_at`, which the service resolves into the predicate. Add an
`http` health predicate for the user-visible proof.

**Failure modes.** `400` invalid signal or timeout; `404` machine destroyed;
`429` 1 req/s per action. The machine is read before the restart so a destroyed
machine fails without a request.

**Config.** `NIGHTJAR_FLY_API_TOKEN`, `NIGHTJAR_FLY_APP`.

### 5.9 Webhook — signed delivery

Current `notify` posts unsigned JSON. Production needs authentication and replay
protection.

**Outbound.**

```http
POST <configured url>
X-Nightjar-Timestamp: 1763000000
X-Nightjar-Delivery: 01a1d3…
X-Nightjar-Signature: v1=<hex hmac_sha256(secret, timestamp + "." + body)>
Content-Type: application/json

{"incident_id": "…", "title": "…", "status": "…", "summary": "…", "case_url": "…"}
```

Verification: `2xx` within 10 s, response body may echo `delivery_id`. Retries:
3, backoff 1/5/25 s, same `delivery_id`. Failure is `TransientError`, not a
resolved incident.

**Inbound** (alert intake): same scheme, reject skew > 300 s, constant-time
compare, dedupe on `delivery_id`. This closes the "anyone can file an incident"
hole.

**Config.** `NIGHTJAR_WEBHOOK_URL`, `NIGHTJAR_WEBHOOK_SECRET`,
`NIGHTJAR_WEBHOOK_INBOUND_SECRET`.

---

## 6. Verification predicate catalogue

`src/nightjar/core/verification.py` currently has HTTP, JSON, DNS, TLS, and a
connector-check wrapper. Add one checker and a polling wrapper.

### 6.1 New: `CredentialCheck`

```python
@dataclass
class CredentialCheck:
    vault: SecretVault
    allow_private: bool = False

    async def __call__(self, predicate) -> dict[str, Any]:
        ref = predicate.params["secret_ref"]          # never the raw value
        probe = predicate.params["probe"]             # method, url, headers template
        raw = self.vault.resolve(ref)
        response = await client.request(..., headers={"authorization": f"Bearer {raw}"})
        revoked = response.status_code in {401} or _provider_says_invalid(response)
        return {"status": response.status_code, "revoked": revoked,
                "fingerprint": fingerprint(raw), "passed": revoked}
```

Observed evidence stores status, provider error code, and fingerprint. Never the
header.

Provider-specific invalid signals:

| Provider | Revoked when |
|---|---|
| GitHub | `401` |
| Google | `400 {"error": "invalid_grant"}` from token endpoint, or `401` from revoke probe |
| Stripe | `401` (not `403`) |
| Microsoft | `401` after session revocation, or `signInActivity` gap |

### 6.2 New: `PollingCheck`

Wraps any check with `attempts`, `interval_seconds`, `grace_seconds`. Retries only
while the observed state is `pending`/`not_yet`, never on `failed`.

```python
PollingCheck(base=ConnectorCheck(...), attempts=12, interval=15)
```

Defaults per provider: Cloudflare 6×5 s, Vercel 12×10 s, Render 20×15 s, Fly
12×5 s, Graph 12×15 s + 180 s grace, Gmail 6×5 s.

### 6.3 New connector checks

| Name | Reads | Passes when |
|---|---|---|
| `cloudflare.cache_status` | asset URL headers | `cf-cache-status` is `MISS`/`EXPIRED` on first fetch after purge |
| `cloudflare.security_level` | `GET /zones/{z}/settings/security_level` | `value == "under_attack"` |
| `cloudflare.pages_production` | deployments list | production deployment id == target |
| `vercel.production_deployment` | deployment + aliases | `READY`, `target=production`, alias present |
| `render.deploy_live` | deploy | `status == "live"`, commit matches |
| `fly.machine_started` | machine | `state == "started"`, `updated_at` after request |
| `github.credential_revoked` | `GET /user` with token | `401` |
| `github.issue_exists` | search issues | open issue with marker label |
| `google.message_quarantined` | message | label present, `INBOX` absent |
| `google.filter_present` | filters | matching `criteria.from` |
| `microsoft.message_folder` | message | `parentFolderId == quarantine` |
| `microsoft.rule_present` | rules | display name + sender match |
| `microsoft.sessions_revoked` | sign-in activity | last sign-in ≤ revocation time |
| `webhook.delivery_accepted` | delivery record | `2xx` and delivery id echoed |

### 6.4 Predicate requirements per incident class

Enforced by `validate_plan()` and the planner prompt:

| Class | Required predicates |
|---|---|
| `site_outage` | `http` 200 on the primary URL, `tls` ≥ 14 days, stability |
| `exposed_credential` | `credential` revoked, `http` provider reachable, stability |
| `phishing_report` | message quarantined, sender filter/rule present, stability |

A plan missing a required predicate for its class is rejected before policy. This
is the mechanism that stops "investigate beautifully, then do nothing".

---

## 7. Policy and planner changes

`src/nightjar/core/actions.py`, `src/nightjar/core/policy.py`,
`policies/nightjar.rego`, `src/nightjar/agents/prompts.py`.

### 7.1 Managed target allowlist

New setting `NIGHTJAR_MANAGED_TARGETS` (JSON):

```json
{
  "cloudflare": {"zones": ["023e…"], "accounts": ["…"], "pages_projects": ["site"]},
  "github": {"repos": ["acme/site"], "orgs": ["acme"]},
  "google": {"domains": ["acme.com"], "mailboxes": ["support@acme.com"]},
  "microsoft": {"mailboxes": ["support@acme.com"]},
  "vercel": {"projects": ["prj_…"]},
  "render": {"services": ["srv-…"]},
  "fly": {"apps": ["acme-site"]}
}
```

Policy rule `target_not_managed` denies any action whose resolved target is not in
the list. This is the control that makes an LLM-driven executor safe: a
prompt-injected plan can name `zone_id` all it wants; the policy denies it.

### 7.2 Availability-aware planning

- `ConnectorRegistry.available_actions()` returns action types with a configured
  connector.
- `AgentRuntime` receives that list; the planner prompt says "you may only use
  these".
- `validate_plan()` raises `ActionUnavailable` for anything else.
- `incident_class` mapping decides whether an unavailable action is fatal
  (credential exposure with no revocation path → plan is `manual_step`, not
  failed).

### 7.3 Risk changes

| Action | Old | New | Reason |
|---|---|---|---|
| `revoke_credential` | R2 | R2 | unchanged; one approval |
| `block_sender` | R2 | R2 | affects future mail |
| `quarantine_message` | R1 | R1 | reversible, single message |
| `rollback_deployment` | R2 | R2 | customer-visible |
| `restart_service` | R2 | R2 | customer-visible |
| `purge_cache` | R1 | R1 | reversible by origin fetch |
| `enable_under_attack` | R2 | R2 | may block legitimate traffic |

New rule: R2 actions require `action.rollback` to be non-empty, except
`revoke_credential` (irreversible by nature, and the incident is the exposure).
For that exception the policy requires a `notify` action in the same plan so a
human knows a credential was killed.

### 7.4 Separation of duties

`NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES=true` (production default): the actor who
filed or approved the incident cannot approve its R2 actions. The approval record
stores `requested_by` and `approved_by`; policy denies `approved_by ==
requested_by`. Break-glass remains denied in v1.

---

## 8. Configuration, secrets, and fail-closed startup

### 8.1 Fail-closed validation

`Settings` gains a `model_validator(mode="after")`:

```python
if self.environment == "production":
    if self.lab_enabled:
        raise ValueError("lab connector must be disabled in production")
    if self.allow_private_probes:
        raise ValueError("private probes must be disabled in production")
    if not (self.api_token or self.oidc_issuer):
        raise ValueError("production requires an API token or OIDC")
    if not self.managed_targets:
        raise ValueError("production requires NIGHTJAR_MANAGED_TARGETS")
    if self.stability_seconds < 60:
        raise ValueError("stability window too short for production")
```

Tests assert each message. `NIGHTJAR_ENVIRONMENT=production` in Compose becomes a
real gate, not a label.

### 8.2 New environment variables

| Variable | Purpose |
|---|---|
| `NIGHTJAR_ENVIRONMENT` | `development` / `test` / `production` |
| `NIGHTJAR_MANAGED_TARGETS` | JSON allowlist |
| `NIGHTJAR_OIDC_ISSUER`, `_AUDIENCE`, `_GROUPS_CLAIM` | OIDC auth |
| `NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES` | approval policy |
| `NIGHTJAR_GITHUB_APP_ID`, `_APP_PRIVATE_KEY`, `_INSTALLATION_ID`, `_ORG` | org PAT revocation |
| `NIGHTJAR_STRIPE_SECRET_KEY` | read-only revocation probe |
| `NIGHTJAR_GOOGLE_SERVICE_ACCOUNT_JSON`, `_IMPERSONATE`, `_WORKSPACE_DOMAINS` | Workspace |
| `NIGHTJAR_MS_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_MAILBOXES` | Microsoft |
| `NIGHTJAR_VERCEL_TOKEN`, `_TEAM_ID`, `_PROJECT_ID` | Vercel |
| `NIGHTJAR_RENDER_API_KEY`, `_SERVICE_ID` | Render |
| `NIGHTJAR_FLY_API_TOKEN`, `_APP` | Fly |
| `NIGHTJAR_WEBHOOK_SECRET`, `_INBOUND_SECRET` | signing |
| `NIGHTJAR_VAULT_PATH` | file vault |
| `NIGHTJAR_DB_LOCKS` | advisory locks on/off |

`.env.example` gets a production block with every value and a comment saying
which are required for which incident class.

### 8.3 Vault

`EnvVault` reads `NIGHTJAR_SECRET_<FINGERPRINT>` or a provider-specific variable.
`FileVault` reads a 0600 JSON map written by `nightjarctl vault set`. Both
implement `resolve(ref) -> str` and `register(raw)` so the redactor knows the
value. No secret is ever logged at `INFO`.

---

## 9. Multi-process correctness

### 9.1 Advisory locks instead of `asyncio.Lock`

`src/nightjar/api/locks.py`:

```python
@asynccontextmanager
async def incident_lock(engine, incident_id: str):
    async with engine.connect() as conn:
        key = await conn.scalar(select(func.hashtextextended(incident_id, 0)))
        got = await conn.scalar(select(func.pg_try_advisory_lock(key)))
        if not got:
            raise LockBusy(incident_id)
        try:
            yield
        finally:
            await conn.execute(select(func.pg_advisory_unlock(key)))
```

Notes that matter:

- The lock must live on a **dedicated connection**, not an `AsyncSession`.
  `Session.commit()` returns the connection to the pool and drops a session-level
  advisory lock. This is the classic bug; the test must assert the lock survives a
  commit inside the critical section.
- `hashtextextended` returns `bigint`; asyncpg needs the value typed as
  `BigInteger` or it raises "out of int32 range". Use `type_coerce(..., BigInteger)`
  or the two-int form.
- Behaviour on contention: `LockBusy` → HTTP `409` with `Retry-After: 2`, not a
  silent drop.
- `NIGHTJAR_DB_LOCKS=false` keeps the in-process lock for tests and single-process
  dev.

### 9.2 Scheduler leader election

`_scheduler_loop` acquires a constant advisory lock
(`0x6E6A7363` = "njsc") on a dedicated connection before each iteration. Only the
leader runs `run_due_verifications()`. On shutdown, release. This makes N replicas
safe without Redis.

### 9.3 SSE without polling

Current stream polls the timeline table once per second per viewer. Replace with
`LISTEN/NOTIFY`:

- After every `append_event`, `SELECT pg_notify('nightjar_events', incident_id)`.
- The SSE endpoint holds one asyncpg listener connection per process, fans out to
  subscribers, and falls back to the 1 s poll if the listener drops.
- Acceptance: note visible in the case file in p95 < 150 ms; no more than one
  query per event, not per viewer per second.

---

## 10. API and UI changes

### 10.1 API

| Endpoint | Purpose |
|---|---|
| `GET /v1/meta/capabilities` | per-action provider, automated/manual, missing scopes |
| `POST /v1/meta/probe` | run `doctor` for one provider, return report |
| `GET /v1/incidents/{id}/manual-steps` | outstanding manual actions with instructions |
| `POST /v1/incidents/{id}/manual-steps/{action_id}/attest` | human confirms a manual step; records `HUMAN_NOTE` + triggers verification |

`CaseFileResponse` gains `capabilities: dict[ActionType, CapabilityView]` and
`manual_steps: list[ManualStep]`.

### 10.2 UI

- Plan card: each action shows `Automated · Cloudflare` or
  `Manual · Stripe Dashboard` with a copyable instruction block.
- Approval dialog: disabled with an explanation when a required capability is
  missing, instead of approving an action that cannot run.
- Case file: a "Manual step" panel that stays open until the predicate passes.
- Settings: `Doctor` button that renders the capability table.
- Command deck: a provider health strip (green/degraded/red), driven by
  `/v1/meta/capabilities`.

---

## 11. Test and eval strategy

### 11.1 Unit and contract

| Layer | Tool | What it proves |
|---|---|---|
| Connector unit | `respx` with sanitized recorded responses | request shape, headers, error mapping |
| Idempotency | pytest | same key twice → one mutation, `replay=True` |
| Rate limit | pytest + fake clock | 429 honors `Retry-After` |
| Secret redaction | pytest | raw value absent from every serialized record |
| Verification | pytest | each connector check passes/fails on fixtures |
| Policy | pytest + OPA parity | `target_not_managed` denies in both engines |

Recorded fixtures live in `tests/fixtures/<provider>/` and are scrubbed by
`scripts/scrub-fixtures.py` (tokens, ids, emails replaced with stable fakes).

### 11.2 Sandbox integration (nightly, secrets-gated)

`tests/integration/` marked `@pytest.mark.live`, skipped unless the provider's
env vars exist:

- GitHub: a dedicated test org and a throwaway PAT; revoke it, assert 401.
- Stripe: test-mode restricted key; assert probe returns 403/200, then expire in
  Dashboard is not automatable — assert the manual-step path.
- Cloudflare: a test zone; purge, read back, Pages rollback on a preview project.
- Google: test Workspace tenant; quarantine a seeded message, create/delete a
  filter.
- Microsoft: dev tenant; move a seeded message, create/delete a rule, revoke a
  test user's sessions.
- Vercel/Render/Fly: preview projects/services/apps; rollback and restart.

Nightly workflow `.github/workflows/live-connectors.yml` with a budget cap and
`continue-on-error: false` only for providers whose secrets are present.

### 11.3 Chaos

`tests/chaos/`: inject 429, 500, timeout, and a mid-mutation disconnect. Assert:
typed error recorded, no `RESOLVED`, no duplicate mutation, ledger valid.

### 11.4 Evaluation additions

- `evals/scenarios/credential_exposure/` gains real-shaped tokens
  (`ghp_` + 36 chars, `github_pat_` + 82) that are structurally valid but
  revoked in the lab. Assert the planner chooses `revoke_credential` and the
  predicate kind is `credential`.
- New metric `unavailable_action_rate` must be 0: no plan may contain an action
  with no configured connector.
- New metric `manual_step_visibility`: every manual action appears in the case
  file before approval.
- `--provider` baselines: `evals/baselines/<provider>-<model>.json`, generated by
  `nightjar-eval --provider openai --model … --record`. CI uses mock; nightly uses
  hosted models with `NIGHTJAR_EVAL_MAX_COST_USD`.

---

## 12. Operational readiness

### 12.1 Backup and restore

`scripts/backup.sh`: `pg_dump --format=custom --no-owner`, timestamped, optional
`age` encryption, retention count. `scripts/restore.sh`: restore into a scratch
database. `scripts/verify-backup.sh`: restore, `alembic upgrade head`, run
`verify_chains` over every incident, exit non-zero on any break.

CI job `backup-restore` seeds incidents, dumps, restores, and asserts chain
validity. A checklist line is not enough for an audit ledger.

### 12.2 Migrations

`alembic upgrade head` from an empty database and from the previous release tag,
both in CI. Downgrade is not promised; forward-only with a documented restore
path.

### 12.3 Runbooks

`docs/runbooks/site-outage.md`, `exposed-credential.md`, `phishing.md`: what
Nightjar does, what it will ask you to approve, what it cannot do, how to verify
manually, how to roll back the rollback.

### 12.4 Observability

- OTel spans per action and per predicate, with `incident_id`, `action_type`,
  `provider`, `outcome`.
- Metrics: `nightjar_action_total{provider,action,outcome}`,
  `nightjar_verification_total{kind,passed}`, `nightjar_lock_busy_total`,
  `nightjar_doctor_verdict{provider}`.
- SLOs: action p95 < 5 s (excluding propagation), verification p95 < 10 s,
  stream latency p95 < 150 ms.

### 12.5 Compose smoke test

CI job that runs `docker compose up -d`, waits for health, files one incident
through the API, asserts resolution, and tears down. Images currently build but
are never run together.

---

## 13. Micro-task backlog

Estimates are focused engineering hours, including tests and docs.

### Phase 6 — Production execution surface (P0)

Status: ✅ shipped in this phase. 6.16 is half shipped — outbound notifications are
signed, inbound signature verification on intake is not. 6.9 is shipped for Google
Workspace; Microsoft 365 (6.10) and the deploy connectors are open.

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 6.1 ✅ | Fail-closed `Settings` validator + production block in `.env.example` | `api/settings.py`, `.env.example`, `tests/test_settings.py` | production with lab/private/no-auth raises; dev unchanged | 4 |
| 6.2 | Connector SDK v2: `Capability`, `ActionResult`, error taxonomy | `connectors/base.py`, `connectors/errors.py`, `tests/test_connector_base.py` | existing connectors migrate; registry returns `ActionResult` | 8 |
| 6.3 | Idempotency table, key derivation, replay path | `api/tables.py`, `api/repository.py`, `connectors/idempotency.py`, migration | same action twice → one mutation; `replay=True` | 8 |
| 6.4 | Per-provider token bucket + `Retry-After` handling | `connectors/ratelimit.py`, tests | 429 waits exactly `Retry-After`; no burst over budget | 5 |
| 6.5 ✅ | `SecretVault`, `CredentialRef`, redaction registry | `core/secrets.py`, `core/redaction.py`, tests | raw secret absent from DB/log/evidence | 8 |
| 6.6 ✅ | `nightjarctl doctor` + `GET /v1/meta/capabilities` | `cli/doctor.py`, `api/routers/meta.py`, `pyproject.toml`, tests | table renders; exit 0 only when all required ok | 10 |
| 6.7 ✅ | GitHub revocation connector (3 modes) | `connectors/github.py`, tests | 202 recorded; probe 401 passes; 403 fails | 10 |
| 6.8 ✅ | `CredentialCheck` + `PredicateKind.CREDENTIAL` wiring | `core/verification.py`, `api/service.py`, tests | revoked → pass, live → fail, 403 → fail | 6 |
| 6.9 ✅ | Google Workspace connector: quarantine + filter | `connectors/google_workspace.py`, tests | label present/INBOX absent; filter read back | 14 |
| 6.10 | Microsoft 365 connector: move + rule + revoke sessions | `connectors/microsoft365.py`, tests | new message id handled; rule read back | 16 |
| 6.11 ✅ | Stripe assisted revocation (`MANUAL_STEP` + probe) | `connectors/stripe.py`, `core/verification.py`, `api/service.py` | manual step visible; 401 passes; 403 fails | 8 |
| 6.12 ✅ | Cloudflare Pages rollback + read-back checks | `connectors/cloudflare.py`, tests | production deployment id verified | 8 |
| 6.13 ✅ | Vercel rollback + alias verification | `connectors/vercel.py`, tests | `READY`, alias present | 8 |
| 6.14 ✅ | Render rollback/restart + autodeploy pause | `connectors/render.py`, tests | autodeploy off, deploy live | 8 |
| 6.15 ✅ | Fly machine restart + wait | `connectors/fly.py`, tests | `started`, `updated_at` after request | 6 |
| 6.16 ◐ | Webhook signing (outbound + inbound) | `connectors/webhook.py`, `api/routers/intake.py`, tests | bad signature 401; replay rejected | 6 |
| 6.17 ✅ | Managed target allowlist in policy + Rego parity | `core/policy.py`, `policies/nightjar.rego`, tests | unmanaged target denied in both engines | 8 |
| 6.18 ✅ | Availability-aware planner + `ActionUnavailable` | `agents/prompts.py`, `core/actions.py`, tests | unavailable action rejected pre-policy | 6 |
| 6.19 | Required-predicate enforcement per class | `core/actions.py`, `agents/prompts.py`, tests | missing predicate → plan rejected | 4 |
| 6.20 | Manual-step API + case-file panel | `api/routers/incidents.py`, `apps/web/components/case/*` | manual action blocks resolution until attested | 10 |

**Phase 6 total: ~161 h.**

### Phase 7 — Multi-process and auth (P0)

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 7.1 | Postgres advisory lock helper | `api/locks.py`, tests | lock survives commit; busy → 409 | 6 |
| 7.2 | Scheduler leader election | `api/main.py`, `api/locks.py`, tests | two processes, one runs checks | 4 |
| 7.3 | OIDC JWT auth + JWKS cache | `api/auth.py`, `api/deps.py`, tests | valid token → subject; expired → 401 | 12 |
| 7.4 | Role mapping + separation of duties | `api/auth.py`, `core/policy.py`, tests | same actor cannot approve own R2 | 6 |
| 7.5 | Rate limiting + body caps + request IDs | `api/middleware.py`, tests | 429 after budget; 413 over cap | 8 |
| 7.6 | SSE via `LISTEN/NOTIFY` with poll fallback | `api/repository.py`, `api/routers/incidents.py`, tests | p95 < 150 ms | 8 |

**Phase 7 total: ~44 h.**

### Phase 8 — Real-model evidence (P1)

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 8.1 | Recorded baselines per provider | `evals/baselines/`, `evals/cli.py` | 3 providers × 46 cases recorded | 10 |
| 8.2 | Cost/latency budgets + hard stop | `agents/runtime.py`, tests | run stops at `max_cost_usd` | 5 |
| 8.3 | Injection corpus for new providers | `evals/scenarios/injection/`, tests | resistance stays 100% | 6 |
| 8.4 | Nightly hosted-model workflow | `.github/workflows/nightly-evals.yml` | green with budget cap | 4 |

**Phase 8 total: ~25 h.**

### Phase 9 — Operational readiness (P1)

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 9.1 | Backup/restore/verify scripts | `scripts/*.sh`, CI job | restore + chain valid | 8 |
| 9.2 | Migration upgrade test | CI job | empty → head; prev tag → head | 4 |
| 9.3 | Runbooks (3 classes) | `docs/runbooks/` | operator can follow without source | 8 |
| 9.4 | OTel spans + metrics + SLO doc | `core/telemetry.py`, docs | spans visible in a local collector | 10 |
| 9.5 | Compose smoke test | CI job | incident filed and resolved | 6 |

**Phase 9 total: ~36 h.**

### Phase 10 — 0.1 launch (P2)

| ID | Task | Acceptance | Est |
|---|---|---|---|
| 10.1 | PyPI publish workflow (`nightjar-ir`, `nightjarctl`) | tagged release installs | 6 |
| 10.2 | `CHANGELOG.md`, semver tags | 0.1.0 entry | 2 |
| 10.3 | Signed images + SBOM + provenance | cosign verify in docs | 6 |
| 10.4 | CodeQL, Dependabot, `CODE_OF_CONDUCT`, issue templates | enabled | 4 |
| 10.5 | Docs site pass + demo recording | three-class walkthrough | 12 |
| 10.6 | Playwright: approval click, relapse, retry | 3 new tests | 8 |
| 10.7 | axe accessibility run + screenshot baselines | no critical violations | 6 |

**Phase 10 total: ~44 h.**

**Grand total: ~310 h.** At 25 focused hours/week, roughly 12–13 weeks. Phases 6
and 7 (205 h) are the product; everything after is credibility and distribution.

---

## 14. Sequencing

| Milestone | Contents | Exit criterion |
|---|---|---|
| M1 — Safe to point at an account | 6.1–6.6 | doctor reports every provider; production refuses unsafe defaults |
| M2 — Credential class closes | 6.7–6.11 | exposed PAT revoked and proven dead; Stripe manual path verified |
| M3 — Outage class closes | 6.12–6.15 | rollback on one real platform + restart on one real platform |
| M4 — Phishing class closes | 6.9, 6.10, 6.16 | message quarantined, sender filtered, both read back |
| M5 — Safe to run replicas | 6.17–6.20, 7.1–7.6 | two API processes, no double execution, OIDC enforced |
| M6 — Evidence for skeptics | 8.x, 9.x | hosted-model baselines, backup restore, Compose smoke |
| M7 — 0.1 | 10.x | PyPI install, signed image, three runbooks, demo |

Recommended build order inside M2: GitHub revocation first. It is the only
provider where the full path — revoke, probe, prove — can be exercised in an
afternoon with a throwaway token, and it validates the connector SDK, the
credential predicate, the idempotency table, and the manual-step UI at once.

---

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Provider API changes | connector breaks silently | contract tests nightly; capability probe marks degraded |
| Stripe cannot revoke | credential class looks fake | assisted remediation + machine verification; stated everywhere |
| Graph propagation delay | false reopen | polling with grace; stability window |
| Message id changes after move | predicate fails wrongly | action result carries new id; `$result.` resolution |
| Render autodeploy undoes rollback | incident recurs | pause autodeploy in the same action; UI states it |
| Prompt-injected target | action against wrong zone | managed allowlist in policy, not prompt |
| Advisory lock lost on commit | double execution | dedicated connection; test asserts survival |
| Secret leaks into evidence | worse than the incident | vault + registry redaction + test that greps serialized rows |
| Scope creep into SIEM/K8s | product loses focus | v1 scope unchanged; new action types require an ADR |
| Mock-only evals | no credibility | hosted baselines before 0.1 |

---

## 16. Launch gate

Nightjar 0.1 ships only when every line is checked and linked to evidence.

- [ ] `nightjarctl doctor` exits 0 for all three incident classes
- [ ] Three recorded incidents (outage, credential, phishing) with green chains
- [ ] `nightjar-eval --suite all` 46/46 on mock **and** a recorded hosted baseline
- [ ] `unavailable_action_rate == 0`
- [ ] `credential_exposure` cannot resolve without a `credential` predicate
- [ ] Production settings validator rejects unsafe configuration
- [ ] Two API replicas run without double execution
- [ ] OIDC enforced; shared token only with explicit opt-in
- [ ] Backup restore + chain verification green in CI
- [ ] Compose stack filed and resolved an incident in CI
- [ ] Nightly live-connector suite green for every provider with secrets
- [ ] Runbooks reviewed by someone who did not write them
- [ ] README "what is real" table generated from `/v1/meta/capabilities`, not prose

---

## Appendix A — provider setup checklist

| Provider | What to create | Where it goes |
|---|---|---|
| Cloudflare | API token: Cache Purge, Zone Settings Write, Pages Write, Zone Read | `NIGHTJAR_CLOUDFLARE_TOKEN` |
| GitHub | fine-grained PAT or GitHub App with org PAT write | `NIGHTJAR_GITHUB_TOKEN` / app vars |
| Google | service account, DWD with `gmail.modify` + `gmail.settings.basic` | `NIGHTJAR_GOOGLE_SERVICE_ACCOUNT_JSON` |
| Microsoft | Entra app, `Mail.ReadWrite`, `MailboxSettings.ReadWrite`, `User.RevokeSessions.All`, admin consent, application access policy | `NIGHTJAR_MS_*` |
| Stripe | restricted key with the minimum read scope for the probe | `NIGHTJAR_STRIPE_SECRET_KEY` |
| Vercel | token scoped to the project, team id | `NIGHTJAR_VERCEL_*` |
| Render | API key with `services:read`, `deploys:write`, `services:write` | `NIGHTJAR_RENDER_*` |
| Fly | deploy token for the app | `NIGHTJAR_FLY_*` |
| Webhook | shared secret, inbound endpoint | `NIGHTJAR_WEBHOOK_*` |

## Appendix B — honest limits of v1

- Stripe secret-key revocation is manual. Nightjar verifies, it does not expire.
- Google "block sender" is a filter, not an SMTP block. Admin console routing
  rules are out of scope.
- Microsoft session revocation is not account disable and is not instant.
- Cloudflare Pages rollback requires a previous successful production deployment.
- Render rollback requires a retained build artifact.
- Fly restart wipes the ephemeral filesystem by design.
- No Kubernetes, no SIEM, no money movement, no arbitrary shell, no autonomous
  code changes, no multi-tenant SaaS.
- R3 remains denied. Break-glass is a policy path, not an API.

## Appendix C — open questions for the operator

1. Which deploy platform is the primary target for 0.1: Cloudflare Pages, Vercel,
   or Render? Build that one first, keep the others behind a capability flag.
2. Which mail platform is primary: Google Workspace or Microsoft 365? The
   connector design is symmetric, but the sandbox tenant and the runbook are not.
3. Is a hosted model baseline acceptable as a CI secret, or should 0.1 ship with
   mock-only numbers and a documented "bring your own key" eval command?
4. Should `NIGHTJAR_MANAGED_TARGETS` be editable from the UI, or config-file only?
   Config-file only is safer; UI is friendlier. Recommendation: config-file in
   0.1.
