"""Typed connector failures, so the executor can decide without parsing strings.

A connector failure is not one thing. "The token is dead", "the token is fine but
lacks a scope", "the target is gone", "we are going too fast", and "the provider
had a bad minute" need different responses, and the difference has to survive the
trip from an ``httpx`` response to the executor's retry decision. String matching
on ``"429"`` inside an exception message is how retry loops become wrong.

Every error carries:

* ``kind`` — a stable, machine-readable label, recorded in the timeline;
* ``retryable`` — whether repeating the same request could plausibly work;
* ``retry_after`` — seconds the provider asked us to wait, when it said so;
* ``status`` — the HTTP status, when there was one;
* ``maybe_applied`` — whether the mutation might have landed before the failure.
  This is the field that keeps retries honest: a read timeout on a ``POST`` means
  the change may exist, so retrying could duplicate it. A connection refused
  means the request never left, so retrying is safe.

``ConnectorError`` remains the base class, so existing ``except ConnectorError``
handlers keep working and a connector can be migrated one call site at a time.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorKind = Literal[
    "error",
    "auth",
    "permission",
    "not_found",
    "conflict",
    "rate_limited",
    "transient",
    "validation",
    "propagation",
]


class ConnectorError(RuntimeError):
    """A typed operation failed. The executor records this and stops the plan."""

    kind: ErrorKind = "error"
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after: float | None = None,
        status: int | None = None,
        provider: str = "",
        maybe_applied: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = self.default_retryable if retryable is None else retryable
        self.retry_after = retry_after
        self.status = status
        self.provider = provider
        self.maybe_applied = maybe_applied

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": str(self),
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "status": self.status,
            "provider": self.provider,
            "maybe_applied": self.maybe_applied,
        }

    def safe_to_retry(self) -> bool:
        """Retry only when repeating the request cannot duplicate a mutation."""
        return self.retryable and not self.maybe_applied


class AuthError(ConnectorError):
    """401: the credential is not accepted. Retrying changes nothing."""

    kind = "auth"


class PermissionDenied(ConnectorError):
    """403: authenticated, but the credential lacks a scope or policy grant."""

    kind = "permission"


class NotFound(ConnectorError):
    """404: the target may have been deleted, which is not a Nightjar failure."""

    kind = "not_found"


class Conflict(ConnectorError):
    """409: the provider disagrees about current state. Read back, do not guess."""

    kind = "conflict"


class RateLimited(ConnectorError):
    """429: too fast. Never retried before the provider's ``Retry-After``."""

    kind = "rate_limited"
    default_retryable = True


class Transient(ConnectorError):
    """5xx or transport failure. Three attempts with jitter, if safe."""

    kind = "transient"
    default_retryable = True


class Validation(ConnectorError):
    """400/422: the request was malformed. That is a planner bug, not a blip."""

    kind = "validation"


class PropagationPending(ConnectorError):
    """Verifier-only: the provider accepted the change but has not converged."""

    kind = "propagation"
    default_retryable = True


def retry_after_seconds(response: Any) -> float | None:
    """Read ``Retry-After`` as seconds, or as an HTTP date.

    The header is seconds in practice; the date form is in the spec and a provider
    that sends it should not cause an infinite tight loop.
    """
    raw = response.headers.get("retry-after") if hasattr(response, "headers") else None
    if not raw:
        return None
    text = str(raw).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
    except TypeError, ValueError:
        return None
    from datetime import UTC, datetime

    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def error_from_response(
    provider: str,
    response: Any,
    what: str,
    *,
    maybe_applied: bool = False,
) -> ConnectorError:
    """Classify an HTTP failure. One place, so every connector agrees.

    ``maybe_applied`` is set by the caller for non-idempotent mutations whose
    response was not received; a 4xx here always means "not applied".
    """
    status = int(getattr(response, "status_code", 0) or 0)
    body = str(getattr(response, "text", ""))[:300]
    detail = f"{provider} {what} failed: {status} {body}".strip()
    if status == 401:
        return AuthError(
            f"{provider} rejected the credential (401) during {what}",
            status=status,
            provider=provider,
        )
    if status == 403:
        return PermissionDenied(
            f"{provider} refused {what} (403): the credential lacks the required "
            "scope, or provider policy blocks this operation",
            status=status,
            provider=provider,
        )
    if status == 404:
        return NotFound(f"{provider} {what}: not found (404)", status=status, provider=provider)
    if status == 409:
        return Conflict(
            f"{provider} {what} conflicted with current state (409); read back",
            status=status,
            provider=provider,
        )
    if status == 429:
        wait = retry_after_seconds(response)
        return RateLimited(
            f"{provider} {what} was rate limited (429)"
            + (f"; provider asked for {wait:.0f}s" if wait is not None else ""),
            retry_after=wait,
            status=status,
            provider=provider,
        )
    if status in {400, 422}:
        return Validation(detail, status=status, provider=provider)
    if status >= 500:
        return Transient(
            detail,
            status=status,
            provider=provider,
            maybe_applied=maybe_applied,
        )
    return ConnectorError(detail, status=status, provider=provider, maybe_applied=maybe_applied)


def transport_error(provider: str, exc: BaseException, what: str) -> Transient:
    """Map an ``httpx`` transport failure.

    A connection that was never established cannot have applied anything; a
    timeout or a dropped response can have. Getting this backwards is how a retry
    loop creates two deployments.
    """
    import httpx

    never_sent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
    return Transient(
        f"{provider} unavailable during {what}: {exc}",
        provider=provider,
        maybe_applied=not never_sent,
    )


__all__ = [
    "AuthError",
    "Conflict",
    "ConnectorError",
    "ErrorKind",
    "NotFound",
    "PermissionDenied",
    "PropagationPending",
    "RateLimited",
    "Transient",
    "Validation",
    "error_from_response",
    "retry_after_seconds",
    "transport_error",
]
