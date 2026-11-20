"""Per-caller API rate limiting.

``connectors.ratelimit`` protects a provider from Nightjar. This module protects
Nightjar from its callers: a dashboard left open on a wall display, a retry loop
with no backoff, or a peer hammering the signed intake.

The budget is a token bucket per caller. Where the bucket lives is a deployment
choice:

* **memory** (default) keeps it in the process. Simple, no database traffic, and
  honest about its limit: with N replicas a caller can spend N times the
  configured budget. Fine for one replica, which is what a small deployment runs.
* **database** keeps one bucket row per caller in PostgreSQL, refilled and debited
  in a single transaction. It costs two short statements per request and gives
  every replica the same view, so the configured number is the real budget.

Callers are keyed by address. That is the only key available before a credential
is checked, and the budget has to be charged before authentication, or a flood of
invalid tokens becomes a free denial of service. Behind a proxy, enable
``NIGHTJAR_API_TRUST_FORWARDED_FOR`` so the address is the real client's.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Above this many tracked callers the map is swept for buckets that have refilled
# completely. Those callers are back to a full budget and their entry carries no
# information, so dropping them changes no decision.
MAX_TRACKED_CALLERS = 20_000

# One in this many requests also prunes expired buckets. Cheap, and it keeps the
# table proportional to active callers rather than to every caller ever seen.
PRUNE_EVERY = 500
# A caller idle for this long has a full bucket, so the row can go.
PRUNE_AFTER_SECONDS = 3600


@dataclass(frozen=True)
class RateLimitDecision:
    """What the caller has left, and when a refused caller may try again."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: float
    reset_after: float


@dataclass
class _Bucket:
    tokens: float
    updated: float


class CallerLimiter:
    """A token bucket per key, refilled at ``per_minute`` tokens per minute.

    ``burst`` is the bucket size, so it is also the largest number of requests a
    caller can make back-to-back after being idle. A fresh caller starts full: a
    dashboard that opens ten panels at once should not be throttled by the fact
    that it has never been seen before.
    """

    def __init__(
        self,
        per_minute: int = 120,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        capacity = burst if burst is not None else per_minute
        if capacity <= 0:
            raise ValueError("burst must be positive")
        self.per_minute = per_minute
        self.capacity = float(capacity)
        self.refill_per_second = per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    async def spend(self, key: str) -> RateLimitDecision:
        """The shared interface: charge one request to ``key``."""
        return self.check(key)

    def check(self, key: str) -> RateLimitDecision:
        """Spend one token for ``key`` and report the result."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                tokens = self.capacity
            else:
                elapsed = max(0.0, now - bucket.updated)
                tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
                retry_after = 0.0
            else:
                # Time until the bucket holds the one token this request needs.
                retry_after = (1.0 - tokens) / self.refill_per_second
            self._buckets[key] = _Bucket(tokens=tokens, updated=now)
            self._sweep(now)
            return RateLimitDecision(
                allowed=allowed,
                limit=int(self.capacity),
                remaining=int(tokens),
                retry_after=retry_after,
                reset_after=(self.capacity - tokens) / self.refill_per_second,
            )

    def _sweep(self, now: float) -> None:
        if len(self._buckets) <= MAX_TRACKED_CALLERS:
            return
        idle = [
            key
            for key, bucket in self._buckets.items()
            if bucket.tokens + (now - bucket.updated) * self.refill_per_second >= self.capacity
        ]
        for key in idle:
            del self._buckets[key]
        if len(self._buckets) > MAX_TRACKED_CALLERS:
            # Still over budget: every tracked caller is active. Forget the map
            # rather than grow without bound. Callers get a fresh bucket, which
            # fails in the permissive direction, never into a lockout.
            self._buckets.clear()


class DatabaseLimiter:
    """A token bucket per caller in PostgreSQL, shared by every replica.

    Two statements in one transaction:

    1. upsert the bucket, refilling it by the elapsed time since ``updated_at``
       (capped at the burst size);
    2. debit one token, but only when the bucket holds one.

    The transaction holds the row lock across both statements, so concurrent
    replicas serialize on the caller's row and the last token is spent once.
    Debit is conditional rather than "always subtract, check later": a refused
    request must not push the bucket negative and extend the caller's wait.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        per_minute: int = 120,
        burst: int | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        prune_every: int = PRUNE_EVERY,
        prune_after_seconds: float = PRUNE_AFTER_SECONDS,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        capacity = burst if burst is not None else per_minute
        if capacity <= 0:
            raise ValueError("burst must be positive")
        self.per_minute = per_minute
        self.capacity = float(capacity)
        self.refill_per_second = per_minute / 60.0
        self._session_factory = session_factory
        self._clock = clock
        self._prune_every = max(1, prune_every)
        self._prune_after = timedelta(seconds=prune_after_seconds)
        self._calls = 0

    async def spend(self, key: str) -> RateLimitDecision:
        from sqlalchemy import text

        now = self._clock()
        # The factory contract is "give me a session maker", matching
        # ``api.db.get_session_factory``: one maker per call is cheap, and the
        # engine behind it is the deployment's pool.
        session_factory = self._session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO api_rate_limits AS bucket (key, tokens, updated_at)
                    VALUES (:key, :capacity, :now)
                    ON CONFLICT (key) DO UPDATE SET
                        tokens = LEAST(
                            :capacity,
                            bucket.tokens
                            + :rate * EXTRACT(EPOCH FROM (:now - bucket.updated_at))
                        ),
                        updated_at = :now
                    """
                ),
                {
                    "key": key,
                    "capacity": self.capacity,
                    "rate": self.refill_per_second,
                    "now": now,
                },
            )
            debited = await session.scalar(
                text(
                    """
                    UPDATE api_rate_limits
                       SET tokens = tokens - 1
                     WHERE key = :key AND tokens >= 1
                    RETURNING tokens
                    """
                ),
                {"key": key},
            )
            available = await session.scalar(
                text("SELECT tokens FROM api_rate_limits WHERE key = :key"), {"key": key}
            )
            available = float(available or 0.0)
            self._calls += 1
            if self._calls % self._prune_every == 0:
                await session.execute(
                    text("DELETE FROM api_rate_limits WHERE updated_at < :cutoff"),
                    {"cutoff": now - self._prune_after},
                )

        allowed = debited is not None
        return RateLimitDecision(
            allowed=allowed,
            limit=int(self.capacity),
            remaining=max(0, int(available)),
            retry_after=0.0 if allowed else (1.0 - available) / self.refill_per_second,
            reset_after=(self.capacity - available) / self.refill_per_second,
        )


def peer_address(
    headers: Mapping[str, str],
    client_host: str | None,
    *,
    trust_forwarded_for: bool = False,
) -> str:
    """The address a caller is billed to.

    ``X-Forwarded-For`` is only read when the deployment says a proxy sets it,
    because any caller can send that header. When it is trusted, the last entry
    is used: that is the address the nearest proxy appended, whereas everything
    to its left is caller-supplied text.
    """
    if trust_forwarded_for:
        forwarded = headers.get("x-forwarded-for", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1][:64]
    return (client_host or "unknown")[:64]


def retry_after_header(decision: RateLimitDecision) -> str:
    """``Retry-After`` is whole seconds; never advertise zero."""
    return str(max(1, math.ceil(decision.retry_after)))


__all__ = [
    "CallerLimiter",
    "DatabaseLimiter",
    "RateLimitDecision",
    "peer_address",
    "retry_after_header",
]
