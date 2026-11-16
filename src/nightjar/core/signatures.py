"""Signed webhook deliveries, in both directions.

Outbound notifications and inbound alert intake share one scheme: the HMAC is
computed over ``"<unix timestamp>.<raw body>"`` so a captured request cannot be
replayed after the tolerance window, and a modified body cannot keep its
signature. Comparison is constant-time.

Two header layouts are accepted on the way in, because alert senders differ:

- ``x-nightjar-signature: t=1763000000,v1=<hex>`` (one header)
- ``x-nightjar-signature: v1=<hex>`` plus ``x-nightjar-timestamp: 1763000000``
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import OrderedDict

DEFAULT_TOLERANCE_SECONDS = 300
_SIGNATURE_SCHEMES = frozenset({"v1", "sha256"})


class SignatureError(ValueError):
    """A delivery could not be authenticated. Never includes the secret."""


class ReplayError(ValueError):
    """A delivery id was already accepted inside the replay window."""


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of ``"<timestamp>.<body>"``."""
    signed = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def parse_signature_header(value: str) -> tuple[int | None, list[str]]:
    """Split a signature header into its timestamp and digest candidates.

    Multiple ``v1`` values are accepted so a sender can rotate secrets without
    dropping deliveries: the request passes if any digest matches.
    """
    if not value or not value.strip():
        raise SignatureError("missing signature header")
    timestamp: int | None = None
    digests: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        key, separator, candidate = part.partition("=")
        if not separator:
            # A bare hex digest, as sent by some libraries.
            digests.append(part)
            continue
        key = key.strip().lower()
        candidate = candidate.strip()
        if key == "t":
            try:
                timestamp = int(candidate)
            except ValueError as exc:
                raise SignatureError("signature timestamp is not an integer") from exc
        elif key in _SIGNATURE_SCHEMES and candidate:
            digests.append(candidate)
    if not digests:
        raise SignatureError("signature header carries no digest")
    return timestamp, digests


def verify_signature(
    *,
    secret: str,
    header: str,
    body: bytes,
    timestamp_header: str | None = None,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: int | None = None,
) -> int:
    """Authenticate a delivery and return the timestamp it was signed with.

    Raises :class:`SignatureError` for a missing, malformed, stale, or invalid
    signature. Staleness is checked before the HMAC so an expired replay costs
    no comparison work, and the HMAC is always checked before the body is parsed.
    """
    if tolerance_seconds < 0:
        raise SignatureError("signature tolerance must not be negative")
    timestamp, digests = parse_signature_header(header)
    if timestamp is None and timestamp_header:
        try:
            timestamp = int(timestamp_header.strip())
        except ValueError as exc:
            raise SignatureError("timestamp header is not an integer") from exc
    if timestamp is None:
        raise SignatureError("missing signature timestamp")

    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > tolerance_seconds:
        raise SignatureError(f"signature timestamp is outside the {tolerance_seconds} s tolerance")

    expected = sign(secret, timestamp, body)
    if not any(hmac.compare_digest(expected, candidate.lower()) for candidate in digests):
        raise SignatureError("signature does not match the request body")
    return timestamp


class ReplayGuard:
    """An in-memory delivery-id guard for one process.

    This is the right tool for a single-process embedder and for tests. The API
    itself uses :class:`nightjar.api.replay.PostgresReplayStore`, because a
    captured delivery can be replayed at any replica and only a shared store can
    make the claim atomic across processes.
    """

    def __init__(self, *, ttl_seconds: int = 600, max_entries: int = 10_000) -> None:
        if ttl_seconds <= 0:
            raise ValueError("replay ttl must be positive")
        if max_entries <= 0:
            raise ValueError("replay max_entries must be positive")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    async def claim(self, delivery_id: str, *, now: float | None = None) -> bool:
        """Record a delivery id, returning ``True`` the first time it is seen."""
        if not delivery_id:
            raise ReplayError("delivery id is empty")
        current = time.monotonic() if now is None else now
        async with self._lock:
            self._prune(current)
            if delivery_id in self._seen:
                return False
            self._seen[delivery_id] = current
            while len(self._seen) > self._max_entries:
                self._seen.popitem(last=False)
            return True

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._seen:
            delivery_id, seen_at = next(iter(self._seen.items()))
            if seen_at > cutoff:
                break
            self._seen.pop(delivery_id, None)

    def __len__(self) -> int:
        return len(self._seen)


__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "ReplayError",
    "ReplayGuard",
    "SignatureError",
    "parse_signature_header",
    "sign",
    "verify_signature",
]
