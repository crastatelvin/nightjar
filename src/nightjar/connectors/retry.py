"""Retrying a connector call, but only when repeating it cannot do damage.

The rule that makes this safe is in :mod:`nightjar.connectors.errors`: an error
knows whether the provider might have applied the mutation before it failed. A
read timeout on ``POST /deployments`` may have created a deployment; retrying
creates a second one. A refused connection did not. So the retry loop asks
``safe_to_retry()`` instead of ``retryable``, and a ``429`` is always safe because
the provider rejected the request before doing anything.

``Retry-After`` wins over backoff whenever the provider sent it. Backing off on a
schedule we invented while the provider told us a number is how a retry storm
starts.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nightjar.connectors.errors import ConnectorError

DEFAULT_ATTEMPTS = 3
DEFAULT_MAX_WAIT = 30.0


@dataclass(frozen=True)
class Attempt:
    """One failed try, kept so the timeline can explain what happened."""

    number: int
    kind: str
    message: str
    waited: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.number,
            "kind": self.kind,
            "error": self.message,
            "waited_seconds": round(self.waited, 3),
        }


def backoff_seconds(attempt: int, *, base: float = 1.0, ceiling: float = 8.0) -> float:
    return float(min(ceiling, base * (2 ** (attempt - 1))))


async def call_with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    max_wait: float = DEFAULT_MAX_WAIT,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rand: Callable[[], float] = random.random,
) -> tuple[T, list[Attempt]]:
    """Run ``operation``, retrying only failures that are safe to repeat.

    Returns the result and the attempts that failed before it. Raises the last
    error when no attempt succeeded, so the caller records a real failure rather
    than a synthetic one.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    pause = sleep or asyncio.sleep
    history: list[Attempt] = []
    for number in range(1, attempts + 1):
        try:
            return await operation(), history
        except ConnectorError as exc:
            if number >= attempts or not exc.safe_to_retry():
                history.append(Attempt(number, exc.kind, str(exc), 0.0))
                raise
            if exc.retry_after is not None:
                wait = min(max_wait, max(0.0, exc.retry_after))
            else:
                # Jitter keeps a fleet of replicas from retrying in lockstep.
                wait = min(max_wait, backoff_seconds(number) * (1.0 + rand() * 0.25))
            history.append(Attempt(number, exc.kind, str(exc), wait))
            await pause(wait)
    raise AssertionError("unreachable: the loop either returns or raises")


__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_MAX_WAIT",
    "Attempt",
    "backoff_seconds",
    "call_with_retries",
]
