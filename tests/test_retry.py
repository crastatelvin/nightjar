"""Retry policy: honour Retry-After, never repeat a mutation that may have landed.

The sleep is injected, so the assertions are about the number the loop chose, not
about wall-clock time. The interesting cases are the refusals: a ``500`` after a
``POST`` is retryable in principle and must still not be retried, because the
deployment may already exist.
"""

from __future__ import annotations

import pytest

from nightjar.connectors.errors import (
    ConnectorError,
    NotFound,
    RateLimited,
    Transient,
    Validation,
)
from nightjar.connectors.retry import Attempt, backoff_seconds, call_with_retries


class Recorder:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def failing(*errors: Exception):
    """An operation that raises each error in turn, then returns."""
    remaining = list(errors)

    async def operation() -> str:
        if remaining:
            raise remaining.pop(0)
        return "done"

    return operation


async def test_a_first_attempt_success_does_not_sleep() -> None:
    recorder = Recorder()

    result, attempts = await call_with_retries(failing(), sleep=recorder.sleep, rand=lambda: 0.0)

    assert result == "done"
    assert attempts == []
    assert recorder.waits == []


async def test_a_rate_limit_waits_exactly_the_provider_number() -> None:
    recorder = Recorder()
    operation = failing(RateLimited("slow down", retry_after=7.0))

    result, attempts = await call_with_retries(operation, sleep=recorder.sleep, rand=lambda: 0.0)

    assert result == "done"
    assert recorder.waits == [7.0]
    assert attempts == [Attempt(1, "rate_limited", "slow down", 7.0)]


async def test_retry_after_is_capped_by_the_configured_maximum() -> None:
    recorder = Recorder()

    await call_with_retries(
        failing(RateLimited("slow down", retry_after=900.0)),
        max_wait=30.0,
        sleep=recorder.sleep,
        rand=lambda: 0.0,
    )

    assert recorder.waits == [30.0]


async def test_a_transient_error_after_a_mutation_is_not_retried() -> None:
    """A 500 on POST /deployments may have created one already."""
    recorder = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise Transient("upstream 500", maybe_applied=True)

    with pytest.raises(Transient):
        await call_with_retries(operation, sleep=recorder.sleep, rand=lambda: 0.0)

    assert calls == 1
    assert recorder.waits == []


async def test_a_transient_error_that_cannot_have_applied_is_retried() -> None:
    recorder = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Transient("connection refused", maybe_applied=False)
        return "done"

    result, attempts = await call_with_retries(operation, sleep=recorder.sleep, rand=lambda: 0.0)

    assert result == "done"
    assert calls == 3
    assert len(attempts) == 2
    # Exponential backoff with jitter; rand() is pinned to zero here.
    assert recorder.waits == [1.0, 2.0]


async def test_jitter_is_added_to_backoff() -> None:
    recorder = Recorder()

    await call_with_retries(
        failing(Transient("blip", maybe_applied=False)),
        sleep=recorder.sleep,
        rand=lambda: 0.5,
    )

    assert recorder.waits == [pytest.approx(1.125)]


async def test_an_auth_failure_is_never_retried() -> None:
    recorder = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise Validation("bad request")

    with pytest.raises(Validation):
        await call_with_retries(operation, sleep=recorder.sleep, rand=lambda: 0.0)

    assert calls == 1


async def test_a_not_found_is_never_retried() -> None:
    recorder = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise NotFound("gone")

    with pytest.raises(NotFound):
        await call_with_retries(operation, sleep=recorder.sleep, rand=lambda: 0.0)

    assert calls == 1


async def test_it_gives_up_after_the_attempt_budget() -> None:
    recorder = Recorder()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise RateLimited("still too fast", retry_after=1.0)

    with pytest.raises(RateLimited):
        await call_with_retries(operation, attempts=3, sleep=recorder.sleep, rand=lambda: 0.0)

    assert calls == 3
    assert recorder.waits == [1.0, 1.0]


async def test_the_history_explains_every_failed_attempt() -> None:
    recorder = Recorder()

    _, attempts = await call_with_retries(
        failing(
            RateLimited("first", retry_after=2.0),
            Transient("second", maybe_applied=False),
        ),
        sleep=recorder.sleep,
        rand=lambda: 0.0,
    )

    assert attempts == [
        Attempt(1, "rate_limited", "first", 2.0),
        Attempt(2, "transient", "second", 2.0),
    ]
    assert [attempt.as_dict()["waited_seconds"] for attempt in attempts] == [2.0, 2.0]


async def test_a_non_connector_error_is_not_swallowed() -> None:
    """A bug in the connector is a bug, not a retryable blip."""

    async def operation() -> str:
        raise KeyError("typo")

    with pytest.raises(KeyError):
        await call_with_retries(operation)


def test_backoff_doubles_and_stops_at_the_ceiling() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert backoff_seconds(10) == 8.0


async def test_zero_attempts_is_refused() -> None:
    async def operation() -> str:
        return "done"

    with pytest.raises(ValueError, match="at least 1"):
        await call_with_retries(operation, attempts=0)


def test_a_connector_error_that_is_not_safe_still_serialises() -> None:
    error = ConnectorError("plain", retryable=True)

    assert error.safe_to_retry() is True
    assert error.as_dict()["kind"] == "error"
