"""Cross-process mutual exclusion through PostgreSQL advisory locks.

The in-process ``asyncio.Lock`` is correct for one API process and wrong for two:
each replica has its own dictionary, so both can execute the same incident, and
the stability scheduler can run twice. PostgreSQL session-level advisory locks
give the same guarantee across processes without adding Redis.

Two rules make this work:

* The lock is held by a **dedicated connection**, not a session or a transaction.
  A session-level advisory lock is released when its connection closes, and
  SQLAlchemy returns a pooled connection to the pool on commit — the classic way
  to lose the lock in the middle of the critical section. The lock engine uses
  ``NullPool`` so the connection lives exactly as long as the context.
* The lock is acquired with the two-integer form, so the scheduler namespace and
  an incident hash cannot collide, and there is no 64-bit typing problem.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Advisory-lock namespaces. Postgres keys the pair (classid, objid), so these
# never collide with an incident key.
INCIDENT_LOCK_NAMESPACE = 0x6E6A  # "nj"
SCHEDULER_LOCK_NAMESPACE = 0x6E6B  # "nk"
SCHEDULER_LOCK_KEY = 1


class LockBusy(RuntimeError):
    """Another process holds the lock, so this caller must not proceed."""

    def __init__(self, name: str) -> None:
        super().__init__(f"another Nightjar process is handling {name}")
        self.name = name


def incident_lock_key(incident_id: str) -> int:
    """A stable positive int32 derived from the incident id.

    ``pg_try_advisory_lock(int4, int4)`` keeps this simple and avoids the
    ``hashtextextended`` bigint round-trip that asyncpg rejects as out of int32
    range when the driver guesses the type.
    """
    digest = hashlib.sha256(incident_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 2_147_483_647


@asynccontextmanager
async def _try_lock(
    engine: AsyncEngine, namespace: int, key: int, *, name: str
) -> AsyncIterator[None]:
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:namespace, :key)"),
            {"namespace": namespace, "key": key},
        )
        if not acquired:
            raise LockBusy(name)
        try:
            yield
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {"namespace": namespace, "key": key},
            )


def incident_lock(engine: AsyncEngine, incident_id: str) -> AbstractAsyncContextManager[None]:
    """Exclusive, cross-process lock for one incident.

    Raises ``LockBusy`` immediately rather than waiting: the API answers ``409``
    and a background spawn simply stops, because another process is already doing
    the same work.
    """
    return _try_lock(
        engine,
        INCIDENT_LOCK_NAMESPACE,
        incident_lock_key(incident_id),
        name=f"incident {incident_id}",
    )


@asynccontextmanager
async def scheduler_leader(engine: AsyncEngine) -> AsyncIterator[bool]:
    """Yield ``True`` for the one replica allowed to run due verifications.

    The lock is taken per iteration and released immediately, so a crashed leader
    costs at most one tick and a follower takes over without an election protocol.
    """
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:namespace, :key)"),
            {"namespace": SCHEDULER_LOCK_NAMESPACE, "key": SCHEDULER_LOCK_KEY},
        )
        try:
            yield bool(acquired)
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :key)"),
                    {"namespace": SCHEDULER_LOCK_NAMESPACE, "key": SCHEDULER_LOCK_KEY},
                )


__all__ = [
    "INCIDENT_LOCK_NAMESPACE",
    "SCHEDULER_LOCK_KEY",
    "SCHEDULER_LOCK_NAMESPACE",
    "LockBusy",
    "incident_lock",
    "incident_lock_key",
    "scheduler_leader",
]
