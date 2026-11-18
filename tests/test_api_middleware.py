"""Request ids, body caps, and the API budget.

These are the three things a caller can use to hurt Nightjar without holding a
valid plan: an unbounded body, an unbounded request rate, and an uncorrelatable
error. Each is tested at the HTTP boundary, because that is where it has to work.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from nightjar.api.main import create_app
from nightjar.api.middleware import (
    RequestIdFilter,
    build_log_config,
    current_request_id,
    request_id_var,
)
from nightjar.api.ratelimit import (
    CallerLimiter,
    RateLimitDecision,
    peer_address,
    retry_after_header,
)
from nightjar.api.settings import Settings


@pytest.fixture
def limited(settings: Settings) -> TestClient:
    """An app with a budget small enough to exhaust in a test."""
    tuned = settings.model_copy(
        update={
            "api_rate_limit_enabled": True,
            "api_rate_limit_per_minute": 2,
            "api_rate_limit_burst": 2,
            "intake_webhook_secret": "s" * 40,
        }
    )
    with TestClient(create_app(tuned)) as client:
        yield client


@pytest.fixture
def small_body(settings: Settings) -> TestClient:
    """An app that refuses anything over 1 KiB."""
    tuned = settings.model_copy(update={"api_max_body_bytes": 1024})
    with TestClient(create_app(tuned)) as client:
        yield client


# --- request ids -------------------------------------------------------------


def test_request_id_is_generated_and_echoed(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert response.json()["status"] == "ok"


def test_request_id_from_the_caller_is_kept(client: TestClient) -> None:
    response = client.get("/healthz", headers={"x-request-id": "checkout-42.a_b:c"})
    assert response.headers["x-request-id"] == "checkout-42.a_b:c"


@pytest.mark.parametrize(
    "supplied",
    [
        "has space",
        "line\nbreak",
        "x" * 200,
        "semi;colon",
        "",
    ],
)
def test_unusable_request_id_is_replaced(client: TestClient, supplied: str) -> None:
    """A caller must not be able to inject newlines into the access log."""
    response = client.get("/healthz", headers={"x-request-id": supplied})
    assert response.status_code == 200
    returned = response.headers["x-request-id"]
    assert returned != supplied
    assert len(returned) == 32


def test_request_id_is_present_on_errors(settings: Settings) -> None:
    secured = settings.model_copy(update={"api_token": "correct-horse"})
    with TestClient(create_app(secured)) as client:
        missing = client.get("/v1/incidents")
        assert missing.status_code == 401
        assert missing.headers["x-request-id"]
        missing_id = missing.headers["x-request-id"]

        refused = client.get(
            "/v1/incidents", headers={"authorization": "Bearer wrong", "x-request-id": "abc"}
        )
        assert refused.status_code == 403
        assert refused.headers["x-request-id"] == "abc"
        assert missing_id != "abc"


def test_request_id_is_bound_to_logs_within_the_request(client: TestClient) -> None:
    seen: list[str] = []

    @client.app.get("/_test/log-id")
    async def log_id() -> dict[str, str]:
        seen.append(current_request_id())
        return {"request_id": current_request_id()}

    response = client.get("/_test/log-id", headers={"x-request-id": "trace-me"})
    assert response.json()["request_id"] == "trace-me"
    assert seen == ["trace-me"]
    # Outside the request the context is reset, so a later line cannot inherit it.
    assert current_request_id() == ""


def test_request_id_filter_and_log_config() -> None:
    record = logging.LogRecord("nightjar.test", logging.INFO, __file__, 1, "hi", None, None)
    token = request_id_var.set("abc123")
    try:
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "abc123"
    finally:
        request_id_var.reset(token)

    record = logging.LogRecord("nightjar.test", logging.INFO, __file__, 1, "hi", None, None)
    RequestIdFilter().filter(record)
    assert record.request_id == "-"

    config = build_log_config("INFO")
    assert "%(request_id)s" in config["formatters"]["default"]["format"]
    assert config["handlers"]["default"]["filters"] == ["request_id"]
    assert config["loggers"]["nightjar"]["handlers"] == ["default"]


# --- body caps ---------------------------------------------------------------


def test_declared_oversized_body_is_refused_before_parsing(small_body: TestClient) -> None:
    response = small_body.post(
        "/v1/incidents",
        content=b"{" + b"x" * 2000,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert "1024" in response.json()["detail"]
    assert response.headers["x-request-id"]


def test_streamed_oversized_body_is_refused_at_the_limit(small_body: TestClient) -> None:
    """No Content-Length: the cap has to be enforced while reading."""
    chunks = iter([b"{" + b"x" * 600, b"y" * 600])
    response = small_body.post(
        "/v1/incidents",
        content=chunks,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert "1024" in response.json()["detail"]


def test_body_within_the_cap_is_processed(small_body: TestClient) -> None:
    @small_body.app.post("/_test/echo")
    async def echo(payload: dict) -> dict[str, int]:
        return {"keys": len(payload)}

    response = small_body.post("/_test/echo", json={"hello": "world"})
    assert response.status_code == 200
    assert response.json() == {"keys": 1}


def test_health_is_not_body_capped(small_body: TestClient) -> None:
    assert small_body.get("/healthz").status_code == 200


# --- API budget --------------------------------------------------------------


def test_budget_is_refused_with_retry_after(limited: TestClient) -> None:
    first = limited.get("/v1/meta")
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"

    second = limited.get("/v1/meta")
    assert second.status_code == 200
    assert second.headers["x-ratelimit-remaining"] == "0"

    third = limited.get("/v1/meta")
    assert third.status_code == 429
    assert third.headers["retry-after"] == "30"
    assert third.headers["x-ratelimit-remaining"] == "0"
    assert third.headers["x-ratelimit-limit"] == "2"
    assert "rate limit exceeded" in third.json()["detail"]


def test_health_is_never_rate_limited(limited: TestClient) -> None:
    for _ in range(5):
        assert limited.get("/healthz").status_code == 200


def test_intake_spends_the_same_budget_before_reading_the_body(limited: TestClient) -> None:
    assert limited.get("/v1/meta").status_code == 200
    assert limited.get("/v1/meta").status_code == 200
    # No signature at all: the budget is checked before the body, so this is a
    # 429 rather than a 401 and the oversized body is never read.
    response = limited.post("/v1/intake/webhook", content=b"{}" * 1000)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


def test_refused_credentials_still_spend_the_budget(settings: Settings) -> None:
    """A flood of bad tokens must not be cheaper than a flood of good ones."""
    secured = settings.model_copy(
        update={
            "api_token": "correct-horse",
            "api_rate_limit_enabled": True,
            "api_rate_limit_per_minute": 1,
            "api_rate_limit_burst": 1,
        }
    )
    with TestClient(create_app(secured)) as client:
        wrong = client.get("/v1/meta", headers={"authorization": "Bearer guess"})
        assert wrong.status_code == 403
        assert wrong.headers["x-ratelimit-remaining"] == "0"

        right = client.get("/v1/meta", headers={"authorization": "Bearer correct-horse"})
        assert right.status_code == 429


def test_budget_headers_are_absent_when_disabled(client: TestClient) -> None:
    response = client.get("/v1/meta")
    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers
    assert "x-ratelimit-remaining" not in response.headers


def test_forwarded_for_is_ignored_unless_trusted() -> None:
    headers = {"x-forwarded-for": "203.0.113.9, 198.51.100.7"}
    assert peer_address(headers, "127.0.0.1") == "127.0.0.1"
    assert peer_address(headers, "127.0.0.1", trust_forwarded_for=True) == "198.51.100.7"
    assert peer_address({}, None, trust_forwarded_for=True) == "unknown"


def test_retry_after_is_never_zero() -> None:
    decision = RateLimitDecision(
        allowed=False, limit=10, remaining=0, retry_after=0.2, reset_after=6.0
    )
    assert retry_after_header(decision) == "1"


# --- the bucket itself -------------------------------------------------------


def test_bucket_refills_over_time() -> None:
    now = [0.0]
    limiter = CallerLimiter(per_minute=60, burst=2, clock=lambda: now[0])
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed
    blocked = limiter.check("a")
    assert not blocked.allowed
    assert blocked.retry_after == pytest.approx(1.0)

    now[0] = 1.0
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed


def test_buckets_are_per_caller() -> None:
    limiter = CallerLimiter(per_minute=60, burst=1, clock=lambda: 0.0)
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    assert limiter.check("b").allowed


def test_remaining_and_reset_are_reported() -> None:
    limiter = CallerLimiter(per_minute=120, burst=4, clock=lambda: 0.0)
    decision = limiter.check("a")
    assert decision.limit == 4
    assert decision.remaining == 3
    # Three tokens to earn back at 2 per second.
    assert decision.reset_after == pytest.approx(0.5)


def test_invalid_budget_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        CallerLimiter(per_minute=0)
    with pytest.raises(ValueError):
        CallerLimiter(per_minute=60, burst=0)
