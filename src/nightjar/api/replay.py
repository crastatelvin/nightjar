"""Shared replay protection for signed alert intake.

A captured delivery can be replayed at any replica while its timestamp is still
inside the signature tolerance window, so an in-process memory guard protects
exactly one process. The claim here is a single ``INSERT ... ON CONFLICT DO
NOTHING``: the database decides the winner atomically, so N replicas accept a
delivery exactly once and every loser gets a conflict instead of a duplicate
incident.

The row is claimed before the incident is filed and released if filing fails, so
a server error does not burn the sender's delivery id and lose the alert. The
signature window bounds how long a replay is interesting, so rows older than the
window are pruned.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightjar.api.tables import IntakeDeliveryRow
from nightjar.core.ids import utcnow
from nightjar.core.signatures import ReplayError

log = logging.getLogger(__name__)

# Pruning on every claim would add a write to each delivery; every so often keeps
# the table small without putting a delete in the critical path of most requests.
PRUNE_EVERY = 128


class ReplayStore(Protocol):
    """What the intake route needs: claim a delivery, or give it back."""

    async def claim(self, delivery_id: str) -> bool: ...

    async def release(self, delivery_id: str) -> None: ...


class PostgresReplayStore:
    """Replay memory shared by every API replica pointed at the same database."""

    def __init__(
        self,
        session_factory: Callable[[], async_sessionmaker[AsyncSession]],
        *,
        tenant_id: str,
        ttl_seconds: int = 600,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("replay ttl must be positive")
        if not tenant_id:
            raise ValueError("replay store needs a tenant")
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._ttl = ttl_seconds
        self._claims = 0

    async def claim(self, delivery_id: str) -> bool:
        """Record a delivery id, returning ``True`` the first time it is seen."""
        if not delivery_id:
            raise ReplayError("delivery id is empty")
        statement = (
            insert(IntakeDeliveryRow)
            .values(
                tenant_id=self._tenant_id,
                delivery_id=delivery_id,
                seen_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "delivery_id"])
            .returning(IntakeDeliveryRow.delivery_id)
        )
        async with self._session_factory()() as session:
            claimed = (await session.execute(statement)).first() is not None
            await session.commit()
        self._claims += 1
        if self._claims % PRUNE_EVERY == 0:
            try:
                await self.prune()
            except Exception:  # pragma: no cover - housekeeping, not delivery
                log.warning("could not prune accepted intake deliveries", exc_info=True)
        return claimed

    async def release(self, delivery_id: str) -> None:
        """Forget a claim whose incident could not be filed."""
        async with self._session_factory()() as session:
            await session.execute(
                delete(IntakeDeliveryRow).where(
                    IntakeDeliveryRow.tenant_id == self._tenant_id,
                    IntakeDeliveryRow.delivery_id == delivery_id,
                )
            )
            await session.commit()

    async def prune(self) -> None:
        """Drop rows whose replay window has closed.

        A row is only interesting while its delivery's timestamp is inside the
        signature tolerance, so anything older than the ttl cannot be replayed.
        """
        cutoff = utcnow() - timedelta(seconds=self._ttl)
        async with self._session_factory()() as session:
            await session.execute(
                delete(IntakeDeliveryRow).where(IntakeDeliveryRow.seen_at < cutoff)
            )
            await session.commit()


__all__ = ["PRUNE_EVERY", "PostgresReplayStore", "ReplayStore"]
