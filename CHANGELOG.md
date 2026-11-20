# Changelog

All notable changes to Nightjar are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Before 1.0, the minor version carries breaking changes and the patch version carries
fixes. A schema change is a breaking change: migrations are forward-only, so an
upgrade is a backup, migrate, deploy, and verify — never a downgrade.

## [Unreleased]

## [0.1.0] — unreleased

The first release. It covers the three v1 incident classes end to end, with a real
execution surface, a verification gate that decides the outcome, and the operational
tools to run it.

### Added

- **Closed loop**: signed intake, triage, evidence collection, adversarial critique,
  typed remediation plan, policy decision, human approval, execution, T+0
  verification, a stability window, and a hash-chained case file that records all of
  it.
- **Four agent roles** (Triage, Investigator, Critic, Remediation Planner) behind a
  provider-neutral runtime, with deterministic Policy, Executor, Verifier, and
  Reporter around them. Swapping model providers changes configuration, not code.
- **Connectors**: Cloudflare Pages, Vercel, Render, Fly, GitHub, Google Workspace,
  Microsoft 365, Stripe (assisted revocation), signed outbound webhooks, HTTP probes,
  and the synthetic CornerShop lab. Every read-back is part of the connector, not a
  separate promise.
- **Verification predicates**: `http`, `json`, `dns`, `tls`, `connector_check`, and
  credential liveness, with per-class required predicates enforced before a plan can
  be approved.
- **Manual steps**: providers with no API for a fix produce a typed step with
  instructions and a probe; an attestation records evidence and verification alone
  decides the outcome.
- **Case-file API** (FastAPI) and **command deck** (Next.js): live case view, plan and
  approval, manual-step attestation, timeline stream over SSE with a polling
  fallback, and an operations dashboard.
- **`nightjarctl doctor`**: proves which v1 incident classes this deployment can
  actually close with its configured connectors, secrets, and targets.
- **`nightjarctl verify-ledger`**: verifies the evidence and timeline hash chains,
  independently of the service.
- **Evaluation harness**: 46 deterministic scenarios across the three classes,
  a policy-parity suite against OPA/Rego, injection resistance scoring, and a
  false-resolution metric that fails the run.
- **Deployment**: Dockerfile, Compose stack with migrations and health checks,
  PostgreSQL 18, optional Temporal orchestrator, optional OPA, optional OpenTelemetry
  export, and a compose smoke test that files and resolves a real incident in CI.
- **Operations**: backup, restore, and a ledger-verifying restore drill; forward-only
  migration tests; runbooks for each incident class; an observability guide with span
  and metric schemas; a security checklist and threat model.

### Security

- Typed actions only: no shell, no model-supplied URLs, no arbitrary code changes,
  no money movement.
- Policy allow/approve/deny with R3 denied in the v1 scope, approval expiry, and
  optional separation of duties.
- Managed-target allowlist shared by the Python policy and the Rego policy, with
  identical refusal reasons.
- Fail-closed production settings: lab connector off, private probes refused,
  authentication required, targets declared, and a stability window of at least 60
  seconds.
- HMAC-signed intake with a replay store shared across replicas, OIDC with approver
  groups, per-caller rate limits with an optional database-backed budget, request
  ids, body caps, and PostgreSQL advisory locks around incident work.
- Connector error taxonomy that distinguishes "refused" from "may have applied", with
  an `unknown` outcome that reconciles against the provider instead of retrying a
  mutation.

[Unreleased]: https://github.com/crastatelvin/nightjar/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/crastatelvin/nightjar/releases/tag/v0.1.0
