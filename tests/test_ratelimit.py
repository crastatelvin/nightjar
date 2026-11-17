"""The token bucket: burst, sustained rate, and the disabled path.

The clock and the sleep are injected, so "waits 0.5 s" is asserted rather than
timed. A test that actually sleeps is a test that gets flaky on CI and gets
deleted, and then the rate limiter is unverified.
"""

from __future__ import annotations

import pytest

from nightjar.connectors import ratelimit
from nightjar.connectors.ratelimit import TokenBucket, budget_for, limiter_for


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def enabled() -> None:
    ratelimit.configure(enabled=True)
    ratelimit.reset_limiters()


def bucket(rate: float = 2.0, burst: int = 2) -> tuple[TokenBucket, FakeTime]:
    time = FakeTime()
    return (
        TokenBucket(rate, burst, clock=time.clock, sleep=time.sleep, name="test"),
        time,
    )


async def test_a_burst_is_served_without_waiting() -> None:
    limiter, time = bucket(rate=2.0, burst=3)

    waits = [await limiter.acquire() for _ in range(3)]

    assert waits == [0.0, 0.0, 0.0]
    assert time.slept == []


async def test_the_sustained_rate_is_enforced() -> None:
    limiter, time = bucket(rate=2.0, burst=2)
    await limiter.acquire()
    await limiter.acquire()

    waited = await limiter.acquire()

    # One token costs half a second at two per second.
    assert waited == pytest.approx(0.5)
    assert time.slept == [pytest.approx(0.5)]


async def test_the_bucket_refills_over_time() -> None:
    limiter, time = bucket(rate=2.0, burst=2)
    await limiter.acquire()
    await limiter.acquire()
    time.now += 1.0  # two tokens of credit, capped at burst

    assert await limiter.acquire() == 0.0
    assert await limiter.acquire() == 0.0
    assert time.slept == []


async def test_the_bucket_does_not_accumulate_more_than_the_burst() -> None:
    limiter, time = bucket(rate=10.0, burst=2)
    time.now += 3600.0

    await limiter.acquire()
    await limiter.acquire()
    waited = await limiter.acquire()

    assert waited == pytest.approx(0.1)


async def test_a_disabled_limiter_never_waits() -> None:
    ratelimit.configure(enabled=False)
    limiter, time = bucket(rate=1.0, burst=1)

    waits = [await limiter.acquire() for _ in range(5)]

    assert waits == [0.0] * 5
    assert time.slept == []


async def test_a_request_larger_than_the_burst_is_a_configuration_error() -> None:
    limiter, _ = bucket(rate=1.0, burst=2)

    with pytest.raises(ValueError, match="only holds 2"):
        await limiter.acquire(cost=3)


def test_a_zero_rate_is_refused() -> None:
    with pytest.raises(ValueError, match="positive rate"):
        TokenBucket(0, 1)
    with pytest.raises(ValueError, match="at least one"):
        TokenBucket(1, 0)


def test_buckets_are_cached_per_provider() -> None:
    ratelimit.reset_limiters()

    assert limiter_for("github") is limiter_for("github")
    assert limiter_for("github") is not limiter_for("vercel")


def test_every_provider_in_the_table_has_a_budget() -> None:
    for provider in ("cloudflare", "github", "google", "microsoft", "vercel", "render", "fly"):
        budget = budget_for(provider)
        assert budget["rate_per_second"] > 0
        assert budget["burst"] >= 1
    assert budget_for("github")["rate_per_second"] == 1.0
    assert budget_for("microsoft")["rate_per_second"] == 8.0


def test_an_unknown_provider_gets_a_conservative_default() -> None:
    assert budget_for("some-new-provider") == {"rate_per_second": 5.0, "burst": 10}
