# Real-world readiness plan

- Status: execution plan — phases 6–7 and 9–10 shipped. Phase 8 is partly shipped:
  per-incident budgets (8.2), the provider-shaped injection corpus (8.3) and the
  nightly workflow with a run cap (8.4) are in place; recorded hosted-model
  baselines (8.1) and the first hosted nightly run need provider API keys
- Date: 2026-11-13 (updated 2026-11-24)
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
| Signed alert intake | **Real**, HMAC + timestamp + replay rejection | `api/routers/intake.py`, `core/signatures.py` |
| `http.get` probe | Real, SSRF-guarded | `connectors/http.py` |
| `revoke_credential` (GitHub) | **Real HTTP**, proven by a 401 probe | `connectors/github.py` |
| `quarantine_message`, `block_sender` (Google) | **Real HTTP** | `connectors/google_workspace.py` |
| `quarantine_message`, `block_sender` (Microsoft) | **Real HTTP**; the move's new message id is verified | `connectors/microsoft365.py` |
| `revoke_credential` (Microsoft sessions) | **Real HTTP**, proven by `signInActivity`, fails closed without audit access | `connectors/microsoft365.py` |
| `rollback_deployment` (Cloudflare Pages, Vercel, Render) | **Real HTTP**, read back from production | `connectors/cloudflare.py`, `connectors/vercel.py`, `connectors/render.py` |
| `restart_service` (Render) | **Real HTTP**, verified live and not suspended | `connectors/render.py` |
| `restart_service` (Fly) | **Real HTTP**, verified by state and `updated_at` | `connectors/fly.py` |
| Credential-liveness verification | Real | `CredentialCheck`, `tests/test_github_revocation.py` |
| Capability discovery | Real | `nightjarctl doctor`, `GET /v1/meta/capabilities` |
| Fail-closed production defaults | Real | `Settings._refuse_unsafe_production` |
| Multi-process safety | **Real for incidents, the scheduler, and intake replay** | `api/locks.py`, `api/replay.py`, `tests/test_locks.py`, `tests/test_replay_store.py` |
| Real-model evaluation | **Missing** | All numbers are mock |
| OIDC / RBAC / rate limits | **OIDC, approver groups, and a per-caller API budget are real**, including a database-backed bucket shared by every replica; group-to-role mapping is missing | `api/auth.py`, `api/ratelimit.py` |

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
| `PermissionDenied` | 403 | no | action fails with missing-scope hint |
| `NotFound` | 404 | no | action fails; target may have been deleted |
| `Conflict` | 409 | no | read back state, treat as possibly applied |
| `RateLimited` | 429 | yes | honor `Retry-After`, then backoff |
| `Transient` | 5xx, timeout | if safe | max 3 attempts, jitter |
| `Validation` | 400/422 | no | planner bug; record and stop |
| `PropagationPending` | — | yes | verifier-only; not an action failure |

**Shipped** (`src/nightjar/connectors/errors.py`). Every error carries `kind`,
`retryable`, `retry_after`, `status`, `provider`, and — the field that makes
retries safe — `maybe_applied`. `error_from_response()` is the single place a
status code becomes an error, so a `429` cannot mean one thing in Cloudflare and
another in Fly, and `retry_after_seconds()` understands both the seconds form and
the HTTP-date form of the header.

`maybe_applied` is the honest part. A `500` from `POST /deployments` may have
created a deployment; repeating the call creates a second one. A connection that
was refused cannot have applied anything. `transport_error()` distinguishes
`ConnectError`/`ConnectTimeout` (safe) from `ReadTimeout` and dropped responses
(not safe), and `safe_to_retry()` combines it with `retryable`. The executor asks
`safe_to_retry()`, never `retryable` alone.

`ConnectorError` remains the base class, so a connector can be migrated one call
site at a time and existing `except ConnectorError` handlers keep working. Not
shipped: an `ActionResult` wrapper. Actions already return a recorded dict, and a
wrapper with no consumer would be churn; the field will be added when idempotent
replay (6.3) needs it.

### 3.3 Idempotency

`src/nightjar/connectors/idempotency.py`:

```python
def action_key(incident_id, plan_id, action) -> str:
    canonical = json.dumps(action.params, sort_keys=True, separators=(",", ":"))
    return sha256(f"{incident_id}|{plan_id}|{action.id}|{action.action_type}|{action.target}|{canonical}")
```

**Shipped** (`src/nightjar/connectors/idempotency.py`). The key is derived from
incident, plan, action id, action type, target, and canonicalised parameters, with
the planner's optional pinned key folded in as extra material. Plan id is part of
the key on purpose: a reopened incident gets a new plan and must be allowed to
re-apply the fix, while a second execution of the *same* plan must not.

`executions.idempotency_key` carries a **partial** unique index
(`WHERE status = 'executed' AND idempotency_key <> ''`). Partial is the whole
trick: a failed attempt has to stay retryable, so only one *successful* execution
may exist per key. Rows without a key — manual steps, history — are excluded.

Before executing, the service asks `Repository.find_execution_by_key()`. If a
successful row exists, it sets the action to `executed`, appends an
`action.replayed` event carrying the original execution id and result, and never
calls the connector. The per-incident advisory lock makes the read-then-execute
safe: the same key cannot be executed twice concurrently because the second
worker cannot take the lock. The unique index is the backstop for a lock that
expired mid-flight.

Connectors also read before they write where the provider allows it. `open_issue`
lists open issues and reuses one with the exact title (a refused listing proceeds
to create rather than failing the incident, since the idempotency key already
covers the same-plan case); Gmail sender blocking replays an existing filter;
rollback paths read the current deployment before promoting anything;
`purge_cache` is naturally idempotent.

Not shipped: an `UNKNOWN` action state plus a reconciliation pass for a timeout
whose outcome cannot be known. Today such an error is recorded as `failed` with
`maybe_applied=true`, and `retry()` starts a fresh investigation with a new plan,
which is honest but leaves the operator to confirm provider state by hand.

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

**Shipped** (`src/nightjar/connectors/ratelimit.py`). A classic token bucket per
provider, created on first use and shared by every connector instance in the
process. Cloudflare, GitHub, Google, Microsoft, Vercel, Render, Fly, and Stripe
call `limiter_for(name).acquire()` before every request; the local lab, plain
HTTP probes, and the outbound webhook do not, because they are not provider APIs
with a published budget.

The bucket is process-local, and that is a real limit: N replicas each honour the
budget, so the effective rate is N times the table. The table's numbers sit well
below every provider's published limit for exactly that reason. A shared limiter
(Redis or a database row) is recorded here as open work rather than pretended.

`Retry-After` handling lives in two places on purpose: the bucket prevents the
`429`, and `retry.py` obeys the header when one arrives anyway. A `429` waits
exactly the provider's number, capped by
`NIGHTJAR_CONNECTOR_RETRY_MAX_WAIT_SECONDS` (default 30) so a provider cannot
park an incident for an hour.

**Shipped** (`src/nightjar/connectors/retry.py`). `call_with_retries()` retries
only `safe_to_retry()` failures, up to `NIGHTJAR_CONNECTOR_MAX_ATTEMPTS` (default
3), with exponential backoff plus jitter for transient errors and the exact
`Retry-After` for rate limits. The executor records the failed attempts on
`action.completed`, so a success after two 429s is visible in the timeline
instead of looking like a clean first try.

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
(there is no universal token to present), so the predicate reads
`GET /users/{upn}?$select=signInActivity` and requires the latest interactive and
non-interactive sign-in to be at or before the revocation timestamp plus
`NIGHTJAR_MICROSOFT_PROPAGATION_SECONDS` (default 180). Reading that property
needs `AuditLog.Read.All` and Entra ID P1; when Graph returns no activity the
check fails closed rather than accepting the API's `true` as proof.

Document that session revocation does not revoke already-issued refresh tokens
immediately and does not disable the account. If the credential class requires
account disable, that is a separate R3 action and is out of v1.

**Failure modes.** `403 Authorization_RequestDenied` → missing admin consent or an
application access policy that excludes the mailbox. `404` → mailbox or message
gone. `429` → `Retry-After` is surfaced on the error and the action is retryable.

**Implementation.** `Microsoft365Connector` (`connectors/microsoft365.py`):

- the app token is cached until 60 s before expiry;
- `_folder_id` reuses `Nightjar Quarantine` and creates it only during execution,
  never during verification;
- quarantine returns `new_message_id`, and `microsoft.message_folder` tries
  `message_id`, `new_message_id`, then `previous_message_id`, treating a `404` as
  "try the next id" rather than a hard failure;
- `microsoft.rule_present` matches display name and sender, and reports
  `isEnabled`;
- `microsoft.sessions_revoked` reads `signInActivity`, passes only when the latest
  interactive and non-interactive sign-in are at or before `since + grace`, and
  **fails closed with an explicit `AuditLog.Read.All` + Entra ID P1 message** when
  Graph returns no activity instead of trusting the API's `true`;
- every verification helper converts `ConnectorError` to `VerificationError`, so a
  provider failure is a failed predicate, not a crashed verifier;
- mail actions resolve the mailbox from `params.mailbox` or the single managed
  mailbox and never fall back to `action.target`, which is the message id or the
  sender.

**Config.** `NIGHTJAR_MICROSOFT_TENANT_ID`, `NIGHTJAR_MICROSOFT_CLIENT_ID`,
`NIGHTJAR_MICROSOFT_CLIENT_SECRET`, `NIGHTJAR_MICROSOFT_GRAPH_URL`,
`NIGHTJAR_MICROSOFT_LOGIN_URL`, `NIGHTJAR_MICROSOFT_QUARANTINE_FOLDER`,
`NIGHTJAR_MICROSOFT_PROPAGATION_SECONDS`, and
`NIGHTJAR_MANAGED_TARGETS.microsoft.mailboxes`. The three credentials must be set
together; production refuses Microsoft without a managed mailbox.

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

`notify` signs each delivery when `NIGHTJAR_NOTIFY_WEBHOOK_SECRET` is set, and
inbound intake verifies the same construction.

**Outbound.**

```http
POST <configured url>
X-Nightjar-Signature: t=1763000000,v1=<hex hmac_sha256(secret, "1763000000." + body)>
Content-Type: application/json

{"event": "nightjar.notify", "channel": "…", "message": "…", "incident_id": "…"}
```

A non-2xx response is a retryable `ConnectorError`, never a resolved incident.
The delivery id and the 1/5/25 s retry loop are not implemented yet; they are
tracked in the connector SDK work.

**Inbound** (alert intake): the same scheme, at `POST /v1/intake/webhook`.
`verify_signature` rejects a missing or malformed header, a timestamp more than
`NIGHTJAR_INTAKE_WEBHOOK_TOLERANCE_SECONDS` away from now (default 300 s), and a
digest that does not match the exact body; comparison is `hmac.compare_digest`.
The body is capped before it is parsed, and the endpoint returns `503` when no
secret is configured rather than accepting anonymous incidents.

Replay protection uses `X-Nightjar-Delivery`, falling back to the SHA-256 of the
body when the sender provides none, so replaying a captured request is still
rejected. **Shipped** as a shared store: `api/replay.py` claims a row in
`intake_deliveries` with `INSERT ... ON CONFLICT DO NOTHING`, so the database
decides the winner and a capture replayed at any replica gets `409`. A failed
filing releases the claim so the sender's retry is not rejected; rows are pruned
after twice the signature tolerance. The incident's dedupe key defaults to
`webhook:<delivery_id>`, so a sender retry that slips through a restarted guard
returns the same case rather than a second one.

**Config.** `NIGHTJAR_NOTIFY_WEBHOOK_URL`, `NIGHTJAR_NOTIFY_WEBHOOK_SECRET`,
`NIGHTJAR_INTAKE_WEBHOOK_SECRET`, `NIGHTJAR_INTAKE_WEBHOOK_TOLERANCE_SECONDS`,
`NIGHTJAR_INTAKE_MAX_BODY_BYTES`.

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

| Class | Required proof |
|---|---|
| `site_outage` | a user-visible `http` check and a functional `json`/connector check |
| `exposed_credential` | a required predicate carrying `credential_ref` or `credential_id` |
| `phishing_report` | two required connector checks: message containment and sender filtering |

A plan missing required proof for its class is rejected before policy. Only
predicates marked `required` count, so an optional check cannot unlock
resolution. Stability is enforced by the verifier's stability window, not by a
predicate the planner can weaken.

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

### 7.3 ✅ OIDC JWT auth + JWKS cache

`src/nightjar/api/auth.py`. Discovery and JWKS are cached for an hour and an
unknown `kid` triggers exactly one refetch, so a rotation does not lock everyone
out. The algorithm allowlist is asymmetric-only (`RS256/384/512`, `ES256/384`):
accepting `HS256` would let a caller sign its own token with the published key
set. Issuer, audience, expiry, and required `sub` are checked before any claim is
read, and `jwks_uri` must use the issuer's scheme so a discovery document cannot
downgrade key fetching to plaintext.

With OIDC the recorded actor is the verified `email` (falling back to
`preferred_username`, then `sub`) and `x-nightjar-actor` is ignored. With the
shared token there is no per-user identity, so the header remains the audit label
and the documentation says so. `NIGHTJAR_OIDC_ISSUER` without
`NIGHTJAR_OIDC_AUDIENCE` is refused at startup in every environment, because an
audience-less verifier accepts a token minted for any other application on the
same issuer.

### 7.4 ✅ Separation of duties

`NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES` (default **false**). The incident stores
`reported_by` and the approval stores `requested_by`; when the flag is on,
`decide_approval` raises `Forbidden` before taking the incident lock if the caller
is the reporter, which returns `403` and executes nothing.

The default is false on purpose. The target deployment is a small organization
where one person may be the whole operations team; a two-person rule would lock
the only operator out of their own incident, and a control that has to be disabled
to work is worse than one that is honestly optional. The refusal is covered by
`tests/test_separation_of_duties.py`, including the single-operator case and a
webhook-filed incident where the reporter is `alert:webhook` and any human may
approve.

**Group-to-role mapping** is the second half, and it is deliberately narrow.
`NIGHTJAR_OIDC_APPROVER_GROUPS` lists claim values whose holders may approve an R2
action or attest a manual step; every other authenticated caller can still read,
file, note, retry, and verify. That is the only role distinction v1 needs: the
risky decision is sign-off, and everything else is work the on-call person already
does. Values are compared exactly to the claim, because mapping a directory's
object ids to friendly names would be inventing a claim the issuer did not make.
The shared token carries no groups, so when approver groups are configured it is
refused rather than treated as a master key, and configuring groups without an
issuer fails at startup. Covered by `tests/test_approver_groups.py`.

Break-glass remains denied in v1.

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
| `NIGHTJAR_OIDC_ISSUER`, `_AUDIENCE`, `_GROUPS_CLAIM` | OIDC auth; audience is mandatory when the issuer is set |
| `NIGHTJAR_OIDC_APPROVER_GROUPS` | groups whose members may sign off; requires OIDC |
| `NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES` | reporter cannot approve their own R2 action (default false) |
| `NIGHTJAR_GITHUB_APP_ID`, `_APP_PRIVATE_KEY`, `_INSTALLATION_ID`, `_ORG` | org PAT revocation |
| `NIGHTJAR_STRIPE_SECRET_KEY` | read-only revocation probe |
| `NIGHTJAR_GOOGLE_SERVICE_ACCOUNT_JSON`, `_IMPERSONATE`, `_WORKSPACE_DOMAINS` | Workspace |
| `NIGHTJAR_MICROSOFT_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_GRAPH_URL`, `_PROPAGATION_SECONDS` | Microsoft 365 |
| `NIGHTJAR_VERCEL_TOKEN`, `_TEAM_ID`, `_PROJECT_ID` | Vercel |
| `NIGHTJAR_RENDER_API_KEY`, `_SERVICE_ID` | Render |
| `NIGHTJAR_FLY_API_TOKEN`, `_APP` | Fly |
| `NIGHTJAR_NOTIFY_WEBHOOK_URL`, `_SECRET` | outbound signing |
| `NIGHTJAR_INTAKE_WEBHOOK_SECRET`, `_TOLERANCE_SECONDS`, `_MAX_BODY_BYTES` | inbound signing |
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

### 9.1 ✅ Advisory locks instead of `asyncio.Lock`

`src/nightjar/api/locks.py`:

```python
@asynccontextmanager
async def _try_lock(engine, namespace: int, key: int, *, name: str):
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:namespace, :key)"),
            {"namespace": namespace, "key": key},
        )
        if not acquired:
            raise LockBusy(name)
        try:
            yield
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {"namespace": namespace, "key": key},
            )
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

**Shipped** in `api/locks.py` and `api/db.py`. The lock engine is a separate
`NullPool` engine, so a held lock never consumes a request-pool connection while
the pipeline calls a model or a provider, and the lock connection cannot be
returned to a pool by a commit. The two-integer form is used
(`pg_try_advisory_lock(namespace, key)`), which avoids the
`hashtextextended`/asyncpg bigint typing trap and keeps the scheduler namespace
disjoint from incident keys. `run_pipeline` raises `LockBusy`; the background
spawn swallows it because another process already owns the work, while the API
routes and `retry(wait=true)` surface `409` with `Retry-After: 2`.
`tests/test_locks.py` covers contention, release, a commit on another connection
not dropping the lock, the scheduler election, and a locked incident not being
run twice.

### 9.2 ✅ Scheduler leader election

`_scheduler_loop` acquires a constant advisory lock on a dedicated connection
before each iteration. Only the leader runs `run_due_verifications()`, and the
lock is released immediately so a crashed leader costs one tick. The namespace is
`0x6E6B` and the key is `1`, disjoint from incident keys. This makes N replicas
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

`tests/chaos/test_executor_chaos.py` injects 429, 500, timeout, and a
mid-mutation disconnect through the real service. It asserts: typed error
recorded, no `RESOLVED`, no duplicate mutation, ledger valid.

**Shipped.** The suite separates failures that may have applied from failures
that provably did not. A read timeout, a dropped connection after the request
left, and a 500 all stop the plan with the action `unknown`, run verification
instead of trusting either answer, and ask the provider exactly once. A 429 and
a refused connection are retried, with each attempt recorded on the timeline. In
both paths the hash chain is checked on the final case file.

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

### 12.1 ✅ Backup and restore

`scripts/pg-backup.sh`: `pg_dump --format=custom --compress=6 --no-owner`, written
next to a JSON manifest holding the dump's SHA-256, byte count, git revision,
Alembic head and schema revision, and row counts. The custom format has no
checksum of its own, so the manifest is what makes a restore verifiable.
`scripts/pg-restore.sh`: verify the manifest, refuse an existing database unless
`--force`, `pg_restore --exit-on-error`, `alembic upgrade head`, then
`nightjarctl verify-ledger` and a count comparison against the manifest. A third
script was not needed: `nightjarctl verify-ledger` is the verifier, usable on its
own against any database.

CI job `backup and restore` migrates an empty database, seeds incidents, dumps,
restores into a second database, asserts every chain and the manifest counts, and
asserts that a dump with one flipped byte is refused before anything is created.
A checklist line is not enough for an audit ledger; the operator-facing version is
in `docs/operations.md`, including what a hash chain does and does not prove.

### 12.2 ✅ Migrations

`tests/test_migrations.py` walks the revision chain: every revision is applied to
a fresh database and then carried to head, the migrated schema is compared with
the model metadata column by column (a model change without a migration fails
there), and a database seeded at the last revision that touched `incidents` is
upgraded to head and then has every hash chain re-verified, which proves the
upgrade does not rewrite the ledger. There are no release tags yet, so the
"previous release" case is expressed as "the previous revision on the chain",
which is what a tag would point at anyway. Downgrade is not promised: forward-only
with the restore path in `docs/operations.md`.

### 12.3 ✅ Runbooks

`docs/runbooks/`: an index that covers the shared case lifecycle, approvals,
attestation, the ledger, and what each failure symptom means, then
`site-outage.md`, `exposed-credential.md`, and `phishing.md`. Each covers what
Nightjar does without the operator, which actions need approval and what the
verifier reads back, what the product cannot do and what to do instead, an
independent manual check, how to undo the fix, and what "resolved" actually
requires. `tests/test_docs.py` resolves every relative link in the documentation so
a runbook cannot point at a file that moved.

### 12.4 ✅ Observability

`core/telemetry.py` wraps the SDK so the base install has no OTel dependency and
every call is a no-op; enabling it without the `otel` extra or without an endpoint
is a startup error rather than a silently empty dashboard.

- `nightjar.action` spans with `incident_id`, `action_type`, `provider`, `target`,
  `risk`, `outcome` (`executed`, `replayed`, `manual`, `failed`), `attempts`, `error`.
- `nightjar.verification` spans with `predicate_id`, `kind`, `required`, `phase`,
  `passed`.
- Metrics: `nightjar_action_total{provider,action,outcome}`,
  `nightjar_verification_total{kind,required,passed}`, `nightjar_lock_busy_total{scope}`,
  `nightjar_doctor_verdict{provider,verdict}`.
- SLO table and a local-collector recipe in `docs/observability.md`; attributes are
  an allowlist, so no prompt, evidence body, or credential value is exported.
- Verified by `tests/test_telemetry.py`, including a full lab incident asserting the
  spans exist with the right provider and outcome.

### 12.5 ✅ Compose smoke test

`scripts/compose-smoke.sh` files one lab incident through the running stack, waits
for `resolved`, approves anything pending the way an operator would, and requires
both hash chains to verify. It polls readiness itself so a failure names the
service that did not come up.

CI job `compose smoke` builds the API/lab/migration images from
`deploy/compose/compose.yaml`, starts `db`, `migrate`, `lab`, and `api`, runs the
script, dumps logs on failure, and always tears down with `docker compose down -v`.
This is the only job that runs the images together rather than building them.

---

## 13. Micro-task backlog

Estimates are focused engineering hours, including tests and docs.

### Phase 6 — Production execution surface (P0)

Status: ✅ shipped in this phase. 6.16 is complete: outbound notifications are
signed and inbound intake verifies the same scheme, rejects stale timestamps, and
rejects replays across replicas through a shared store. Both mail providers are
shipped: Google Workspace (6.9) and Microsoft 365 (6.10).

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 6.1 ✅ | Fail-closed `Settings` validator + production block in `.env.example` | `api/settings.py`, `.env.example`, `tests/test_settings.py` | production with lab/private/no-auth raises; dev unchanged | 4 |
| 6.2 ✅ | Error taxonomy + `maybe_applied`; `ActionResult` deferred | `connectors/errors.py`, `connectors/*.py`, `tests/test_connector_errors.py` | every connector classifies status → typed error | 8 |
| 6.3 ✅ | Derived idempotency key, partial unique index, replay path, `UNKNOWN` + reconciliation | `api/tables.py`, `api/repository.py`, `connectors/idempotency.py`, `alembic/...4b7e2c9d1a83` | same action twice → one mutation | 8 |
| 6.4 ✅ | Per-provider token bucket + `Retry-After` retry loop | `connectors/ratelimit.py`, `connectors/retry.py`, `api/service.py`, tests | 429 waits exactly `Retry-After`; no burst over budget | 5 |
| 6.5 ✅ | `SecretVault`, `CredentialRef`, redaction registry | `core/secrets.py`, `core/redaction.py`, tests | raw secret absent from DB/log/evidence | 8 |
| 6.6 ✅ | `nightjarctl doctor` + `GET /v1/meta/capabilities` | `cli/doctor.py`, `api/routers/meta.py`, `pyproject.toml`, tests | table renders; exit 0 only when all required ok | 10 |
| 6.7 ✅ | GitHub revocation connector (3 modes) | `connectors/github.py`, tests | 202 recorded; probe 401 passes; 403 fails | 10 |
| 6.8 ✅ | `CredentialCheck` + `PredicateKind.CREDENTIAL` wiring | `core/verification.py`, `api/service.py`, tests | revoked → pass, live → fail, 403 → fail | 6 |
| 6.9 ✅ | Google Workspace connector: quarantine + filter | `connectors/google_workspace.py`, tests | label present/INBOX absent; filter read back | 14 |
| 6.10 ✅ | Microsoft 365 connector: move + rule + revoke sessions | `connectors/microsoft365.py`, tests | new message id handled; rule read back | 16 |
| 6.11 ✅ | Stripe assisted revocation (`MANUAL_STEP` + probe) | `connectors/stripe.py`, `core/verification.py`, `api/service.py` | manual step visible; 401 passes; 403 fails | 8 |
| 6.12 ✅ | Cloudflare Pages rollback + read-back checks | `connectors/cloudflare.py`, tests | production deployment id verified | 8 |
| 6.13 ✅ | Vercel rollback + alias verification | `connectors/vercel.py`, tests | `READY`, alias present | 8 |
| 6.14 ✅ | Render rollback/restart + autodeploy pause | `connectors/render.py`, tests | autodeploy off, deploy live | 8 |
| 6.15 ✅ | Fly machine restart + wait | `connectors/fly.py`, tests | `started`, `updated_at` after request | 6 |
| 6.16 ✅ | Webhook signing (outbound + inbound) | `connectors/webhook.py`, `api/routers/intake.py`, `core/signatures.py`, tests | bad signature 401; replay rejected | 6 |
| 6.17 ✅ | Managed target allowlist in policy + Rego parity | `core/policy.py`, `policies/nightjar.rego`, tests | unmanaged target denied in both engines | 8 |
| 6.18 ✅ | Availability-aware planner + `ActionUnavailable` | `agents/prompts.py`, `core/actions.py`, tests | unavailable action rejected pre-policy | 6 |
| 6.19 ✅ | Required-predicate enforcement per class | `core/actions.py`, `agents/prompts.py`, tests | missing predicate → plan rejected | 4 |
| 6.20 ✅ | Manual-step API + case-file panel | `api/routers/incidents.py`, `apps/web/components/case/manual-step-panel.tsx` | manual action blocks resolution until attested | 10 |

**Phase 6 total: ~161 h.**

### Phase 7 — Multi-process and auth (P0)

Status: Phase 7 is shipped. OIDC carries identity and approver-group membership;
separation of duties and approver groups are both enforced. The Temporal
manual-step follow-up is done: the workflow parks on an attestation signal and
continues through `resume_after_manual()`, so the durable path no longer finishes
while a human step is outstanding.

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 7.1 ✅ | Postgres advisory lock helper | `api/locks.py`, tests | lock survives commit; busy → 409 | 6 |
| 7.2 ✅ | Scheduler leader election | `api/main.py`, `api/locks.py`, tests | two processes, one runs checks | 4 |
| 7.3 ✅ | OIDC JWT auth + JWKS cache | `api/auth.py`, `api/deps.py`, `tests/test_auth.py` | valid token → subject; expired/forged → 403 | 12 |
| 7.4 ✅ | Separation of duties + OIDC approver groups | `api/auth.py`, `api/deps.py`, `api/service.py`, `tests/test_separation_of_duties.py`, `tests/test_approver_groups.py` | reporter cannot approve own R2; non-members cannot sign off | 6 |
| 7.5 ✅ | Rate limiting + body caps + request IDs, per-process or database-backed | `api/middleware.py`, `api/ratelimit.py`, `alembic/...9f1e7c2a6b40`, tests | 429 after budget; 413 over cap; two replicas share one bucket with `database` | 8 |
| 7.6 ✅ | SSE via `LISTEN/NOTIFY` with poll fallback | `api/notify.py`, `api/repository.py`, `api/routers/incidents.py`, tests | p95 < 150 ms | 8 |

**Phase 7 total: ~44 h.**

### Phase 8 — Real-model evidence (P1)

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 8.1 | Recorded baselines per provider | `evals/baselines/`, `evals/cli.py` | 3 providers × 46 cases recorded | 10 |
| 8.2 ✅ | Cost/latency/call budgets + hard stop | `agents/budget.py`, `api/service.py`, `worker/activities.py`, `tests/test_budget.py` | a run stops before the next role at `max_cost_usd`, `max_investigation_seconds`, or `max_agent_calls`; `incident.budget_exceeded` records the limit that fired | 5 |
| 8.3 ✅ | Injection corpus for new providers | `lab/cornershop/injection.py`, `lab/cornershop/app.py`, `evals/scorer.py`, `tests/test_evals.py`, `tests/test_agents.py` | ten payloads across page, deploy, credential, mail and intake surfaces; suite reaches every payload; resistance stays 100% | 6 |
| 8.4 ✅ | Nightly hosted-model workflow | `.github/workflows/nightly-evals.yml`, `evals/cli.py`, `evals/runner.py`, `tests/test_evals.py`, `docs/evals.md` | nightly baseline always runs; hosted providers run when key + model variable are set; per-run `--max-cost`/`NIGHTJAR_EVAL_MAX_COST_USD` stops with exit 3, per-incident cap bounds overshoot | 4 |

**Phase 8 total: ~25 h.**

### Phase 9 — Operational readiness (P1)

| ID | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 9.1 ✅ | Backup/restore/verify scripts | `scripts/pg-backup.sh`, `scripts/pg-restore.sh`, `cli/ledger.py`, CI job, `docs/operations.md` | restore + chain valid | 8 |
| 9.2 ✅ | Migration upgrade test | `tests/test_migrations.py` | empty → head per revision; data survives to head | 4 |
| 9.3 ✅ | Runbooks (3 classes) | `docs/runbooks/` | operator can follow without source | 8 |
| 9.4 ✅ | OTel spans + metrics + SLO doc | `core/telemetry.py`, `docs/observability.md` | spans visible in a local collector | 10 |
| 9.5 ✅ | Compose smoke test | `scripts/compose-smoke.sh`, CI job | incident filed and resolved | 6 |

**Phase 9 total: ~36 h.**

### Phase 10 — 0.1 launch (P2)

| ID | Task | Acceptance | Est |
|---|---|---|---|
| 10.1 ✅ | PyPI publish workflow (`nightjar-ir`, `nightjarctl`), trusted publishing, wheel install smoke (`release.yml`, CI `packaging` job, `docs/releasing.md`) | tagged release installs | 6 |
| 10.2 ✅ | `CHANGELOG.md`, semver tags, one-version tests (`tests/test_versioning.py`) | 0.1.0 entry | 2 |
| 10.3 ✅ | Signed images + SBOM + provenance (`images.yml`, `docs/supply-chain.md`) | cosign verify in docs | 6 |
| 10.4 ✅ | CodeQL, Dependabot, `CODE_OF_CONDUCT`, issue templates (`codeql.yml`, `dependabot.yml`, `ISSUE_TEMPLATE/`) | enabled; CodeQL gates once the repository is public (SARIF upload needs GHAS on a private repo), documented in `docs/supply-chain.md` | 4 |
| 10.5 ✅ | Docs pass and the three-class walkthrough (`docs/assets/demo.webm`, `apps/web/scripts/record-demo.mjs`, `encode-demo.sh`, README) | recorded against the lab build; every case resolves on proof | 12 |
| 10.6 ✅ | Playwright: approval click, relapse, retry (`e2e/lifecycle.spec.ts`) | 3 new tests | 8 |
| 10.7 ✅ | axe gate (`e2e/accessibility.spec.ts`, WCAG 2.1 AA, fails on critical/serious), self-hosted Inter/JetBrains Mono (`app/fonts/`), on-host request assertion (`e2e/typography.spec.ts`), and screenshot artifacts at desktop/phone width (`e2e/visual.spec.ts` + `nightjar-screenshots`). Pixel diffing is deliberately not gated: the background is an animated canvas and the type is dense sub-pixel, so a committed baseline would encode the rasterizer of the machine that made it | no critical violations; screenshots reviewed per run | 6 |

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
- [ ] `credential_exposure` cannot resolve without a credential-bearing predicate
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
| Microsoft | Entra app, `Mail.ReadWrite`, `MailboxSettings.ReadWrite`, `User.RevokeSessions.All`, plus `AuditLog.Read.All` and Entra ID P1 for session proof; admin consent; application access policy | `NIGHTJAR_MICROSOFT_*` |
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
- Microsoft "block sender" is an inbox message rule on the managed mailbox, not a
  tenant-wide transport rule; tenant-wide blocking is Exchange admin scope and out
  of v1.
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
