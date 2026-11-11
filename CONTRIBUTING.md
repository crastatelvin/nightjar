# Contributing to Nightjar

Nightjar is Apache-2.0 and uses the Developer Certificate of Origin. There is no
CLA and no open-core tier: the repository is the product.

## Ground rules

- Sign off every commit (`git commit -s`). The DCO check is enforced in CI.
- Keep the safety invariants intact. A change that lets a model execute without
  policy, or resolve without verification, will not be merged.
- Tests, types, lint, policy tests, and the evaluation suite must pass.
- One logical change per pull request. Small PRs get reviewed faster.
- English in code, comments, docs, and commit messages.

## Development setup

Everything is local; no system-wide installs are required.

```bash
# Toolchains (uv, node, temporal, opa, postgres) into .tools/
bash scripts/bootstrap-tools.sh
source .tools/bin/env.sh

# Python environment
uv venv
uv sync --extra dev --extra anthropic --extra google

# Database
bash scripts/db.sh init
NIGHTJAR_DATABASE_URL=postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar \
  .venv/bin/alembic upgrade head
```

Run the stack:

```bash
nightjar-lab --port 8100          # synthetic CornerShop
nightjar-api                      # API on :8000
nightjar-eval --suite all         # 46-case evaluation
```

## Checks

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest
opa test policies/
nightjar-eval --suite all
```

The frontend lives in `apps/web`:

```bash
cd apps/web
npm ci
npm run lint
npm run build
```

## Commits

Use the imperative mood and explain *why* in the body.

```
Add stability verification for cache faults

A passing T+0 check is not proof: cache faults can return when the poisoned
object is re-fetched. Re-run required predicates after the window and reopen
the incident on failure.

Signed-off-by: Your Name <you@example.com>
```

## Pull requests

Include:

1. What changed and why.
2. Which invariant or user problem it addresses.
3. The commands you ran and their result.
4. Evaluation impact if you touched agents, policy, actions, connectors, or
   verification.

UI changes should include a screenshot or short recording and note the
reduced-motion behaviour.

## Adding an action

New capabilities are security-sensitive.

1. Add the enum value and risk class in `core/actions.py`, plus required params.
2. Add the connector implementation with parameter validation.
3. Update `policies/nightjar.rego` and the local engine so parity holds.
4. Add policy tests for allow, approve, and deny paths.
5. Add an evaluation case that exercises it end to end.
6. Document it in `docs/architecture.md`.

## Adding a connector

Connectors must be outbound-only, must validate every parameter, and must not
accept arbitrary URLs or shell strings. Register it in
`connectors/registry.py`; the registry rejects ambiguous or duplicate tools.

## Reporting bugs and vulnerabilities

Bugs: open an issue with reproduction steps and the evaluation case if one exists.
Security issues: do not open a public issue. Follow [SECURITY.md](SECURITY.md).

## Code of conduct

Be direct, be kind, assume competence. Harassment and personal attacks are not
welcome. Maintainers may remove comments or contributions that make the project
hostile to contributors.
