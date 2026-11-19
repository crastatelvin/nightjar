"""Backup and restore: the dump, the manifest, and the chains inside it.

These tests drive the real scripts against a real PostgreSQL server, because the
things that go wrong here are not logic errors: a dump from a newer server, a
truncated file, a restore that quietly skipped a table. The source database is a
scratch database migrated from empty, so the test also proves the migration path
and does not depend on the shared test database or on extensions it happens to
have.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from nightjar.api import db
from nightjar.api.repository import Repository
from nightjar.core.enums import EventType, EvidenceKind
from nightjar.core.models import EvidenceItem, Incident

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "scripts" / "pg-backup.sh"
RESTORE = ROOT / "scripts" / "pg-restore.sh"
ALEMBIC = Path(sys.executable).parent / "alembic"

pytestmark = pytest.mark.integration


def _libpq(url: str, *, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def _run(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed test binaries, no shell
        args,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def _admin(sql: str, database_url: str) -> subprocess.CompletedProcess[str]:
    admin = _libpq(database_url, database="postgres")
    return _run(["psql", "--no-psqlrc", "--quiet", "-d", admin, "-c", sql])


def _query(sql: str, database_url: str) -> str:
    admin = _libpq(database_url, database="postgres")
    result = _run(
        [
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "-d",
            admin,
            "-c",
            sql,
        ]
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _create_database(database_url: str, name: str) -> str:
    result = _admin(f'CREATE DATABASE "{name}"', database_url)
    assert result.returncode == 0, result.stderr
    return str(make_url(database_url).set(database=name))


def _drop_database(database_url: str, name: str) -> None:
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', database_url)


def _migrate(database_url: str) -> None:
    result = _run(
        [str(ALEMBIC), "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        env={
            "NIGHTJAR_ENVIRONMENT": "development",
            "NIGHTJAR_DATABASE_URL": database_url,
        },
    )
    assert result.returncode == 0, result.stderr


async def _seed(database_url: str) -> str:
    """Two incidents with timeline and evidence rows, so counts are meaningful."""
    db.configure(database_url)
    try:
        repo = Repository(db.get_session_factory())
        tenant = str(uuid.uuid4())
        await repo.ensure_tenant(tenant, f"backup-{tenant[:8]}")
        incident, _ = await repo.create_incident(
            Incident(tenant_id=tenant, title="Checkout is returning 502")
        )
        await repo.append_event(
            incident.id, EventType.TRIAGE_COMPLETED, payload={"incident_class": "site_outage"}
        )
        await repo.append_event(incident.id, EventType.NOTE, payload={"body": "backed up"})
        await repo.append_evidence(
            EvidenceItem(
                incident_id=incident.id,
                kind=EvidenceKind.HTTP_RESPONSE,
                source="checkout",
                summary="502 from the origin",
                content={"status": 502},
            )
        )
        second, _ = await repo.create_incident(
            Incident(tenant_id=tenant, title="Credential was posted publicly")
        )
        await repo.append_event(second.id, EventType.NOTE, payload={"body": "second"})
        return incident.id
    finally:
        await db.dispose()


@dataclass
class Source:
    database_url: str
    incident_id: str
    dump: Path
    manifest: Path
    manifest_data: dict[str, object]


@pytest.fixture(scope="module")
def source(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Source]:
    for tool in ("psql", "pg_dump", "pg_restore"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not installed")
    if not ALEMBIC.exists():
        pytest.skip("alembic is not installed in this environment")

    name = f"nightjar_backup_src_{uuid.uuid4().hex[:8]}"
    created = _create_database(database_url, name)
    try:
        _migrate(created)
        incident_id = asyncio.run(_seed(created))
        out = tmp_path_factory.mktemp("backups")
        backup = _run([str(BACKUP), "--database-url", created, "--out", str(out)])
        assert backup.returncode == 0, backup.stderr
        dump, manifest = [Path(line) for line in backup.stdout.strip().splitlines()]
        assert dump.exists() and manifest.exists()
        yield Source(
            database_url=created,
            incident_id=incident_id,
            dump=dump,
            manifest=manifest,
            manifest_data=json.loads(manifest.read_text()),
        )
    finally:
        _drop_database(database_url, name)


def _verify(url: str) -> dict[str, object]:
    result = _run(
        [
            sys.executable,
            "-m",
            "nightjar.cli.main",
            "verify-ledger",
            "--database-url",
            url,
            "--json",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_the_manifest_records_what_the_dump_contains(source: Source) -> None:
    manifest = source.manifest_data
    assert manifest["format"] == 1
    assert manifest["dump"] == source.dump.name
    assert len(str(manifest["sha256"])) == 64
    assert manifest["bytes"] == source.dump.stat().st_size
    # Started from an empty database: two incidents, three events, one evidence.
    assert manifest["counts"] == {
        "incidents": 2,
        "timeline_events": 3,
        "evidence_items": 1,
    }
    assert manifest["schema_revision"] == manifest["alembic_head"]
    assert manifest["git_sha"] != ""
    assert "pg_dump" in str(manifest["pg_dump_version"])


def test_a_restored_database_verifies(source: Source, database_url: str) -> None:
    name = f"nightjar_restore_{uuid.uuid4().hex[:8]}"
    target = str(make_url(database_url).set(database=name))
    try:
        result = _run([str(RESTORE), str(source.dump), "--database-url", target])
        assert result.returncode == 0, result.stdout + result.stderr
        assert "every chain intact" in result.stdout
        assert "verified: 2 incidents" in result.stdout

        report = _verify(target)
        assert report["ok"] is True
        assert report["incidents"] == 2
        assert report["timeline_events"] == 3
        assert report["evidence_items"] == 1

        # The case file itself came across, not just the row counts.
        case = _run(
            [
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "-d",
                _libpq(target),
                "-c",
                # The id is a generated UUID the test itself created.
                f"select title from incidents where id = '{source.incident_id}'",  # noqa: S608
            ]
        )
        assert case.stdout.strip() == "Checkout is returning 502"
    finally:
        _drop_database(database_url, name)


def test_a_tampered_dump_is_refused_before_anything_is_created(
    source: Source, database_url: str, tmp_path: Path
) -> None:
    tampered = tmp_path / "nightjar-tampered.dump"
    data = bytearray(source.dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tampered.write_bytes(data)
    shutil.copy(source.manifest, tmp_path / "nightjar-tampered.json")

    name = f"nightjar_tamper_{uuid.uuid4().hex[:8]}"
    target = str(make_url(database_url).set(database=name))
    result = _run([str(RESTORE), str(tampered), "--database-url", target])
    try:
        assert result.returncode != 0
        assert "checksum mismatch" in result.stderr
        # The refusal happens before the database is touched. The name is a
        # generated identifier, never caller input.
        exists = _query(f"select 1 from pg_database where datname = '{name}'", database_url)  # noqa: S608
        assert exists == ""
    finally:
        _drop_database(database_url, name)


def test_an_existing_database_is_not_overwritten_without_force(
    source: Source, database_url: str
) -> None:
    name = f"nightjar_existing_{uuid.uuid4().hex[:8]}"
    target = str(make_url(database_url).set(database=name))
    _create_database(database_url, name)
    try:
        refused = _run([str(RESTORE), str(source.dump), "--database-url", target])
        assert refused.returncode != 0
        assert "already exists" in refused.stderr
        assert "--force" in refused.stderr

        forced = _run([str(RESTORE), str(source.dump), "--database-url", target, "--force"])
        assert forced.returncode == 0, forced.stdout + forced.stderr
        assert "every chain intact" in forced.stdout
    finally:
        _drop_database(database_url, name)


def test_an_unverified_restore_says_so(source: Source, database_url: str) -> None:
    name = f"nightjar_unverified_{uuid.uuid4().hex[:8]}"
    target = str(make_url(database_url).set(database=name))
    try:
        result = _run([str(RESTORE), str(source.dump), "--database-url", target, "--no-verify"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert "UNVERIFIED" in result.stdout
        assert "not verified" in result.stderr
    finally:
        _drop_database(database_url, name)


def test_a_dump_without_a_manifest_is_refused(
    source: Source, database_url: str, tmp_path: Path
) -> None:
    lonely = tmp_path / "nightjar-lonely.dump"
    shutil.copy(source.dump, lonely)
    result = _run(
        [
            str(RESTORE),
            str(lonely),
            "--database-url",
            str(make_url(database_url).set(database="nightjar_missing_manifest")),
        ]
    )
    assert result.returncode != 0
    assert "manifest not found" in result.stderr


def test_backup_refuses_a_database_without_the_schema(database_url: str, tmp_path: Path) -> None:
    name = f"nightjar_empty_{uuid.uuid4().hex[:8]}"
    empty = _create_database(database_url, name)
    try:
        result = _run([str(BACKUP), "--database-url", empty, "--out", str(tmp_path)])
        assert result.returncode != 0
        assert "no Nightjar schema" in result.stderr
    finally:
        _drop_database(database_url, name)


def test_the_scripts_are_executable() -> None:
    assert BACKUP.stat().st_mode & 0o111
    assert RESTORE.stat().st_mode & 0o111
