"""Prove that the local engine and the Rego reference return identical results.

The Rego policy is the reference implementation. If the two engines disagree on
decision, reasons, matched rules or required approvals, this test fails.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from nightjar.core.enums import (
    ActionType,
    IncidentClass,
    RiskClass,
    Severity,
)
from nightjar.core.models import ActionSpec
from nightjar.core.policy import (
    LocalPolicyEngine,
    OpaPolicyEngine,
    PolicyContext,
    PolicyRequest,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policies" / "nightjar.rego"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _opa_binary() -> str | None:
    configured = os.environ.get("NIGHTJAR_OPA_BIN")
    candidates = [configured, shutil.which("opa"), str(ROOT / ".tools" / "bin" / "opa")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def opa_url() -> Iterator[str]:
    binary = _opa_binary()
    if binary is None:
        pytest.skip("OPA binary not available; set NIGHTJAR_OPA_BIN to run parity tests")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(  # noqa: S603 - fixed arguments, local binary
        [binary, "run", "--server", f"--addr=127.0.0.1:{port}", str(POLICY)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"OPA exited with {process.returncode}")
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("OPA did not become healthy")
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)


def context(**overrides: object) -> PolicyContext:
    base = PolicyContext(
        tenant_id="tenant-1",
        incident_id="incident-1",
        incident_class=IncidentClass.SITE_OUTAGE,
        severity=Severity.HIGH,
        confidence=0.9,
        evidence_count=3,
        blast_radius=1,
    )
    return base.model_copy(update=overrides)


CASES = [
    (
        ActionSpec(
            action_type=ActionType.PROBE,
            target="https://example.com",
            risk=RiskClass.R0,
        ),
        context(confidence=0.0, evidence_count=0),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(evidence_count=0),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(confidence=0.4),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(change_freeze=True),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(confidence=0.6),
    ),
    (
        ActionSpec(
            action_type=ActionType.RESTART_SERVICE,
            target="web",
            params={"service": "web"},
            risk=RiskClass.R2,
        ),
        context(),
    ),
    (
        ActionSpec(
            action_type=ActionType.RESTART_SERVICE,
            target="web",
            params={"service": "web"},
            risk=RiskClass.R2,
        ),
        context(approvals_granted=1),
    ),
    # --- managed targets: the same action, one managed zone and one not.
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-1",
            params={"zone": "zone-1", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(
            managed_targets={"cloudflare": {"zones": ["zone-1"]}},
            target_provider="cloudflare",
            target_kind="zones",
            target_value="zone-1",
        ),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-2",
            params={"zone": "zone-2", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(
            managed_targets={"cloudflare": {"zones": ["zone-1"]}},
            target_provider="cloudflare",
            target_kind="zones",
            target_value="zone-2",
        ),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-2",
            params={"zone": "zone-2", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(
            managed_targets={"cloudflare": {"zones": ["*"]}},
            target_provider="cloudflare",
            target_kind="zones",
            target_value="zone-2",
        ),
    ),
    (
        ActionSpec(
            action_type=ActionType.REVOKE_CREDENTIAL,
            target="github",
            params={"credential_id": "cred-1", "credential_ref": "env:EXPOSED_PAT"},
            risk=RiskClass.R2,
        ),
        context(
            managed_targets={"github": {}},
            target_provider="github",
            target_kind=None,
            target_value=None,
            approvals_granted=1,
        ),
    ),
    (
        ActionSpec(
            action_type=ActionType.PURGE_CACHE,
            target="zone-2",
            params={"zone": "zone-2", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
        ),
        context(
            evidence_count=0,
            managed_targets={"cloudflare": {"zones": ["zone-1"]}},
            target_provider="cloudflare",
            target_kind="zones",
            target_value="zone-2",
        ),
    ),
]


@pytest.mark.parametrize(("action", "ctx"), CASES)
async def test_local_and_rego_agree(opa_url: str, action: ActionSpec, ctx: PolicyContext) -> None:
    request = PolicyRequest(action=action, context=ctx)
    local = await LocalPolicyEngine().decide(request)
    remote = await OpaPolicyEngine(opa_url).decide(request)

    assert local.decision == remote.decision
    assert local.reasons == remote.reasons
    assert local.matched_rules == remote.matched_rules
    assert local.required_approvals == remote.required_approvals


def test_rego_denies_irreversible_action_without_break_glass(opa_url: str) -> None:
    payload = {
        "input": {
            "action": {
                "action_type": "restart_service",
                "risk": "r3",
                "target": "web",
            },
            "context": {
                "confidence": 0.99,
                "evidence_count": 5,
                "blast_radius": 1,
                "change_freeze": False,
                "approvals_granted": 0,
                "break_glass": False,
            },
        }
    }
    response = httpx.post(f"{opa_url}/v1/data/nightjar/result", json=payload, timeout=5)
    result = response.json()["result"]
    assert result["decision"] == "deny"
    assert result["required_approvals"] == 2

    payload["input"]["context"]["break_glass"] = True  # type: ignore[index]
    payload["input"]["context"]["approvals_granted"] = 2  # type: ignore[index]
    response = httpx.post(f"{opa_url}/v1/data/nightjar/result", json=payload, timeout=5)
    result = response.json()["result"]
    assert result["decision"] == "allow"
    assert "break_glass" in result["matched_rules"]
