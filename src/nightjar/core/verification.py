from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from nightjar.core.enums import PredicateKind
from nightjar.core.models import VerificationPredicate, VerificationResult
from nightjar.core.secrets import SecretError, SecretVault, fingerprint

CheckFn = Callable[[VerificationPredicate], Awaitable[dict[str, Any]]]


class VerificationError(RuntimeError):
    pass


def _is_public_host(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise VerificationError(f"cannot resolve {host}: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def validate_url(url: str, *, allow_private: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise VerificationError(f"unsupported scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise VerificationError("url has no hostname")
    if not allow_private and not _is_public_host(parsed.hostname):
        raise VerificationError(f"refusing to probe non-public host: {parsed.hostname}")


@dataclass
class HttpCheck:
    allow_private: bool = False
    timeout: float = 10.0

    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        url = str(predicate.params.get("url", ""))
        validate_url(url, allow_private=self.allow_private)
        expected = int(predicate.params.get("expected_status", 200))
        consecutive = int(predicate.params.get("consecutive", 1))
        max_ms = predicate.params.get("max_p95_ms")
        if consecutive < 1 or consecutive > 50:
            raise VerificationError("consecutive must be between 1 and 50")

        statuses: list[int] = []
        timings: list[float] = []
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"user-agent": "nightjar-verifier/0.1"},
        ) as client:
            for _ in range(consecutive):
                started = time.perf_counter()
                response = await client.get(url)
                timings.append((time.perf_counter() - started) * 1000)
                statuses.append(response.status_code)
                if response.status_code != expected:
                    break
                if consecutive > 1:
                    await asyncio.sleep(float(predicate.params.get("interval_seconds", 0.25)))

        passed = len(statuses) == consecutive and all(code == expected for code in statuses)
        if max_ms is not None and timings:
            timings_sorted = sorted(timings)
            p95 = timings_sorted[max(0, int(len(timings_sorted) * 0.95) - 1)]
            passed = passed and p95 <= float(max_ms)
        else:
            p95 = None
        return {
            "url": url,
            "statuses": statuses,
            "expected_status": expected,
            "consecutive": consecutive,
            "p95_ms": round(p95, 2) if p95 is not None else None,
            "latencies_ms": [round(value, 2) for value in timings],
            "passed": passed,
        }


@dataclass
class JsonCheck:
    allow_private: bool = False
    timeout: float = 10.0

    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        url = str(predicate.params.get("url", ""))
        validate_url(url, allow_private=self.allow_private)
        path = str(predicate.params.get("path", ""))
        expected = predicate.params.get("equals")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.get(url)
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise VerificationError(f"response is not JSON: {exc}") from exc
        observed: Any = body
        for part in [item for item in path.split(".") if item]:
            if isinstance(observed, dict) and part in observed:
                observed = observed[part]
            elif isinstance(observed, list) and part.isdigit() and int(part) < len(observed):
                observed = observed[int(part)]
            else:
                raise VerificationError(f"path {path!r} not found in response")
        return {
            "url": url,
            "path": path,
            "expected": expected,
            "observed": observed,
            "status": response.status_code,
            "passed": observed == expected,
        }


@dataclass
class DnsCheck:
    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        host = str(predicate.params.get("host", ""))
        expected = predicate.params.get("contains")
        try:
            addresses = sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
        except socket.gaierror as exc:
            return {"host": host, "addresses": [], "error": str(exc), "passed": False}
        passed = (
            bool(addresses)
            if expected is None
            else any(str(expected) in address for address in addresses)
        )
        return {"host": host, "addresses": addresses, "contains": expected, "passed": passed}


@dataclass
class TlsCheck:
    timeout: float = 10.0

    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        host = str(predicate.params.get("host", ""))
        port = int(predicate.params.get("port", 443))
        minimum_days = int(predicate.params.get("minimum_days", 7))
        context = ssl.create_default_context()

        def probe() -> dict[str, Any]:
            with (
                socket.create_connection((host, port), timeout=self.timeout) as raw,
                context.wrap_socket(raw, server_hostname=host) as tls,
            ):
                certificate = tls.getpeercert()
                if certificate is None:
                    raise VerificationError("server did not present a certificate")
                not_after_raw = certificate["notAfter"]
                if not isinstance(not_after_raw, str):
                    raise VerificationError("certificate has no text expiry timestamp")
                not_after = datetime.strptime(not_after_raw, "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=UTC
                )
                remaining = (not_after - datetime.now(UTC)).days
                return {
                    "host": host,
                    "port": port,
                    "not_after": not_after.isoformat(),
                    "days_remaining": remaining,
                    "minimum_days": minimum_days,
                    "passed": remaining >= minimum_days,
                }

        return await asyncio.to_thread(probe)


@dataclass
class ConnectorCheck:
    checks: dict[str, CheckFn]

    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        name = str(predicate.params.get("check", ""))
        if name not in self.checks:
            raise VerificationError(f"no connector check registered as {name!r}")
        return await self.checks[name](predicate)


@dataclass
class CredentialCheck:
    """Prove a credential is dead by presenting it and expecting a rejection.

    The raw value comes from the vault at probe time. Evidence records only the
    status code and a fingerprint, so a verification record can be shared without
    leaking the credential it was about.
    """

    vault: SecretVault
    allow_private: bool = False
    timeout: float = 10.0

    async def __call__(self, predicate: VerificationPredicate) -> dict[str, Any]:
        ref = str(predicate.params.get("credential_ref", ""))
        url = str(predicate.params.get("probe_url", ""))
        if not ref:
            raise VerificationError("credential check requires credential_ref")
        if not url:
            raise VerificationError("credential check requires probe_url")
        validate_url(url, allow_private=self.allow_private)
        try:
            raw = self.vault.resolve(ref)
        except SecretError as exc:
            raise VerificationError(str(exc)) from exc
        expected_status = int(predicate.params.get("revoked_status", 401))
        auth = str(predicate.params.get("auth", "bearer")).lower()
        if auth not in {"bearer", "basic"}:
            raise VerificationError(
                f"credential check auth must be 'bearer' or 'basic', got {auth!r}"
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                headers={"user-agent": "nightjar-verifier/0.1"},
            ) as client:
                if auth == "basic":
                    # Stripe (and others) authenticate with the key as the Basic
                    # username. Sending it as a bearer token would be rejected
                    # even while the key is live, which would fake a revocation.
                    response = await client.get(url, auth=httpx.BasicAuth(raw, ""))
                else:
                    response = await client.get(url, headers={"authorization": f"Bearer {raw}"})
        except httpx.HTTPError as exc:
            raise VerificationError(f"credential probe failed: {exc}") from exc
        return {
            "url": url,
            "status": response.status_code,
            "revoked_status": expected_status,
            "auth": auth,
            "fingerprint": fingerprint(raw),
            "passed": response.status_code == expected_status,
        }


class Verifier:
    """Runs verification predicates and returns evidence, never a bare boolean."""

    def __init__(
        self,
        checks: dict[PredicateKind, CheckFn] | None = None,
        *,
        allow_private: bool = False,
    ) -> None:
        self._checks: dict[PredicateKind, CheckFn] = {
            PredicateKind.HTTP: HttpCheck(allow_private=allow_private),
            PredicateKind.JSON: JsonCheck(allow_private=allow_private),
            PredicateKind.DNS: DnsCheck(),
            PredicateKind.TLS: TlsCheck(),
        }
        if checks:
            self._checks.update(checks)

    def register(self, kind: PredicateKind, check: CheckFn) -> None:
        self._checks[kind] = check

    async def run(
        self,
        predicate: VerificationPredicate,
        *,
        incident_id: str,
        action_id: str | None = None,
    ) -> VerificationResult:
        check = self._checks.get(predicate.kind)
        if check is None:
            return VerificationResult(
                incident_id=incident_id,
                action_id=action_id,
                predicate_id=predicate.id,
                kind=predicate.kind,
                passed=False,
                error=f"no checker for {predicate.kind}",
            )
        try:
            observed = await asyncio.wait_for(check(predicate), timeout=30)
            passed = bool(observed.pop("passed", False))
            return VerificationResult(
                incident_id=incident_id,
                action_id=action_id,
                predicate_id=predicate.id,
                kind=predicate.kind,
                passed=passed,
                observed=observed,
            )
        except (VerificationError, httpx.HTTPError, TimeoutError, OSError) as exc:
            return VerificationResult(
                incident_id=incident_id,
                action_id=action_id,
                predicate_id=predicate.id,
                kind=predicate.kind,
                passed=False,
                error=str(exc),
            )

    async def run_all(
        self,
        predicates: list[VerificationPredicate],
        *,
        incident_id: str,
        action_id: str | None = None,
    ) -> list[VerificationResult]:
        """Run every predicate concurrently. The caller applies the verdict policy."""
        results = await asyncio.gather(
            *(self.run(p, incident_id=incident_id, action_id=action_id) for p in predicates)
        )
        return list(results)


class StabilityWindow:
    """Re-check after a delay so a transient 200 cannot fake a resolution."""

    def __init__(self, seconds: int = 900) -> None:
        self.seconds = seconds

    async def wait(self) -> None:
        await asyncio.sleep(self.seconds)

    @property
    def next_check_at(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.seconds)
