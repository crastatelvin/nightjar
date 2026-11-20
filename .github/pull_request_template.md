## What changed

<!-- One paragraph. What behaviour is different after this merge? -->

## Why

<!-- The failure this prevents, or the capability it adds. -->

## Verification

<!-- Commands you ran and the result. "Tests pass" is not enough for a change that
     touches execution, verification, or a connector; say what the test asserts. -->

- [ ] `uv run pytest`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy src`
- [ ] `uv run pytest evals` (or explain why the model path is untouched)

## Safety

- [ ] A model still cannot execute without policy and approval.
- [ ] Nothing resolves without a passing required predicate.
- [ ] No shell, no model-supplied URL, no secret in the database, logs, or evidence.
- [ ] Manual steps remain typed and verified, and an attestation is still only evidence.

## Docs and migration

- [ ] Docs updated where behaviour or configuration changed.
- [ ] If a table or column changed: a forward-only migration is included, and
      `tests/test_migrations.py` passes against a fresh database.
- [ ] Commits are signed off (`git commit -s`), one logical change.
