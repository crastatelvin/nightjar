"""Per-incident model budget.

Four model calls are cheap; a retry storm is not. The ceiling is charged after
every call and checked before the next one starts, so one expensive call can end
a run but cannot be followed by another. Wall-clock time works the same way: the
service bounds each call by the time that is left, so a slow provider cannot
outlive the budget by one full timeout.

Cost comes from the provider's reported usage. Some local servers report
nothing, which is why call count and elapsed time are separate ceilings: a run
is always bounded even when cost is unknown.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(RuntimeError):
    """The incident has spent its model allowance.

    Carries the limit that fired so the caller can record it as structured
    evidence instead of parsing the message.
    """

    def __init__(self, message: str, *, limit: str, observed: float, ceiling: float) -> None:
        super().__init__(message)
        self.limit = limit
        self.observed = observed
        self.ceiling = ceiling


@dataclass
class Budget:
    max_cost_usd: float
    max_seconds: float
    max_calls: int
    clock: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    cost_usd: float = 0.0
    calls: int = 0

    def __post_init__(self) -> None:
        self.started = self.clock()

    @property
    def elapsed_seconds(self) -> float:
        return self.clock() - self.started

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed_seconds)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "max_cost_usd": self.max_cost_usd,
            "max_seconds": self.max_seconds,
            "max_calls": self.max_calls,
        }

    def ensure_can_start(self, role: str) -> None:
        """Refuse to begin a call the budget cannot pay for."""
        elapsed = self.elapsed_seconds
        if elapsed >= self.max_seconds:
            raise BudgetExceeded(
                f"model budget exceeded: {elapsed:.0f} s elapsed against a "
                f"{self.max_seconds:.0f} s ceiling before {role}",
                limit="seconds",
                observed=elapsed,
                ceiling=self.max_seconds,
            )
        if self.calls >= self.max_calls:
            raise BudgetExceeded(
                f"model budget exceeded: {self.calls} model calls against a "
                f"{self.max_calls} call ceiling before {role}",
                limit="calls",
                observed=float(self.calls),
                ceiling=float(self.max_calls),
            )
        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                f"model budget exceeded: ${self.cost_usd:.2f} spent against a "
                f"${self.max_cost_usd:.2f} ceiling before {role}",
                limit="cost",
                observed=self.cost_usd,
                ceiling=self.max_cost_usd,
            )

    def charge(self, role: str, cost_usd: float) -> None:
        """Record one completed call and stop the run if it crossed a ceiling."""
        self.calls += 1
        self.cost_usd += max(0.0, cost_usd)
        elapsed = self.elapsed_seconds
        if self.cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"model budget exceeded: ${self.cost_usd:.2f} spent against a "
                f"${self.max_cost_usd:.2f} ceiling after {role}",
                limit="cost",
                observed=self.cost_usd,
                ceiling=self.max_cost_usd,
            )
        if elapsed > self.max_seconds:
            raise BudgetExceeded(
                f"model budget exceeded: {elapsed:.0f} s elapsed against a "
                f"{self.max_seconds:.0f} s ceiling after {role}",
                limit="seconds",
                observed=elapsed,
                ceiling=self.max_seconds,
            )


__all__ = ["Budget", "BudgetExceeded"]
