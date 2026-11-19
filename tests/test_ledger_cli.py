"""The ledger verifier: what it accepts, what it refuses, and how it says so.

The hash chains are the reason the case file is evidence rather than a log. A
verifier that only ever printed "ok" would be worse than none, so these tests
alter rows behind the product's back and assert the report names the incident,
the chain, and the row where the chain stops adding up.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from argparse import Namespace
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nightjar.api import db
from nightjar.api.repository import Repository
from nightjar.api.tables import Base, EvidenceRow, TimelineRow
from nightjar.cli import ledger
from nightjar.core.enums import EventType, EvidenceKind
from nightjar.core.models import EvidenceItem, Incident

pytestmark = pytest.mark.integration


def _url_with_database(database_url: str, name: str) -> URL:
    return make_url(database_url).set(database=name)


def _url_text(database_url: str, name: str) -> str:
    """A URL with the password intact: ``str(URL)`` masks it, which CI needs."""
    return _url_with_database(database_url, name).render_as_string(hide_password=False)


def _admin_url(database_url: str) -> str:
    return _url_text(database_url, "postgres")


async def _admin_exec(admin_dsn: str, sql: str) -> None:
    """Run one maintenance statement on its own engine and loop.

    An engine holds connections created in the event loop that opened them, so an
    engine cannot be reused across two ``asyncio.run`` calls. Everything here is
    one-shot, which sidesteps that entirely.
    """
    engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


async def _truncate(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE incidents, tenants CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def ledger_db(database_url: str) -> Iterator[str]:
    """A scratch database, so tampering here never touches another test's rows."""
    name = f"nightjar_ledger_{uuid.uuid4().hex[:8]}"
    url = _url_text(database_url, name)

    async def create_schema() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_admin_exec(_admin_url(database_url), f'CREATE DATABASE "{name}"'))
    asyncio.run(create_schema())
    try:
        yield url
    finally:
        asyncio.run(
            _admin_exec(_admin_url(database_url), f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )


@pytest.fixture(autouse=True)
def _empty_before_each(ledger_db: str) -> None:
    asyncio.run(_truncate(ledger_db))


async def _seed(database_url: str) -> tuple[str, str, str, str]:
    """One incident with three events and two evidence items; returns the ids."""
    db.configure(database_url)
    repo = Repository(db.get_session_factory())
    tenant = str(uuid.uuid4())
    await repo.ensure_tenant(tenant, f"ledger-{tenant[:8]}")
    incident, _ = await repo.create_incident(
        Incident(tenant_id=tenant, title="Checkout is returning 502")
    )
    await repo.append_event(incident.id, EventType.INCIDENT_CREATED, payload={})
    middle = await repo.append_event(incident.id, EventType.NOTE, payload={"body": "one"})
    last = await repo.append_event(incident.id, EventType.NOTE, payload={"body": "two"})
    evidence = await repo.append_evidence(
        EvidenceItem(
            incident_id=incident.id,
            kind=EvidenceKind.HTTP_RESPONSE,
            source="checkout",
            summary="502 from the origin",
            content={"status": 502},
        )
    )
    return incident.id, middle.id, last.id, evidence.id


async def _tamper_timeline(database_url: str, event_id: str, payload: dict[str, object]) -> None:
    """Edit a row the way a database operator with psql would."""
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                update(TimelineRow).where(TimelineRow.id == event_id).values(payload=payload)
            )
    finally:
        await engine.dispose()


async def _delete_event(database_url: str, event_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(delete(TimelineRow).where(TimelineRow.id == event_id))
    finally:
        await engine.dispose()


async def _tamper_evidence(database_url: str, evidence_id: str, summary: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                update(EvidenceRow).where(EvidenceRow.id == evidence_id).values(summary=summary)
            )
    finally:
        await engine.dispose()


async def _seeded(database_url: str) -> tuple[str, str, str, str]:
    try:
        return await _seed(database_url)
    finally:
        await db.dispose()


async def test_a_freshly_seeded_incident_verifies(ledger_db: str) -> None:
    await _seeded(ledger_db)
    try:
        report = await ledger.verify_database(ledger_db)
    finally:
        await db.dispose()
    assert report.ok is True
    assert report.incidents >= 1
    assert report.timeline_events >= 3
    assert report.evidence_items >= 1
    assert report.broken == []


async def test_a_changed_event_payload_breaks_the_timeline_chain(ledger_db: str) -> None:
    incident_id, middle_id, _last_id, _evidence_id = await _seeded(ledger_db)
    try:
        await _tamper_timeline(ledger_db, middle_id, {"body": "edited after the fact"})
        report = await ledger.verify_database(ledger_db)
    finally:
        await db.dispose()

    assert report.ok is False
    broken = [item for item in report.broken if item.incident_id == incident_id]
    assert [item.chain for item in broken] == ["timeline"]
    assert broken[0].broken_at == middle_id
    assert "timeline" in broken[0].reason


async def test_a_deleted_middle_event_is_reported(ledger_db: str) -> None:
    """Removing a row breaks the link to its successor, so deletion is visible."""
    incident_id, middle_id, _last_id, _evidence_id = await _seeded(ledger_db)
    try:
        await _delete_event(ledger_db, middle_id)
        report = await ledger.verify_database(ledger_db)
    finally:
        await db.dispose()

    assert report.ok is False
    assert any(
        item.incident_id == incident_id and item.chain == "timeline" for item in report.broken
    )


async def test_a_changed_evidence_summary_breaks_the_evidence_chain(ledger_db: str) -> None:
    incident_id, _middle_id, _last_id, evidence_id = await _seeded(ledger_db)
    try:
        await _tamper_evidence(ledger_db, evidence_id, "nothing to see here")
        report = await ledger.verify_database(ledger_db)
    finally:
        await db.dispose()

    assert report.ok is False
    broken = [item for item in report.broken if item.incident_id == incident_id]
    assert [item.chain for item in broken] == ["evidence"]
    assert broken[0].broken_at == evidence_id


async def test_an_empty_database_verifies(ledger_db: str) -> None:
    """A restore target that has been migrated but not filled is not an error."""
    report = await ledger.verify_database(ledger_db)
    assert report.ok is True
    assert report.incidents == 0
    assert report.timeline_events == 0
    assert report.evidence_items == 0


def test_the_cli_exit_code_follows_the_report(
    ledger_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    incident_id, middle_id, _last_id, _evidence_id = asyncio.run(_seeded(ledger_db))
    try:
        healthy = ledger.run(Namespace(database_url=ledger_db, json=True))
        assert healthy == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["incidents"] >= 1

        asyncio.run(_tamper_timeline(ledger_db, middle_id, {"body": "edited"}))
        broken = ledger.run(Namespace(database_url=ledger_db, json=True))
        captured = capsys.readouterr()
        assert broken == 1
        payload = json.loads(captured.out)
        assert payload["ok"] is False
        assert payload["broken"][0]["incident_id"] == incident_id
        assert payload["broken"][0]["chain"] == "timeline"
    finally:
        asyncio.run(db.dispose())


def test_the_text_report_names_the_break(ledger_db: str) -> None:
    incident_id, middle_id, _last_id, _evidence_id = asyncio.run(_seeded(ledger_db))
    try:
        asyncio.run(_tamper_timeline(ledger_db, middle_id, {"body": "edited"}))
        report = asyncio.run(ledger.verify_database(ledger_db))
        rendered = ledger._render(report)
        assert "BROKEN CHAINS:" in rendered
        assert incident_id in rendered
        assert middle_id in rendered
        assert "FAIL" in rendered
    finally:
        asyncio.run(db.dispose())


def test_the_parser_exposes_the_command() -> None:
    from nightjar.cli.main import _parser

    args = _parser().parse_args(["verify-ledger", "--database-url", "postgresql://localhost/x"])
    assert args.command == "verify-ledger"
    assert args.database_url == "postgresql://localhost/x"
    assert args.func is ledger.run


def test_the_environment_is_the_default_url(
    ledger_db: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A restore target can be pointed at without repeating the URL on the command line."""
    asyncio.run(_seeded(ledger_db))
    monkeypatch.setenv("NIGHTJAR_DATABASE_URL", ledger_db)
    try:
        assert ledger.run(Namespace(database_url=None, json=True)) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    finally:
        asyncio.run(db.dispose())
