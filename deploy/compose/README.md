# Docker Compose

Runs the API, the synthetic lab, PostgreSQL, and an optional durable Temporal
stack. Images build from `deploy/docker/Dockerfile`.

## Demo (inline orchestrator)

```bash
cd deploy/compose
cp ../../.env.example ../../.env    # edit if you want a real provider
docker compose up --build
```

- API: http://127.0.0.1:8000
- Lab: http://127.0.0.1:8100
- PostgreSQL: 127.0.0.1:55432

The `migrate` service applies Alembic migrations once and exits. The API waits for
it to complete.

`NIGHTJAR_ALLOW_PRIVATE_PROBES=true` is set for the lab. **Set it to false and set
`NIGHTJAR_API_TOKEN` before pointing Nightjar at anything real.** See
[the security checklist](../../docs/security/checklist.md).

## Durable mode

```bash
docker compose --profile durable up --build
```

This adds Temporal, its own PostgreSQL, and a worker. Set
`NIGHTJAR_ORCHESTRATOR=temporal` so the API starts workflows instead of running the
pipeline in-process.

## Files

- `compose.yaml` — the stack
- `../docker/Dockerfile` — multi-stage image; API, worker, lab, and migrations all
  use it
- `../../.env.example` — every supported variable

## Notes

- Provider credentials are passed through from `.env`. Never bake them into the
  image.
- Data lives in the `pgdata` and `temporaldata` volumes. `docker compose down -v`
  destroys the ledger.
- The UI is not in this stack yet; it will be added as a `web` service once the
  Next.js app lands.
