from __future__ import annotations

import pytest

from nightjar.core.enums import PredicateKind
from nightjar.core.models import VerificationPredicate
from nightjar.core.verification import (
    ConnectorCheck,
    HttpCheck,
    JsonCheck,
    VerificationError,
    Verifier,
    validate_url,
)


@pytest.fixture(autouse=True)
def healthy_lab(lab_url: str) -> None:
    """Every verification test starts from a healthy, unpoisoned lab."""
    import httpx

    httpx.post(f"{lab_url}/lab/reset", timeout=5)


def predicate(kind: PredicateKind, **params: object) -> VerificationPredicate:
    return VerificationPredicate(kind=kind, description="test predicate", params=dict(params))


def test_private_hosts_are_refused_by_default() -> None:
    with pytest.raises(VerificationError, match="non-public host"):
        validate_url("http://127.0.0.1:8100/site/", allow_private=False)
    with pytest.raises(VerificationError, match="non-public host"):
        validate_url("http://[::1]:8100/site/", allow_private=False)


def test_private_hosts_can_be_allowed_for_local_labs() -> None:
    validate_url("http://127.0.0.1:8100/site/", allow_private=True)


def test_non_http_schemes_are_refused() -> None:
    with pytest.raises(VerificationError, match="unsupported scheme"):
        validate_url("file:///etc/passwd", allow_private=True)
    with pytest.raises(VerificationError, match="no hostname"):
        validate_url("http:///site/", allow_private=True)


async def test_http_check_observes_real_status(lab_url: str) -> None:
    result = await HttpCheck(allow_private=True)(
        predicate(PredicateKind.HTTP, url=f"{lab_url}/site/", expected_status=200)
    )
    assert result["passed"] is True
    assert result["statuses"] == [200]


async def test_http_check_repeats_and_stops_on_failure(lab_url: str) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=lab_url, timeout=10) as lab:
        await lab.post("/lab/scenario/site_cache_poisoned", params={"variant": 0})
        result = await HttpCheck(allow_private=True)(
            predicate(
                PredicateKind.HTTP,
                url=f"{lab_url}/site/",
                expected_status=200,
                consecutive=5,
                interval_seconds=0.01,
            )
        )
    assert result["passed"] is False
    assert result["statuses"] == [503]


async def test_json_check_reads_a_path(lab_url: str) -> None:
    result = await JsonCheck(allow_private=True)(
        predicate(
            PredicateKind.JSON,
            url=f"{lab_url}/site/checkout",
            path="status",
            equals="ok",
        )
    )
    assert result["passed"] is True
    assert result["observed"] == "ok"


async def test_json_check_reports_a_missing_path(lab_url: str) -> None:
    verifier = Verifier(allow_private=True)
    result = await verifier.run(
        predicate(
            PredicateKind.JSON,
            url=f"{lab_url}/site/checkout",
            path="not.there",
            equals="ok",
        ),
        incident_id="incident-1",
    )
    assert result.passed is False
    assert result.error is not None
    assert "not found" in result.error


async def test_verifier_returns_failure_instead_of_raising(lab_url: str) -> None:
    verifier = Verifier(allow_private=False)
    result = await verifier.run(
        predicate(PredicateKind.HTTP, url=f"{lab_url}/site/"),
        incident_id="incident-1",
    )
    assert result.passed is False
    assert result.error is not None
    assert "non-public host" in result.error


async def test_unknown_predicate_kind_returns_failure() -> None:
    verifier = Verifier(checks={}, allow_private=True)
    verifier._checks.pop(PredicateKind.DNS)
    result = await verifier.run(
        predicate(PredicateKind.DNS, host="example.com"), incident_id="incident-1"
    )
    assert result.passed is False
    assert result.error == "no checker for dns"


async def test_custom_checks_extend_defaults(lab_url: str) -> None:
    """Regression: registering connector checks must not remove HTTP checks."""
    verifier = Verifier(
        checks={PredicateKind.CONNECTOR_CHECK: ConnectorCheck({})},
        allow_private=True,
    )
    result = await verifier.run(
        predicate(PredicateKind.HTTP, url=f"{lab_url}/site/"),
        incident_id="incident-1",
    )
    assert result.passed is True


async def test_connector_check_dispatch() -> None:
    async def always_passes(item: VerificationPredicate) -> dict[str, object]:
        return {"check": item.params.get("check"), "passed": True}

    verifier = Verifier(
        checks={
            PredicateKind.CONNECTOR_CHECK: ConnectorCheck({"credential_revoked": always_passes})
        }
    )
    result = await verifier.run(
        predicate(PredicateKind.CONNECTOR_CHECK, check="credential_revoked"),
        incident_id="incident-1",
    )
    assert result.passed is True
    assert result.observed == {"check": "credential_revoked"}


async def test_unknown_connector_check_fails() -> None:
    verifier = Verifier(checks={PredicateKind.CONNECTOR_CHECK: ConnectorCheck({})})
    result = await verifier.run(
        predicate(PredicateKind.CONNECTOR_CHECK, check="missing"),
        incident_id="incident-1",
    )
    assert result.passed is False
    assert result.error is not None
    assert "no connector check" in result.error


async def test_run_all_executes_every_predicate(lab_url: str) -> None:
    verifier = Verifier(allow_private=True)
    predicates = [
        predicate(PredicateKind.HTTP, url=f"{lab_url}/site/"),
        predicate(PredicateKind.JSON, url=f"{lab_url}/site/checkout", path="status", equals="ok"),
    ]
    results = await verifier.run_all(predicates, incident_id="incident-1")
    assert len(results) == 2
    assert all(result.passed for result in results)
