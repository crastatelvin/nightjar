# Releasing

Nightjar ships one distribution, `nightjar-ir`. The import package is `nightjar`,
and the console scripts keep their names (`nightjar-api`, `nightjar-worker`,
`nightjar-lab`, `nightjar-eval`, `nightjarctl`). The short distribution name is
taken on PyPI, so installs read `pip install nightjar-ir`.

## What a version means

Versioning is [SemVer](https://semver.org/spec/v2.0.0.html), with the pre-1.0
allowance: a breaking change bumps the minor version. For this product a breaking
change includes a schema change that needs a migration, a removed setting, a renamed
event type, or a changed response shape. The changelog says which one.

There is exactly one version, written in `pyproject.toml`. `nightjar.__version__`
must match it, the tag must match it, and the changelog must have an entry for it;
`tests/test_versioning.py` fails when any of those drift.

## The release checklist

1. Move the `## [Unreleased]` items into a new `## [x.y.z] — YYYY-MM-DD` section.
2. Bump `version` in `pyproject.toml` and `__version__` in `src/nightjar/__init__.py`.
   Add the comparison link at the bottom of `CHANGELOG.md`.
3. Run the release gate locally, with a real database:

   ```bash
   source .tools/bin/env.sh
   .venv/bin/ruff check src tests
   .venv/bin/ruff format --check src tests
   .venv/bin/mypy src
   .venv/bin/python -m pytest
   .venv/bin/python -m pytest evals
   ```

   The migration tests and the compose smoke job are part of CI; both are release
   blockers even though they do not run here.
4. If the release changes the schema, take a backup of a representative database and
   restore it into a scratch database with the new code, following
   [operations.md](operations.md#restore). A forward-only migration that has never
   been applied to real data is not tested.
5. Commit, sign off, and push to `main`. CI must be green on that commit.
6. Tag the commit and push the tag:

   ```bash
   git tag -a v0.1.0 -m "nightjar 0.1.0"
   git push origin v0.1.0
   ```

## What the tag does

`.github/workflows/release.yml` runs on `v*.*.*` tags:

- builds an sdist and a wheel;
- refuses the release unless the tag matches the version in `pyproject.toml`;
- checks that the metadata renders with `twine check`;
- installs the wheel into an empty virtual environment and runs `nightjarctl
  --version` and `nightjar-lab --help`, so a broken entry point fails before
  publication;
- publishes to PyPI through trusted publishing, using the job's OIDC token rather
  than a stored API token.

The PyPI project needs a trusted publisher configured once: repository
`crastatelvin/nightjar`, workflow `release.yml`, environment `pypi`. Nothing else is
stored in the repository. The same workflow can be dispatched manually, and it
requires typing the exact version to confirm; it never publishes because someone
clicked the button by accident.

## After publishing

```bash
uv tool install nightjar-ir
nightjarctl --version
```

Then verify the installed artifact, not the checkout: `nightjarctl doctor` should
report the same capabilities and the same missing connectors you expect for that
deployment. A wheel that starts but cannot close a scenario is a failed release.

## Containers

Container images are built separately; see
[deploy/compose/README.md](../deploy/compose/README.md). The wheel and the images are
cut from the same tagged commit, and `nightjarctl --version` reports the version both
were built from.
