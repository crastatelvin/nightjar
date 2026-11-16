from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nightjar.api import replay as replay_module
from nightjar.api.replay import PostgresReplayStore
from nightjar.api.tables import IntakeDeliveryRow, TenantRow
from nightjar.core.ids import utcnow
from nightjar.core.signatures import ReplayError


@dataclass
class Replay:
    """A store plus the pieces a test needs to inspect the table."""

    store: PostgresReplayStore
    factory: async_sessionmaker[AsyncSession]
    tenant_id: str

    async def rows(self) -> int:
        async with self.factory() as session:
            return int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(IntakeDeliveryRow)
                        .where(IntakeDeliveryRow.tenant_id == self.tenant_id)
                    )
                )
                or 0
            )

    async def add_seen(self, delivery_id: str, *, year: int | None = None) -> None:
        seen_at = utcnow() if year is None else utcnow().replace(year=year)
        async with self.factory() as session:
            session.add(
                IntakeDeliveryRow(
                    tenant_id=self.tenant_id, delivery_id=delivery_id, seen_at=seen_at
                )
            )
            await session.commit()


def unused_factory() -> async_sessionmaker[AsyncSession]:
    raise AssertionError("this store must not touch the database")


@pytest.fixture
async def replay(database_url: str) -> AsyncIterator[Replay]:
    engine: AsyncEngine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(TenantRow(id=tenant_id, name=f"replay-{tenant_id}"))
        await session.commit()
    try:
        yield Replay(
            store=PostgresReplayStore(lambda: session_factory, tenant_id=tenant_id),
            factory=session_factory,
            tenant_id=tenant_id,
        )
    finally:
        await engine.dispose()


async def test_the_first_claim_wins_and_the_second_loses(replay: Replay) -> None:
    assert await replay.store.claim("dlv-1") is True
    assert await replay.store.claim("dlv-1") is False


async def test_claims_are_independent_per_delivery(replay: Replay) -> None:
    assert await replay.store.claim("dlv-1") is True
    assert await replay.store.claim("dlv-2") is True


async def test_a_released_claim_can_be_taken_again(replay: Replay) -> None:
    """A failed filing must not burn the sender's delivery id."""
    assert await replay.store.claim("dlv-1") is True

    await replay.store.release("dlv-1")

    assert await replay.store.claim("dlv-1") is True


async def test_release_only_touches_its_own_tenant(replay: Replay) -> None:
    other_tenant = str(uuid.uuid4())
    async with replay.factory() as session:
        session.add(TenantRow(id=other_tenant, name=f"replay-{other_tenant}"))
        await session.commit()
    other = PostgresReplayStore(lambda: replay.factory, tenant_id=other_tenant)

    assert await replay.store.claim("dlv-1") is True
    assert await other.claim("dlv-1") is True

    await replay.store.release("dlv-1")

    assert await replay.store.claim("dlv-1") is True
    assert await other.claim("dlv-1") is False


async def test_two_replicas_accept_a_delivery_exactly_once(database_url: str) -> None:
    """The whole point: independent pools, one winner."""
    tenant_id = str(uuid.uuid4())
    engines = [create_async_engine(database_url) for _ in range(2)]
    factories = [async_sessionmaker(engine, expire_on_commit=False) for engine in engines]
    async with factories[0]() as session:
        session.add(TenantRow(id=tenant_id, name=f"replay-{tenant_id}"))
        await session.commit()
    try:
        replicas = [
            PostgresReplayStore(lambda f=factory: f, tenant_id=tenant_id) for factory in factories
        ]

        results = await asyncio.gather(*(replica.claim("dlv-1") for replica in replicas))

        assert results.count(True) == 1
        assert results.count(False) == 1
    finally:
        for engine in engines:
            await engine.dispose()


async def test_pruning_removes_expired_rows(replay: Replay) -> None:
    await replay.add_seen("dlv-old", year=2020)
    await replay.add_seen("dlv-fresh")

    await replay.store.prune()

    assert await replay.rows() == 1


async def test_claims_prune_on_the_housekeeping_interval(
    replay: Replay, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(replay_module, "PRUNE_EVERY", 2)
    await replay.add_seen("dlv-old", year=2020)

    assert await replay.store.claim("dlv-1") is True
    assert await replay.rows() == 2
    assert await replay.store.claim("dlv-2") is True
    assert await replay.rows() == 2


async def test_a_pruning_failure_does_not_fail_the_claim(
    replay: Replay, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Housekeeping must never turn an accepted delivery into a 500."""
    monkeypatch.setattr(replay_module, "PRUNE_EVERY", 1)
    monkeypatch.setattr(replay.store, "prune", _explode)

    assert await replay.store.claim("dlv-1") is True


async def _explode() -> None:
    raise RuntimeError("database is busy")


async def test_an_empty_delivery_id_is_refused(replay: Replay) -> None:
    with pytest.raises(ReplayError, match="empty"):
        await replay.store.claim("")


def test_a_non_positive_ttl_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        PostgresReplayStore(unused_factory, tenant_id="t", ttl_seconds=0)


def test_a_store_needs_a_tenant() -> None:
    with pytest.raises(ValueError, match="tenant"):
        PostgresReplayStore(unused_factory, tenant_id="")
