# Security checklist

Use this before exposing a Nightjar deployment to a real organization. Items marked
**Required** must hold for the security invariants in the
[threat model](threat-model.md) to be true.

## Identity and access

- [ ] **Required** Set `NIGHTJAR_API_TOKEN` to a long random value. An empty token
      disables authentication.
- [ ] Put Nightjar behind an authenticating reverse proxy (SSO, mTLS, or a VPN).
      The API token is a single shared secret, not per-user identity.
- [ ] Restrict who can reach the API and the UI. Do not bind to `0.0.0.0` on a
      public interface.
- [ ] Treat `x-nightjar-actor` as advisory. Names are self-asserted; the proxy
      should inject and strip it if actor identity matters.
- [ ] Turn on provider-side audit logging for every token Nightjar holds.

## Alert intake

- [ ] Leave `NIGHTJAR_INTAKE_WEBHOOK_SECRET` empty unless an alert source needs
      to file incidents; the endpoint returns `503` when it is unset.
- [ ] Use at least 32 random characters (production refuses a shorter secret) and
      share it only with the sender.
- [ ] Give each sender its own delivery id. Nightjar rejects a repeated id, and
      falls back to the body hash when the sender omits one.
- [ ] Keep `NIGHTJAR_INTAKE_WEBHOOK_TOLERANCE_SECONDS` small enough that a captured
      request expires quickly; 300 is the default.
- [ ] Replicas are safe for incident work and the stability scheduler (advisory
      locks), but the intake replay guard is per process. If you run several API
      replicas, terminate intake at one replica or add a shared replay store.

## Provider credentials

- [ ] **Required** Use scoped tokens, never personal admin tokens.
- [ ] GitHub: fine-grained token limited to the repositories Nightjar may open
      issues in; no workflow or admin scope.
- [ ] Cloudflare: API token limited to the zones Nightjar manages, with cache purge
      and under-attack permissions only.
- [ ] Mail: a dedicated account or app password with the minimum mailbox scope.
- [ ] Google Workspace: a service account with domain-wide delegation limited to
      `gmail.modify` and `gmail.settings.basic`, and only the managed mailboxes.
- [ ] Microsoft 365: an Entra app with `Mail.ReadWrite`,
      `MailboxSettings.ReadWrite`, and `User.RevokeSessions.All`, scoped to the
      managed mailboxes by an application access policy. Session proof also needs
      `AuditLog.Read.All` and Entra ID P1; without them the check fails closed.
      Never grant `User.ReadWrite.All` or directory roles.
- [ ] Store secrets in the environment or a secret manager. `.env` is gitignored;
      never bake secrets into images.
- [ ] Rotate on a schedule and immediately after any suspected exposure.

## Network and probes

- [ ] **Required** Set `NIGHTJAR_ALLOW_PRIVATE_PROBES=false` outside the lab.
- [ ] Apply an egress policy so the API and worker cannot reach internal networks,
      metadata endpoints (`169.254.169.254`), or the control plane.
- [ ] Do not follow redirects to internal hosts; Nightjar already refuses, but
      network policy is the backstop.
- [ ] Terminate TLS at the proxy. Do not expose PostgreSQL or Temporal publicly.

## Database and ledger

- [ ] **Required** Use a dedicated database role with no superuser and no access to
      other databases.
- [ ] Enable encryption at rest and in transit.
- [ ] Back up the database, and test restore. The ledger is the audit trail.
- [ ] Restrict write access to the Nightjar role; operators read through the API.
- [ ] Verify `/v1/incidents/{id}/chain` returns `evidence_valid` and
      `timeline_valid` for recent incidents as part of monitoring.
- [ ] Consider exporting head hashes to an append-only store. Nightjar does not do
      this yet; a database-level attacker can rewrite the whole chain.

## Policy

- [ ] **Required** Keep policy fail-closed. Do not add a bypass path for "urgent"
      incidents; use R3 break-glass with two approvals instead.
- [ ] If using OPA, pin the bundle and verify policy parity tests pass
      (`opa test policies/`).
- [ ] Review the action catalogue after every upgrade. Any new action type is a new
      capability.
- [ ] Keep approval TTLs short enough that a stale approval cannot be replayed
      hours later. Default is 30 minutes.

## Operations

- [ ] Run the API and worker as non-root, read-only filesystem where possible.
- [ ] Set resource limits; a runaway investigation must not starve the host.
- [ ] Ship logs and metrics somewhere off-host.
- [ ] Keep Temporal durable if you rely on approvals surviving restarts.
- [ ] Monitor failed verifications and reopened incidents; they are leading
      indicators, not noise.
- [ ] Pin image digests and upgrade deliberately. Read the changelog for policy and
      catalogue changes.

## Evaluation and change control

- [ ] **Required** Run `nightjar-eval --suite all` in CI and require all gates to
      pass before merging policy, action, verifier, or agent changes.
- [ ] When switching model providers, re-run the full suite and record the result.
      Provider swap must not change policy, executor, verifier, or ledger
      behaviour.
- [ ] Never relax the false-resolution, policy-violation, or injection-resistance
      gates to make a model pass.

## Incident response for Nightjar itself

- [ ] Know how to stop the worker and revoke every provider token.
- [ ] Know how to mark an incident failed manually and why that is preferable to a
      false resolution.
- [ ] Preserve the database before cleanup; the ledger is evidence.
- [ ] Report vulnerabilities per [SECURITY.md](../../SECURITY.md).
