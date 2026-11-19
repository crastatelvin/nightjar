# Backup, restore, and ledger verification

The database is the product's memory and its audit trail. Case files, approvals,
executions, and verifications are rows; every evidence item and timeline entry is
also sealed into a hash chain, so an edit made behind Nightjar's back is visible
afterward. A backup is only worth taking if a restore of it can be verified, and
that is what these scripts do.

Two commands:

| Command | Purpose |
| --- | --- |
| `scripts/pg-backup.sh` | `pg_dump` in custom format plus a JSON manifest |
| `scripts/pg-restore.sh` | restore into a new database, migrate, verify |
| `nightjarctl verify-ledger` | recompute every chain in a database |

All three need the PostgreSQL client tools to match the server's major version.
An older `pg_dump` refuses a newer server, which is the failure you want rather
than a dump that silently omits syntax it does not understand. With the project
toolchain that is already true (`scripts/db.sh` and `.tools/pg` are both 18.x).

## Backup

```bash
./scripts/pg-backup.sh --database-url "$NIGHTJAR_DATABASE_URL" --out ./backups
```

```
  dumping postgresql://nightjar@127.0.0.1:55432/nightjar (47 incidents) to ./backups/nightjar-20261119T043811Z.dump
  manifest: ./backups/nightjar-20261119T043811Z.json
./backups/nightjar-20261119T043811Z.dump
./backups/nightjar-20261119T043811Z.json
```

The dump is written with `--format=custom --compress=6 --no-owner
--no-privileges`, so it restores into any database owned by the restoring role
without dragging the source cluster's role names along. The script refuses a
database that has no Nightjar schema, and it never overwrites an existing dump.

The manifest is half the backup. PostgreSQL's custom format carries no checksum of
its own, so this file is what lets a restore prove it is holding the bytes that
were written:

```json
{
  "format": 1,
  "created_at": "2026-11-19T04:38:11Z",
  "database": "postgresql://nightjar@127.0.0.1:55432/nightjar",
  "dump": "nightjar-20261119T043811Z.dump",
  "sha256": "44144c98b182d9e160c8ac1860a9c0d3bd537fe2042eef0bf1125c18ff219fed",
  "bytes": 183883,
  "git_sha": "de0360417361785afc52c6fb05520ae722a36a72",
  "git_dirty": false,
  "alembic_head": "4b7e2c9d1a83",
  "schema_revision": "4b7e2c9d1a83",
  "pg_dump_version": "pg_dump (PostgreSQL) 18.6",
  "server_version": "18.6",
  "counts": {"incidents": 47, "timeline_events": 664, "evidence_items": 220}
}
```

`git_sha` and `alembic_head` are there for the question "which build wrote this
database" — answerable during an incident, months later, without access to the
cluster that produced it. `counts` is what the restore checks the restored tables
against.

**Treat a dump as sensitive.** It contains incident descriptions, operator notes,
provider identifiers, and redacted-but-present evidence content. It does not
contain credentials — Nightjar stores vault references, not secrets — but the
case files alone are not something to leave in a world-readable directory.

## Restore

```bash
./scripts/pg-restore.sh ./backups/nightjar-20261119T043811Z.dump \
  --database-url "postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar_restored"
```

The step order is the point:

1. **Checksum first.** The dump is hashed and compared with the manifest (and its
   size checked) before the target database is touched. A tampered or truncated
   file is refused with the two digests printed; nothing is created.
2. **A whole database, never a merge.** The target must not exist. `--force`
   drops and recreates it; without it the script stops and says so. Restoring
   into a live deployment is the expensive mistake this script exists to prevent.
3. **`pg_restore --exit-on-error`.** A restore that skipped a table and reported
   success would be worse than a failure.
4. **`alembic upgrade head`.** A dump from an older release is migrated forward,
   so restoring a backup is also the upgrade path.
5. **Verify.** `nightjarctl verify-ledger` recomputes every evidence and timeline
   chain, and the row counts are compared with the manifest. Exit 0 means the
   restored ledger is the one that was dumped.

```
  checksum ok: 44144c98b182d9e160c8ac1860a9c0d3bd537fe2042eef0bf1125c18ff219fed
  creating database nightjar_restored
  restoring nightjar-20261119T043811Z.dump
  migrating to head
  verifying every evidence and timeline chain
verified: 47 incidents, 664 timeline events, 220 evidence items, every chain intact
restored nightjar-20261119T043811Z.dump into postgresql://nightjar@127.0.0.1:55432/nightjar_restored
```

`--no-verify` exists for a dump that is known to be partial. It skips the checksum
and the chain verification, prints `UNVERIFIED` on stdout and a warning on stderr,
so a script that reads the output cannot mistake it for a proven restore.

## Verifying the ledger directly

```bash
nightjarctl verify-ledger --database-url "$NIGHTJAR_DATABASE_URL"
```

```
ledger: postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar
  incidents       47
  timeline events 664
  evidence items  220

OK: every chain is intact.
```

`--json` prints the same report for CI. Exit status is 0 only when every chain is
intact; a break names the incident, which chain, and the id of the first row that
does not add up.

What the chains prove: no stored row was altered, inserted, removed from the
middle, or reordered without detection, and the row is the one the product wrote.
What they do not prove on their own: that the last rows were not cut off. A hash
chain has no external anchor, which is why the backup manifest records counts and
a digest separately — a tail-truncated restore fails the count comparison even
though the remaining chain is internally consistent. Keep the manifest somewhere
the dump itself cannot be rewritten, or the check travels with what it checks.

## Restore drills

Run a drill on a schedule you would be comfortable explaining after an incident:
monthly for a small deployment, and after every upgrade that touches the schema.
A drill is the restore command above pointed at a scratch database, then dropping
that database. The CI `backup and restore` job runs the same drill on every push:
it migrates an empty database, seeds incidents, backs up, restores into a second
database, and asserts both chain validity and the manifest counts. It also tampers
with one byte of the dump and asserts the restore is refused.

An example cron entry on the database host:

```cron
17 3 * * * cd /srv/nightjar && ./scripts/pg-backup.sh --out /var/backups/nightjar >> /var/log/nightjar-backup.log 2>&1
```

Retention is yours to choose; the script does not delete old dumps, because a
backup tool that removes files on a schedule it invented is a backup tool that
eventually removes the one someone needed.
