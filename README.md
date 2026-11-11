# Nightjar

**Open-source incident response that proves it fixed the problem.**

Nightjar is a self-hosted incident responder for small organizations that do not have
an SRE or a security operations centre. It receives a problem — a website outage, a
leaked API key, a suspicious email — investigates it with evidence, asks before it
changes anything, and only marks the incident resolved when independent checks pass.

Most "AI SRE" tools stop at a root-cause paragraph. Nightjar treats that as the
halfway point.

```
intake → investigate → challenge → plan → policy → approve → execute → verify → prove
```

## Why

Small teams run on SaaS: Cloudflare, GitHub, Google Workspace, a storefront, a few
servers. When something breaks, one person has to become the incident commander, the
SRE, the security analyst and the postmortem author at the same time.

Existing open-source agents are Kubernetes-first (HolmesGPT, K8sGPT, Aurora) or
SOC-first (Vigil, Shuffle). Nightjar is built for the long tail in between: the
corner shop with a checkout problem, the studio with a leaked token, the practice
with a phishing report.

## The difference: verification is the product

An incident is not resolved because a model says so. The remediation plan declares,
up front, observable success criteria, and a deterministic verifier runs them:

- `http` — N consecutive responses, expected status, optional p95 latency bound
- `json` — a JSON path equals an expected value
- `dns` / `tls` — record and certificate checks
- `connector_check` — a typed, read-only connector probe (cache status, service
  state, credential state, audit health)

Those predicates run at T+0 and again after a stability window. If any required
predicate fails, the incident is reopened. The **false resolution rate** — "declared
fixed, actually not" — is the headline metric in every evaluation run.

Current deterministic baseline (`nightjar-eval --suite all`):

| Cases | Accuracy | False resolutions | Policy violations | Injection resistance | Chain integrity |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 46 | 100% | 0 | 0 | 100% | 100% |

Mean latency 0.88 s, p95 2.07 s. See [docs/evals.md](docs/evals.md) for methodology.

## Safety model

- The model never gets a shell. It emits typed actions from a fixed catalogue.
- A deterministic policy engine decides allow / approve / deny. A Rego reference
  policy and the local engine return identical decisions.
- R2 actions require an approved, unexpired human approval scoped to the current
  plan. R3 is denied unless break-glass with two approvals.
- Untrusted content (mail, web pages, deployment notes) is redacted, wrapped as data,
  and never parsed as instructions.
- Every claim cites hash-chained evidence; tampering is detectable.
- The investigator, critic, planner, policy engine, executor, and verifier are
  separate components. Model reasoning is never authority.

## Stack

Python 3.14 · FastAPI · PydanticAI V2 · PostgreSQL 18 · Temporal (optional) · OPA
(optional) · Next.js 16 · React 19 · Tailwind 4 · Apache-2.0.

No Qdrant, no Neo4j, no open-core. One database, one policy engine, one audit ledger.

## Repository layout

```
src/nightjar/
  core/          domain model, actions, policy, evidence ledger, verification
  agents/        provider-agnostic roles, prompts, runtimes
  connectors/    typed adapters (lab, HTTP, GitHub, Cloudflare, webhook)
  api/           FastAPI control plane and closed incident loop
  worker/        optional Temporal workflow and activities
  lab/           CornerShop synthetic SMB environment
  evals/         catalogue, harness, scorer, gates, nightjar-eval CLI
apps/web/        Next.js case-file UI
docs/            architecture, API, evals, ADRs, threat model, checklist
policies/        Rego reference policy and tests
deploy/          Docker Compose
```

## Quickstart (fully isolated)

Nightjar vendors its toolchain under `.tools/`. Nothing is installed system-wide.

```bash
bash scripts/bootstrap-tools.sh --with-pgvector
source .tools/bin/env.sh

uv venv
uv sync --extra dev
bash scripts/db.sh init
NIGHTJAR_DATABASE_URL=postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar \
  .venv/bin/alembic upgrade head
```

Run the lab and API in separate terminals:

```bash
nightjar-lab --port 8100
nightjar-api                 # http://127.0.0.1:8000
```

Inject a fault, then file an incident:

```bash
curl -s -X POST "http://127.0.0.1:8100/lab/scenario/site_cache_poisoned?variant=0" > /tmp/seed.json

python3 - <<'EOF'
import json, urllib.request
seed = json.load(open("/tmp/seed.json"))
body = {k: seed[k] for k in ("title","description","incident_class","severity","source","metadata")}
req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/incidents?wait=false",
    data=json.dumps(body).encode(),
    headers={"content-type": "application/json"},
    method="POST",
)
print(json.load(urllib.request.urlopen(req))["id"])
EOF
```

Poll `GET /v1/incidents/{id}`. Approval-gated actions wait in `awaiting_approval`
until you `POST /v1/incidents/{id}/approvals/{approval_id}`. See
[docs/api.md](docs/api.md).

## Evaluation

```bash
nightjar-eval --suite all
```

Starts a private CornerShop, runs the real HTTP API through 46 cases, auto-approves
gated actions, and writes JSON/Markdown reports to `evals/reports/`. Exits non-zero
when a gate fails. Suites: `cornershop-30`, `relapse-6`, `injection-10`.

## Durable mode (optional)

The inline orchestrator is the default. For durable approvals and stability windows
across restarts:

```bash
temporal server start-dev
NIGHTJAR_ORCHESTRATOR=temporal nightjar-worker
NIGHTJAR_ORCHESTRATOR=temporal nightjar-api
```

Approval decisions signal the running workflow; the same `IncidentService` methods
do the work.

## Development

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest
opa test policies/
nightjar-eval --suite all
```

Web app:

```bash
cd apps/web
npm ci
npm run dev
```

## Documentation

- [Architecture](docs/architecture.md)
- [API reference](docs/api.md)
- [Evaluation](docs/evals.md)
- [ADRs](docs/adr/)
- [Threat model](docs/security/threat-model.md)
- [Security checklist](docs/security/checklist.md)
- [Roadmap](ROADMAP.md)

## Status

Pre-alpha, building in public. The control plane, closed loop, evaluation suite, and
optional durable orchestration are working. The case-file UI is in progress. The
repository is private until 0.1; the license and governance are already final.

## Contributing

Apache-2.0 with the Developer Certificate of Origin. No CLA, no open-core. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [GOVERNANCE.md](GOVERNANCE.md).

## Security

Report vulnerabilities per [SECURITY.md](SECURITY.md). Do not open a public issue.

## License

Apache-2.0. Contributions under DCO 1.1.
