"""The error taxonomy: what each HTTP failure means and whether retrying is safe.

These are the rules the executor relies on, so they are tested directly rather
than inferred from a connector's behaviour. The ``maybe_applied`` cases matter
most: a retry that duplicates a deployment is a worse incident than the one being
fixed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from httpx import Response

from nightjar.connectors.errors import (
    AuthError,
    Conflict,
    ConnectorError,
    NotFound,
    PermissionDenied,
    PropagationPending,
    RateLimited,
    Transient,
    Validation,
    error_from_response,
    retry_after_seconds,
    transport_error,
)


def classify(status: int, *, headers: dict[str, str] | None = None) -> ConnectorError:
    return error_from_response("acme", Response(status, headers=headers or {}), "purge")


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (400, "validation", False),
        (401, "auth", False),
        (403, "permission", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (422, "validation", False),
        (429, "rate_limited", True),
        (500, "transient", True),
        (502, "transient", True),
        (503, "transient", True),
    ],
)
def test_each_status_maps_to_one_kind(status: int, kind: str, retryable: bool) -> None:
    error = classify(status)

    assert error.kind == kind
    assert error.retryable is retryable
    assert error.status == status
    assert error.provider == "acme"


def test_a_401_names_the_credential_not_the_target() -> None:
    error = classify(401)

    assert isinstance(error, AuthError)
    assert "credential" in str(error)
    # The response body is not repeated: it can echo the credential back.
    assert "purge" in str(error)


def test_a_403_explains_the_missing_scope() -> None:
    error = classify(403)

    assert isinstance(error, PermissionDenied)
    assert "scope" in str(error)


def test_a_404_does_not_claim_nightjar_broke_something() -> None:
    error = classify(404)

    assert isinstance(error, NotFound)
    assert "not found" in str(error)


def test_a_409_says_to_read_back() -> None:
    error = classify(409)

    assert isinstance(error, Conflict)
    assert "read back" in str(error)


def test_a_429_carries_retry_after() -> None:
    error = classify(429, headers={"retry-after": "7"})

    assert isinstance(error, RateLimited)
    assert error.retry_after == 7.0
    assert "7s" in str(error)


def test_a_429_without_retry_after_is_still_retryable() -> None:
    error = classify(429)

    assert error.retry_after is None
    assert error.retryable is True


def test_a_retry_after_http_date_is_understood() -> None:
    when = datetime.now(UTC) + timedelta(seconds=30)
    response = Response(429, headers={"retry-after": format_datetime(when, usegmt=True)})

    wait = retry_after_seconds(response)

    assert wait is not None
    assert 25 <= wait <= 31


def test_a_nonsense_retry_after_is_ignored() -> None:
    assert retry_after_seconds(Response(429, headers={"retry-after": "soon"})) is None


def test_a_5xx_after_a_mutation_is_not_safe_to_retry() -> None:
    error = error_from_response("acme", Response(500), "deployment rollback", maybe_applied=True)

    assert isinstance(error, Transient)
    assert error.retryable is True
    assert error.maybe_applied is True
    assert error.safe_to_retry() is False


def test_a_4xx_after_a_mutation_is_safe_because_nothing_applied() -> None:
    error = error_from_response("acme", Response(409), "deployment rollback", maybe_applied=True)

    assert error.retryable is False
    assert error.safe_to_retry() is False


def test_an_unexpected_status_stays_generic() -> None:
    error = classify(418)

    assert type(error) is ConnectorError
    assert error.kind == "error"
    assert error.retryable is False


def test_a_refused_connection_is_safe_to_retry() -> None:
    error = transport_error("acme", httpx.ConnectError("refused"), "POST /deploy")

    assert isinstance(error, Transient)
    assert error.maybe_applied is False
    assert error.safe_to_retry() is True
    assert "unavailable" in str(error)


def test_a_read_timeout_is_not_safe_to_retry() -> None:
    """The request may have been processed before the response was lost."""
    error = transport_error("acme", httpx.ReadTimeout("timed out"), "POST /deploy")

    assert error.maybe_applied is True
    assert error.safe_to_retry() is False


def test_a_connect_timeout_is_safe_to_retry() -> None:
    error = transport_error("acme", httpx.ConnectTimeout("no answer"), "POST /deploy")

    assert error.maybe_applied is False
    assert error.safe_to_retry() is True


def test_a_propagation_error_is_verifier_only_and_retryable() -> None:
    error = PropagationPending("Graph has not converged")

    assert error.kind == "propagation"
    assert error.safe_to_retry() is True


def test_the_error_serialises_for_the_timeline() -> None:
    detail = classify(429, headers={"retry-after": "3"}).as_dict()

    assert detail["kind"] == "rate_limited"
    assert detail["retry_after"] == 3.0
    assert detail["status"] == 429
    assert "purge" in detail["message"]


def test_a_subclass_can_override_the_default_retryability() -> None:
    error = Validation("bad request", retryable=True)

    assert error.retryable is True
