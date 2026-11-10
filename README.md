# Nightjar

**Open-source incident response that proves it fixed the problem.**

Nightjar is a self-hosted incident responder for small organizations that do not have an SRE
or a security operations centre. It receives a problem — a website outage, a leaked API key,
a suspicious email — investigates it with evidence, asks before it changes anything, and only
marks the incident resolved when independent checks pass.

Most "AI SRE" tools stop at a root-cause paragraph. Nightjar treats that as the halfway point.

```
intake → investigate → challenge → plan → policy → approve → execute → verify → prove
```

## Why

Small teams run on SaaS: Stripe, Cloudflare, Google Workspace, GitHub, a storefront, a few
servers. When something breaks, one person has to become the incident commander, the SRE, the
security analyst and the postmortem author at the same time.

Existing open-source agents are Kubernetes-first (HolmesGPT, K8sGPT, Aurora) or SOC-first
(Vigil, Shuffle). Nightjar is built for the long tail in between: the corner shop with a
payment problem, the studio with a leaked token, the practice with a phishing report.

## The difference: verification is the product

An incident is not resolved because a model says so. The remediation plan must declare, up
front, observable success criteria:

- `site_healthy`: 20 consecutive HTTP 200s, p95 below baseline × 1.5
- `payments_working`: a Stripe test-mode charge succeeds
- `credential_dead`: the old key returns 401 and the new key works
- `sender_blocked`: a test message is rejected

The verifier runs those checks at T+0 and T+15 minutes. If any predicate fails, the incident
is reopened. The false-resolution rate — "declared fixed, actually not" — is a first-class
metric, published in every benchmark run.

## Safety model

- The model never gets a shell. It emits typed actions from a schema.
- A deterministic policy engine (OPA/Rego) decides allow / approve / deny.
- High-risk actions require a signed, expiring human approval.
- Untrusted content (emails, logs, web pages) is labelled as data and cannot authorize an action.
- Every claim cites immutable, hash-chained evidence.
- The executor is a separate component from the investigator; it never sees model reasoning as authority.

## Stack

Python 3.13+ · FastAPI · PydanticAI · Temporal · PostgreSQL 18 + pgvector · OPA · OpenBao ·
Next.js 16 · OpenTelemetry · Apache-2.0.

No Qdrant, no Neo4j, no open-core. One database, one policy engine, one audit ledger.

## Repository layout

```
src/nightjar/
  core/          domain model, evidence ledger, verification, actions
  agents/        provider-agnostic roles, prompts, tool registry
  connectors/    typed adapters (HTTP, Cloudflare, GitHub, Stripe, mail)
  api/           FastAPI control plane
  worker/        Temporal workflows + inline orchestrator
  lab/           CornerShop synthetic SMB environment
  evals/         scenario runner and scorers
apps/web/        Next.js case-file UI
docs/            architecture, ADRs, threat model, checklists
policies/        Rego policy
deploy/          Docker Compose / Helm
```

## Local development (isolated)

Nightjar vendors its entire toolchain under `.tools/`. Nothing is installed system-wide.

```bash
./scripts/bootstrap-tools.sh --with-pgvector
source .tools/bin/env.sh
uv venv .venv --python /usr/bin/python3
uv pip install --python .venv/bin/python -e ".[dev]"
./scripts/db.sh init
.venv/bin/alembic upgrade head
.venv/bin/nightjar-api        # http://127.0.0.1:8000
```

The web app:

```bash
cd apps/web
npm install
npm run dev
```

## Status

Pre-alpha. The foundation, evidence ledger, policy gate, verification engine and the first
three incident classes are being built in public. See [ROADMAP.md](ROADMAP.md).

## License

Apache-2.0. Contributions under the Developer Certificate of Origin. No CLA.
