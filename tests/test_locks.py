from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from nightjar.agents.mock import MockAgentRuntime
from nightjar.api import db
from nightjar.api.locks import (
    LockBusy,
    incident_lock,
    incident_lock_key,
    scheduler_leader,
)
from nightjar.api.repository import Repository
from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.core.enums import IncidentStatus
from nightjar.core.models import Incident


@pytest.fixture
async def lock_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_the_key_is_stable_and_inside_int32() -> None:
    first = incident_lock_key("inc_abc")
    assert first == incident_lock_key("inc_abc")
    assert first != incident_lock_key("inc_abd")
    assert 0 <= first < 2_147_483_647


async def test_a_second_holder_is_refused(lock_engine: AsyncEngine) -> None:
    async with incident_lock(lock_engine, "inc_1"):
        with pytest.raises(LockBusy, match="incident inc_1"):
            async with incident_lock(lock_engine, "inc_1"):
                pass  # pragma: no cover - the acquisition must fail


async def test_the_lock_is_released_for_the_next_holder(lock_engine: AsyncEngine) -> None:
    async with incident_lock(lock_engine, "inc_2"):
        pass
    async with incident_lock(lock_engine, "inc_2"):
        pass


async def test_the_lock_survives_a_commit_on_another_connection(
    lock_engine: AsyncEngine, database_url: str
) -> None:
    """The classic bug: a pooled connection commit must not drop the lock."""
    other = create_async_engine(database_url)
    try:
        async with incident_lock(lock_engine, "inc_3"):
            async with other.begin() as connection:
                await connection.execute(text("SELECT 1"))
            with pytest.raises(LockBusy):
                async with incident_lock(lock_engine, "inc_3"):
                    pass  # pragma: no cover
    finally:
        await other.dispose()


async def test_only_one_replica_leads_the_scheduler(lock_engine: AsyncEngine) -> None:
    async with scheduler_leader(lock_engine) as first, scheduler_leader(lock_engine) as second:
        assert first is True
        assert second is False
    async with scheduler_leader(lock_engine) as third:
        assert third is True


async def test_a_locked_incident_is_not_run_by_a_second_process(
    settings: Settings, database_url: str
) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    db.configure(database_url)
    try:
        repo = Repository(db.get_session_factory())
        await repo.ensure_tenant(settings.tenant_id, "locks")
        stored, created = await repo.create_incident(
            Incident(tenant_id=settings.tenant_id, title="Checkout is returning 502")
        )
        assert created is True
        service = IncidentService(
            settings=settings,
            repository=repo,
            runtime=MockAgentRuntime(),
            registry=ConnectorRegistry([]),
            lock_engine=engine,
        )

        async with incident_lock(engine, stored.id):
            with pytest.raises(LockBusy):
                await service.run_pipeline(stored.id)

        after = await repo.get_incident(stored.id)
        assert after.status is IncidentStatus.INTAKE
    finally:
        await engine.dispose()
        await db.dispose()


async def test_without_a_lock_engine_the_service_stays_in_process(
    settings: Settings,
) -> None:
    import asyncio

    service = IncidentService(
        settings=settings,
        repository=Repository(db.get_session_factory()),
        runtime=MockAgentRuntime(),
        registry=ConnectorRegistry([]),
    )
    order: list[str] = []

    async def worker(name: str) -> None:
        async with service._lock("inc_local"):
            order.append(f"{name}:in")
            await asyncio.sleep(0.01)
            order.append(f"{name}:out")

    await asyncio.gather(worker("a"), worker("b"))

    assert order[0].endswith(":in")
    assert order[1] == order[0].replace(":in", ":out")
    assert order[2].endswith(":in")
    assert order[3] == order[2].replace(":in", ":out")
