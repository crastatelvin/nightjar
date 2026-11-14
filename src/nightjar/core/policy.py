from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from nightjar.core.actions import ActionValidationError, risk_for, validate_action
from nightjar.core.enums import Decision, IncidentClass, RiskClass, Severity
from nightjar.core.models import ActionSpec, PolicyResult
from nightjar.core.targets import (
    TARGET_DENIED_REASON,
    TARGET_DENIED_RULE,
    ManagedTargets,
    ResolvedTarget,
    target_allowed,
)

log = logging.getLogger(__name__)


class PolicyContext(BaseModel):
    tenant_id: str
    incident_id: str
    incident_class: IncidentClass
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    blast_radius: int = Field(default=1, ge=0)
    change_freeze: bool = False
    approvals_granted: int = Field(default=0, ge=0)
    break_glass: bool = False
    # An empty allowlist means unmanaged development mode. Production refuses
    # to start that way, so a real deployment always takes the strict branch.
    managed_targets: ManagedTargets = Field(default_factory=dict)
    target_provider: str | None = None
    target_kind: str | None = None
    target_value: str | None = None

    def resolved_target(self) -> ResolvedTarget | None:
        if self.target_provider is None:
            return None
        return ResolvedTarget(
            provider=self.target_provider,
            kind=self.target_kind,
            value=self.target_value,
        )


class PolicyRequest(BaseModel):
    action: ActionSpec
    context: PolicyContext


class PolicyEngine(Protocol):
    async def decide(self, request: PolicyRequest) -> PolicyResult: ...


def _result(
    decision: Decision,
    action: ActionSpec,
    reasons: list[str],
    rules: list[str],
    required: int,
    engine: str,
) -> PolicyResult:
    return PolicyResult(
        decision=decision,
        risk=action.risk,
        reasons=reasons,
        matched_rules=rules,
        required_approvals=required,
        engine=engine,
    )


def _required_approvals(action: ActionSpec, context: PolicyContext) -> int:
    """Mirror the RegO ``required_approvals`` result for the same input."""
    if action.risk is RiskClass.R3:
        return 2
    if action.risk is RiskClass.R2:
        return 1
    if action.risk is RiskClass.R1 and not (
        context.confidence >= 0.7 and context.blast_radius <= 1
    ):
        return 1
    return 0


class LocalPolicyEngine:
    """Deterministic, fail-closed policy used in tests and single-process dev.

    The Rego policy in policies/nightjar.rego is the reference implementation.
    `tests/test_policy_parity.py` runs the same cases through both.
    """

    async def decide(self, request: PolicyRequest) -> PolicyResult:
        action = request.action
        ctx = request.context
        reasons: list[str] = []
        rules: list[str] = []

        try:
            validate_action(action)
        except ActionValidationError as exc:
            return _result(
                Decision.DENY, action, [str(exc)], ["invalid_action", "fail_closed"], 0, "local"
            )

        if action.risk is RiskClass.R0:
            return _result(Decision.ALLOW, action, [], ["read_only_allow"], 0, "local")

        if not target_allowed(ctx.resolved_target(), ctx.managed_targets):
            reasons.append(TARGET_DENIED_REASON)
            rules.append(TARGET_DENIED_RULE)

        if ctx.evidence_count == 0:
            reasons.append("no evidence has been collected")
            rules.append("evidence_required")
        if ctx.confidence < 0.5:
            reasons.append("confidence below 0.50")
            rules.append("confidence_floor")
        if ctx.change_freeze:
            reasons.append("change freeze is active")
            rules.append("change_freeze")

        if reasons:
            return _result(
                Decision.DENY,
                action,
                sorted(reasons),
                sorted(rules),
                _required_approvals(action, ctx),
                "local",
            )

        if action.risk is RiskClass.R1:
            if ctx.confidence >= 0.7 and ctx.blast_radius <= 1:
                return _result(Decision.ALLOW, action, [], ["low_risk_auto"], 0, "local")
            return _result(
                Decision.REQUIRE_APPROVAL,
                action,
                ["low-confidence or wide blast radius"],
                ["low_risk_needs_review"],
                1,
                "local",
            )

        if action.risk is RiskClass.R2:
            if ctx.approvals_granted >= 1:
                return _result(Decision.ALLOW, action, [], [], 1, "local")
            return _result(
                Decision.REQUIRE_APPROVAL,
                action,
                ["action may affect customers"],
                ["customer_impact_requires_approval"],
                1,
                "local",
            )

        if action.risk is RiskClass.R3:
            if ctx.break_glass and ctx.approvals_granted >= 2:
                return _result(Decision.ALLOW, action, [], ["break_glass"], 2, "local")
            return _result(
                Decision.DENY,
                action,
                ["irreversible action requires break-glass and two approvers"],
                [],
                2,
                "local",
            )

        return _result(Decision.DENY, action, ["unhandled risk class"], ["fail_closed"], 0, "local")


class OpaPolicyEngine:
    """HTTP client for an OPA server running policies/nightjar.rego."""

    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/data/nightjar/result"
        self._timeout = timeout

    async def decide(self, request: PolicyRequest) -> PolicyResult:
        validate_action(request.action)
        payload = {
            "input": {
                "action": {
                    "action_type": request.action.action_type.value,
                    "risk": request.action.risk.value,
                    "target": request.action.target,
                },
                "context": request.context.model_dump(mode="json"),
            }
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("policy engine unavailable, failing closed: %s", exc)
            return _result(
                Decision.DENY,
                request.action,
                [f"policy engine unavailable: {exc}"],
                ["fail_closed"],
                0,
                "opa",
            )

        result = body.get("result") or {}
        decision = result.get("decision", "deny")
        try:
            parsed = Decision(decision)
        except ValueError:
            parsed = Decision.DENY
            result.setdefault("reasons", []).append("unknown decision from policy engine")
        return _result(
            parsed,
            request.action,
            list(result.get("reasons", [])),
            list(result.get("matched_rules", [])),
            int(result.get("required_approvals", 0)),
            "opa",
        )


def default_risk(action: ActionSpec) -> RiskClass:
    return risk_for(action.action_type)
