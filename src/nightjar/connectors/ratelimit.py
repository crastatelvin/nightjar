"""Per-provider token buckets, so Nightjar does not get itself rate limited.

Provider APIs are not uniformly generous, and an incident response that fires six
parallel reads at GitHub earns a 403 that looks exactly like a dead token. The
budgets below are deliberately conservative: the point is not to maximize
throughput, it is to never have to explain to an operator that the revocation
failed because Nightjar was noisy.

The bucket is process-local. That is honest for the current single-replica
deployment and wrong for a fleet: N replicas each honour the budget, so the
effective rate is N times the table. The table's numbers are far below every
provider's real limit for exactly that reason, and a shared limiter is recorded
in the readiness plan rather than pretended.

``429`` is never retried before ``Retry-After``; the bucket prevents the 429, and
the error taxonomy carries the header when one arrives anyway.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

# Provider → (requests per second, burst). Numbers from the readiness plan, which
# derived them from each provider's published limit with headroom for retries.
BUDGETS: dict[str, tuple[float, int]] = {
    "cloudflare": (4.0, 8),
    "github": (1.0, 3),
    "google": (10.0, 20),
    "microsoft": (8.0, 16),
    "vercel": (2.0, 4),
    "render": (1.0, 2),
    "fly": (1.0, 3),
    "stripe": (4.0, 8),
}

_ENABLED = True


def configure(*, enabled: bool | None = None) -> None:
    """Turn limiting off for tests that are not about limiting.

    The bucket is exercised directly with a fake clock in
    ``tests/test_ratelimit.py``; integration tests would otherwise pay real
    seconds for a control that is already proven.
    """
    global _ENABLED
    if enabled is not None:
        _ENABLED = enabled


class TokenBucket:
    """A classic token bucket: ``rate`` per second, at most ``burst`` saved up."""

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] | None = None,
        name: str = "",
    ) -> None:
        if rate <= 0:
            raise ValueError("a token bucket needs a positive rate")
        if burst < 1:
            raise ValueError("a token bucket needs room for at least one request")
        self.rate = rate
        self.burst = burst
        self.name = name
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        return self._tokens

    async def acquire(self, *, cost: float = 1.0) -> float:
        """Wait until a token is available and return how long it waited."""
        if not _ENABLED:
            return 0.0
        if cost > self.burst:
            raise ValueError(
                f"request costs {cost} tokens but the {self.name or 'provider'} bucket "
                f"only holds {self.burst}"
            )
        waited = 0.0
        async with self._lock:
            self._refill()
            if self._tokens < cost:
                wait = (cost - self._tokens) / self.rate
                # Reserve the tokens before sleeping so a concurrent waiter sees
                # the debt instead of both of us taking the same token.
                self._tokens -= cost
                self._updated += wait
                waited = wait
                await self._sleep(wait)
            else:
                self._tokens -= cost
            return waited

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._updated = now


_BUCKETS: dict[str, TokenBucket] = {}


def limiter_for(
    provider: str, *, rate: float | None = None, burst: int | None = None
) -> TokenBucket:
    """The process-wide bucket for a provider, created on first use."""
    bucket = _BUCKETS.get(provider)
    if bucket is not None and rate is None and burst is None:
        return bucket
    default_rate, default_burst = BUDGETS.get(provider, (5.0, 10))
    bucket = TokenBucket(
        rate if rate is not None else default_rate,
        burst if burst is not None else default_burst,
        name=provider,
    )
    _BUCKETS[provider] = bucket
    return bucket


def reset_limiters() -> None:
    """Drop cached buckets. Tests use this so one case cannot drain another's."""
    _BUCKETS.clear()


def budget_for(provider: str) -> dict[str, float]:
    rate, burst = BUDGETS.get(provider, (5.0, 10))
    return {"rate_per_second": rate, "burst": burst}


__all__ = [
    "BUDGETS",
    "TokenBucket",
    "budget_for",
    "configure",
    "limiter_for",
    "reset_limiters",
]
