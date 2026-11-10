from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """Return a time-sortable UUIDv7 string."""
    return str(uuid.uuid7())


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
