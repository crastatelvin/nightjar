"""The upgrade path: every revision applies, and an upgrade leaves the ledger alone.

Two things are being proven here. First, that each migration in the chain works
against a real PostgreSQL server on its own, not just as one long ``upgrade head``
on an empty database. Second, and more important for this product, that upgrading
a database with case files in it does not alter a single ledger row: the chains
sealed before the upgrade still verify after it.

The second test deliberately seeds at ``2d94c6b1f0a7``, the last revision to change
``incidents``, and then upgrades to head. Anything the head revision does to
``executions`` is irrelevant to an incident, which is the point.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nightjar.api import db
from nightjar.api.repository import Repository
from nightjar.api.tables import Base
from nightjar.cli import ledger
from nightjar.core.enums import EventType, EvidenceKind
from nightjar.core.models import EvidenceItem, Incident

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC = Path(sys.executable).parent / "alembic"
CONFIG = ROOT / "alembic.ini"

pytestmark = pytest.mark.integration

# The revision whose ``incidents`` shape is the one head still uses for the ledger
# tables. Seeding here makes the upgrade do real work rather than nothing.
LAST_LEDGER_CHANGE = "2d94c6b1f0a7"


def _revisions() -> list[str]:
    """Every revision id from the base upwards, oldest first."""
    script = ScriptDirectory.from_config(Config(str(CONFIG)))
    chain: list[str] = []
    head = script.get_current_head()
    assert head is not None
    revision = script.get_revision(head)
    while revision is not None:
        chain.append(revision.revision)
        parent = revision.down_revision
        revision = script.get_revision(parent) if parent else None
    return list(reversed(chain))


def _url_text(database_url: str, *, database: str | None = None) -> str:
    """A URL with the password intact: ``str(URL)`` masks it, which CI needs."""
    parsed = make_url(database_url)
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


async def _admin(admin_dsn: str, sql: str) -> None:
    engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(sql)
    finally:
        await engine.dispose()


@contextmanager
def _scratch(database_url: str) -> Iterator[str]:
    """A throwaway database, created empty and dropped with everything in it."""
    name = f"nightjar_migrate_{uuid.uuid4().hex[:8]}"
    admin = _url_text(database_url, database="postgres")
    asyncio.run(_admin(admin, f'CREATE DATABASE "{name}"'))
    try:
        yield _url_text(database_url, database=name)
    finally:
        asyncio.run(_admin(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed alembic invocation, no shell
        [str(ALEMBIC), "-c", str(CONFIG), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "NIGHTJAR_ENVIRONMENT": "development",
            "NIGHTJAR_DATABASE_URL": url,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _upgrade(url: str, revision: str) -> None:
    result = _alembic(url, "upgrade", revision)
    assert result.returncode == 0, result.stdout + result.stderr


def _current(url: str) -> str:
    result = _alembic(url, "current")
    assert result.returncode == 0, result.stderr
    # "4b7e2c9d1a83 (head)" or "4b7e2c9d1a83"
    return result.stdout.strip().split()[0]


@pytest.fixture(scope="module", autouse=True)
def _alembic_installed() -> None:
    if not ALEMBIC.exists():
        pytest.skip("alembic is not installed in this environment")


def test_the_chain_has_the_revisions_the_tests_expect() -> None:
    revisions = _revisions()
    assert revisions[0] == "f7472f8a882e"
    assert revisions[-1] == "4b7e2c9d1a83"
    assert LAST_LEDGER_CHANGE in revisions


def test_every_revision_applies_and_reaches_head(database_url: str) -> None:
    revisions = _revisions()
    for revision in revisions:
        with _scratch(database_url) as url:
            _upgrade(url, revision)
            assert _current(url) == revision
            _upgrade(url, "head")
            assert _current(url) == revisions[-1]


def test_the_migrated_schema_has_every_modelled_column(database_url: str) -> None:
    """A model change without a migration fails here rather than in production."""
    with _scratch(database_url) as url:
        _upgrade(url, "head")

        async def columns() -> dict[str, dict[str, bool]]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as connection:
                    found = await connection.run_sync(
                        lambda sync: {
                            table: {
                                column["name"]: bool(column["nullable"])
                                for column in inspect(sync).get_columns(table)
                            }
                            for table in inspect(sync).get_table_names()
                        }
                    )
                return found
            finally:
                await engine.dispose()

        migrated = asyncio.run(columns())

    for table in Base.metadata.sorted_tables:
        assert table.name in migrated, f"{table.name} has no migration"
        for column in table.columns:
            where = f"{table.name}.{column.name}"
            assert column.name in migrated[table.name], f"{where} has no migration"
            assert migrated[table.name][column.name] is bool(column.nullable), (
                f"{where} nullability differs between the model and the migration"
            )


async def _seed(url: str) -> tuple[str, int, int]:
    db.configure(url)
    try:
        repo = Repository(db.get_session_factory())
        tenant = str(uuid.uuid4())
        await repo.ensure_tenant(tenant, f"migrate-{tenant[:8]}")
        incident, _ = await repo.create_incident(
            Incident(tenant_id=tenant, title="Checkout is returning 502")
        )
        await repo.append_event(
            incident.id, EventType.TRIAGE_COMPLETED, payload={"incident_class": "site_outage"}
        )
        await repo.append_event(incident.id, EventType.NOTE, payload={"body": "before upgrade"})
        await repo.append_evidence(
            EvidenceItem(
                incident_id=incident.id,
                kind=EvidenceKind.HTTP_RESPONSE,
                source="checkout",
                summary="502 from the origin",
                content={"status": 502},
            )
        )
        report = await ledger.verify_database(url)
        return incident.id, report.timeline_events, report.evidence_items
    finally:
        await db.dispose()


async def _verify(url: str, incident_id: str) -> tuple[str, ledger.LedgerReport]:
    db.configure(url)
    try:
        repo = Repository(db.get_session_factory())
        incident = await repo.get_incident(incident_id)
        report = await ledger.verify_database(url)
        return incident.title, report
    finally:
        await db.dispose()


def test_an_upgrade_preserves_the_ledger(database_url: str) -> None:
    with _scratch(database_url) as url:
        _upgrade(url, LAST_LEDGER_CHANGE)
        incident_id, events_before, evidence_before = asyncio.run(_seed(url))

        _upgrade(url, "head")

        title, report = asyncio.run(_verify(url, incident_id))

    assert title == "Checkout is returning 502"
    assert report.ok is True, report.broken
    assert report.timeline_events == events_before
    assert report.evidence_items == evidence_before


def test_there_is_exactly_one_head() -> None:
    """Two heads would make ``upgrade head`` ambiguous in the restore script."""
    script = ScriptDirectory.from_config(Config(str(CONFIG)))
    heads = script.get_heads()
    assert len(heads) == 1
    assert script.get_current_head() == heads[0]
