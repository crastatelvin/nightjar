"""Per-caller API rate limiting.

``connectors.ratelimit`` protects a provider from Nightjar. This module protects
Nightjar from its callers: a dashboard left open on a wall display, a retry loop
with no backoff, or a peer hammering the signed intake.

The budget is a token bucket per caller, held in process memory. That is honest
about what it is: with N replicas a caller can spend N times the configured
budget, so the numbers here are set well below what one process can serve. A
shared limiter is a separate piece of work; this one exists so a single runaway
client cannot take the API down for everyone.

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

# Above this many tracked callers the map is swept for buckets that have refilled
# completely. Those callers are back to a full budget and their entry carries no
# information, so dropping them changes no decision.
MAX_TRACKED_CALLERS = 20_000


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
    "RateLimitDecision",
    "peer_address",
    "retry_after_header",
]
