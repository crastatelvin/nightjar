from __future__ import annotations

import pytest

from nightjar.core.signatures import (
    ReplayError,
    ReplayGuard,
    SignatureError,
    parse_signature_header,
    sign,
    verify_signature,
)

SECRET = "a" * 40
BODY = b'{"title":"Checkout is failing"}'
NOW = 1_763_000_000


def test_a_fresh_signature_verifies_and_returns_its_timestamp() -> None:
    digest = sign(SECRET, NOW, BODY)
    assert (
        verify_signature(
            secret=SECRET,
            header=f"t={NOW},v1={digest}",
            body=BODY,
            now=NOW,
        )
        == NOW
    )


def test_a_tampered_body_is_rejected() -> None:
    digest = sign(SECRET, NOW, BODY)
    with pytest.raises(SignatureError, match="does not match"):
        verify_signature(
            secret=SECRET,
            header=f"t={NOW},v1={digest}",
            body=BODY + b" ",
            now=NOW,
        )


def test_the_wrong_secret_is_rejected() -> None:
    digest = sign("b" * 40, NOW, BODY)
    with pytest.raises(SignatureError, match="does not match"):
        verify_signature(secret=SECRET, header=f"t={NOW},v1={digest}", body=BODY, now=NOW)


def test_a_stale_timestamp_is_rejected_before_the_comparison() -> None:
    digest = sign(SECRET, NOW - 301, BODY)
    with pytest.raises(SignatureError, match="outside the 300 s tolerance"):
        verify_signature(
            secret=SECRET,
            header=f"t={NOW - 301},v1={digest}",
            body=BODY,
            now=NOW,
        )


def test_a_future_timestamp_is_rejected() -> None:
    digest = sign(SECRET, NOW + 301, BODY)
    with pytest.raises(SignatureError, match="outside"):
        verify_signature(
            secret=SECRET,
            header=f"t={NOW + 301},v1={digest}",
            body=BODY,
            now=NOW,
        )


def test_the_tolerance_boundary_is_inclusive() -> None:
    digest = sign(SECRET, NOW - 300, BODY)
    assert (
        verify_signature(
            secret=SECRET,
            header=f"t={NOW - 300},v1={digest}",
            body=BODY,
            now=NOW,
        )
        == NOW - 300
    )


def test_a_missing_timestamp_is_rejected() -> None:
    digest = sign(SECRET, NOW, BODY)
    with pytest.raises(SignatureError, match="missing signature timestamp"):
        verify_signature(secret=SECRET, header=f"v1={digest}", body=BODY, now=NOW)


def test_a_malformed_timestamp_is_rejected() -> None:
    digest = sign(SECRET, NOW, BODY)
    with pytest.raises(SignatureError, match="not an integer"):
        verify_signature(secret=SECRET, header=f"t=soon,v1={digest}", body=BODY, now=NOW)


def test_an_empty_header_is_rejected() -> None:
    with pytest.raises(SignatureError, match="missing signature header"):
        verify_signature(secret=SECRET, header="", body=BODY, now=NOW)


def test_a_header_without_a_digest_is_rejected() -> None:
    with pytest.raises(SignatureError, match="no digest"):
        verify_signature(secret=SECRET, header=f"t={NOW}", body=BODY, now=NOW)


def test_the_timestamp_may_arrive_in_its_own_header() -> None:
    digest = sign(SECRET, NOW, BODY)
    assert (
        verify_signature(
            secret=SECRET,
            header=f"v1={digest}",
            body=BODY,
            timestamp_header=str(NOW),
            now=NOW,
        )
        == NOW
    )


def test_a_bare_digest_with_a_timestamp_header_is_accepted() -> None:
    digest = sign(SECRET, NOW, BODY)
    assert (
        verify_signature(
            secret=SECRET,
            header=digest,
            body=BODY,
            timestamp_header=str(NOW),
            now=NOW,
        )
        == NOW
    )


def test_a_digest_list_passes_if_any_candidate_matches() -> None:
    digest = sign(SECRET, NOW, BODY)
    header = f"t={NOW},v1={'0' * 64},v1={digest}"
    assert verify_signature(secret=SECRET, header=header, body=BODY, now=NOW) == NOW


def test_parse_signature_header_lowercases_schemes_and_keeps_bare_digests() -> None:
    timestamp, digests = parse_signature_header(f"T={NOW},V1={'f' * 64}")
    assert timestamp == NOW
    assert digests == ["f" * 64]
    assert parse_signature_header("a" * 64) == (None, ["a" * 64])


def test_a_negative_tolerance_is_a_configuration_error() -> None:
    with pytest.raises(SignatureError, match="negative"):
        verify_signature(secret=SECRET, header="t=1,v1=00", body=BODY, tolerance_seconds=-1)


async def test_replay_guard_accepts_a_delivery_once() -> None:
    guard = ReplayGuard(ttl_seconds=600)
    assert await guard.claim("dlv-1", now=100.0) is True
    assert await guard.claim("dlv-1", now=101.0) is False
    assert await guard.claim("dlv-2", now=101.0) is True
    assert len(guard) == 2


async def test_replay_guard_forgets_ids_after_the_window() -> None:
    guard = ReplayGuard(ttl_seconds=10)
    assert await guard.claim("dlv-1", now=100.0) is True
    assert await guard.claim("dlv-1", now=109.0) is False
    assert await guard.claim("dlv-1", now=111.0) is True


async def test_replay_guard_evicts_the_oldest_entry_at_the_cap() -> None:
    guard = ReplayGuard(ttl_seconds=600, max_entries=2)
    assert await guard.claim("a", now=100.0) is True
    assert await guard.claim("b", now=101.0) is True
    assert await guard.claim("c", now=102.0) is True
    assert len(guard) == 2
    assert await guard.claim("a", now=103.0) is True


async def test_replay_guard_refuses_an_empty_id() -> None:
    with pytest.raises(ReplayError, match="empty"):
        await ReplayGuard().claim("")
