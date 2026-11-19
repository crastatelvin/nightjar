# Docker Compose

Runs the web UI, the API, the synthetic lab, PostgreSQL, and an optional durable
Temporal stack. Images build from `deploy/docker/Dockerfile` and
`deploy/docker/web.Dockerfile`.

## Demo (inline orchestrator)

```bash
cd deploy/compose
cp ../../.env.example ../../.env    # edit if you want a real provider
docker compose up --build
```

- UI: http://127.0.0.1:3000
- API: http://127.0.0.1:8000
- Lab: http://127.0.0.1:8100
- PostgreSQL: 127.0.0.1:55432

The `migrate` service applies Alembic migrations once and exits. The API waits for
it to complete. The UI waits for the API and the lab.

`NIGHTJAR_ALLOW_PRIVATE_PROBES=true` is set for the lab. **Set it to false and set
`NIGHTJAR_API_TOKEN` before pointing Nightjar at anything real.** See
[the security checklist](../../docs/security/checklist.md).

The browser calls the API directly, so the API origin list must include the UI
origin. The default `NIGHTJAR_CORS_ORIGINS` is `http://127.0.0.1:3000`; add more
origins as a comma-separated list or a JSON array.

The compose demo uses a 30-second stability window so a run resolves while you
watch. Change `NIGHTJAR_STABILITY_SECONDS` to match how long a fix must hold in
your environment.

## Durable mode

```bash
docker compose --profile durable up --build
```

This adds Temporal, its own PostgreSQL, and a worker. Set
`NIGHTJAR_ORCHESTRATOR=temporal` so the API starts workflows instead of running the
pipeline in-process.

## Smoke test

`scripts/compose-smoke.sh` (run from the repository root, after the stack is up)
files a lab incident through the API and requires it to resolve with both hash
chains intact. CI runs exactly this, in `.github/workflows/ci.yml`:

```bash
docker compose up -d db migrate lab api
./scripts/compose-smoke.sh
docker compose down -v
```

## Files

- `compose.yaml` — the stack
- `../docker/Dockerfile` — multi-stage Python image; API, worker, lab, and migrations
  all use it
- `../docker/web.Dockerfile` — Next.js standalone image for the UI
- `../../.env.example` — every supported variable

## Notes

- Provider credentials are passed through from `.env`. Never bake them into the
  image.
- Data lives in the `pgdata` and `temporaldata` volumes. `docker compose down -v`
  destroys the ledger.
- The UI is built with `output: standalone`; the image runs `node server.js`, not
  `next start`.
