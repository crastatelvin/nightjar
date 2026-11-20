"""The shared budget: one token bucket per caller, in PostgreSQL.

The interesting property is not "it counts" but "two replicas count together".
Each test builds two limiters over independent engines pointed at the same
database, which is what a second API process looks like to the database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from nightjar.api.ratelimit import PRUNE_AFTER_SECONDS, DatabaseLimiter, RateLimitDecision
from nightjar.api.tables import RateLimitRow


@dataclass
class Replicas:
    """Two API processes sharing one database, and the clock they both read."""

    first: DatabaseLimiter
    second: DatabaseLimiter
    factory: async_sessionmaker
    key: str
    clock: dict[str, datetime]

    @property
    def now(self) -> datetime:
        return self.clock["now"]

    def advance(self, seconds: float) -> None:
        self.clock["now"] += timedelta(seconds=seconds)

    async def rows(self) -> int:
        async with self.factory() as session:
            return int((await session.scalar(select(func.count()).select_from(RateLimitRow))) or 0)


@pytest.fixture
async def replicas(database_url: str) -> AsyncIterator[Replicas]:
    engines: list[AsyncEngine] = [create_async_engine(database_url) for _ in range(2)]
    factories = [async_sessionmaker(engine, expire_on_commit=False) for engine in engines]
    state = {"now": datetime.now(UTC)}

    def clock() -> datetime:
        return state["now"]

    async with factories[0]() as session:
        await session.execute(delete(RateLimitRow))
        # Without the commit the delete rolls back when the session closes, and a
        # later test inherits a half-spent bucket from an earlier one.
        await session.commit()

    picture = Replicas(
        first=DatabaseLimiter(lambda: factories[0], per_minute=60, burst=5, clock=clock),
        second=DatabaseLimiter(lambda: factories[1], per_minute=60, burst=5, clock=clock),
        factory=factories[0],
        key="peer:203.0.113.10",
        clock=state,
    )
    try:
        yield picture
    finally:
        for engine in engines:
            await engine.dispose()


async def test_a_fresh_caller_starts_with_a_full_bucket(replicas: Replicas) -> None:
    decision = await replicas.first.spend(replicas.key)
    assert decision.allowed is True
    assert decision.limit == 5
    assert decision.remaining == 4


async def test_two_replicas_share_one_budget(replicas: Replicas) -> None:
    """The whole point: N processes, one budget, not N budgets."""
    decisions = [
        await (replicas.first if index % 2 == 0 else replicas.second).spend(replicas.key)
        for index in range(6)
    ]

    assert [item.allowed for item in decisions] == [True] * 5 + [False]
    assert decisions[-1].remaining == 0


async def test_a_refused_request_costs_nothing(replicas: Replicas) -> None:
    for _ in range(5):
        await replicas.first.spend(replicas.key)

    first_refusal = await replicas.first.spend(replicas.key)
    second_refusal = await replicas.second.spend(replicas.key)

    assert first_refusal.allowed is False
    assert second_refusal.allowed is False
    # A refusal that still debited would push the bucket negative and lengthen
    # the caller's wait every time it retried. One token refills in one second
    # at 60 per minute, so both refusals quote the same short wait.
    assert first_refusal.retry_after == pytest.approx(1.0, abs=0.05)
    assert second_refusal.retry_after == pytest.approx(first_refusal.retry_after, abs=0.05)


async def test_the_bucket_refills_with_elapsed_time(replicas: Replicas) -> None:
    for _ in range(5):
        await replicas.first.spend(replicas.key)
    assert (await replicas.first.spend(replicas.key)).allowed is False

    replicas.advance(60)  # 60 tokens/minute: one minute refills the bucket
    decision = await replicas.second.spend(replicas.key)

    assert decision.allowed is True
    assert decision.remaining == 4


async def test_the_bucket_never_exceeds_its_burst(replicas: Replicas) -> None:
    await replicas.first.spend(replicas.key)
    replicas.advance(3600)

    decision = await replicas.first.spend(replicas.key)
    assert decision.allowed is True
    assert decision.remaining == 4


async def test_callers_are_isolated(replicas: Replicas) -> None:
    for _ in range(5):
        await replicas.first.spend(replicas.key)

    other = await replicas.first.spend("peer:198.51.100.7")
    assert other.allowed is True
    assert other.remaining == 4


async def test_reset_after_describes_returning_to_a_full_bucket(replicas: Replicas) -> None:
    for _ in range(4):
        await replicas.first.spend(replicas.key)
    decision: RateLimitDecision = await replicas.first.spend(replicas.key)
    # The bucket is empty and refills at one per second: a full five is 5 s away.
    assert decision.allowed is True
    assert decision.remaining == 0
    assert decision.reset_after == pytest.approx(5.0, abs=0.2)


async def test_idle_buckets_are_pruned(replicas: Replicas) -> None:
    # The sweep is normally every 500 calls; this limiter prunes on every call so
    # the test does not have to pretend to be a burst of traffic.
    limiter = DatabaseLimiter(
        lambda: replicas.factory,
        per_minute=60,
        burst=5,
        clock=lambda: replicas.now,
        prune_every=1,
    )
    for index in range(10):
        await limiter.spend(f"peer:192.0.2.{index}")
    assert await replicas.rows() == 10

    replicas.advance(PRUNE_AFTER_SECONDS + 60)
    await limiter.spend("peer:192.0.2.200")

    # Only the caller that just refilled survives; idle rows carry no information.
    assert await replicas.rows() == 1


async def test_the_last_token_is_spent_once_when_two_replicas_race(
    replicas: Replicas,
) -> None:
    """Row locking, not application timing, is what makes the budget shared."""
    async with replicas.factory() as session, session.begin():
        session.add(RateLimitRow(key="peer:198.51.100.99", tokens=1.0, updated_at=replicas.now))

    decisions = await asyncio.gather(
        replicas.first.spend("peer:198.51.100.99"),
        replicas.second.spend("peer:198.51.100.99"),
    )

    assert sorted(item.allowed for item in decisions) == [False, True]


def test_the_limiter_backend_is_wired_from_settings() -> None:
    from nightjar.api.main import _build_limiter
    from nightjar.api.settings import Settings

    shared = _build_limiter(
        Settings(
            environment="test",
            api_rate_limit_backend="database",
            api_rate_limit_per_minute=60,
            api_rate_limit_burst=5,
        )
    )
    assert isinstance(shared, DatabaseLimiter)
    assert shared.capacity == 5.0

    local = _build_limiter(Settings(environment="test", api_rate_limit_backend="memory"))
    from nightjar.api.ratelimit import CallerLimiter

    assert isinstance(local, CallerLimiter)
    assert _build_limiter(Settings(environment="test", api_rate_limit_enabled=False)) is None


def test_the_backend_must_be_a_known_name() -> None:
    from nightjar.api.settings import Settings

    with pytest.raises(ValueError, match="NIGHTJAR_API_RATE_LIMIT_BACKEND"):
        Settings(environment="test", api_rate_limit_backend="redis")
    assert Settings(environment="test", api_rate_limit_backend="database").api_rate_limit_backend
