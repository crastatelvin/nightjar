from __future__ import annotations

import pytest

from nightjar.core.enums import (
    ActionType,
    Decision,
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

CONTEXT = PolicyContext(
    tenant_id="tenant-1",
    incident_id="incident-1",
    incident_class=IncidentClass.SITE_OUTAGE,
    severity=Severity.HIGH,
    confidence=0.9,
    evidence_count=4,
    blast_radius=1,
)


def action(action_type: ActionType, risk: RiskClass, **params: object) -> ActionSpec:
    defaults: dict[ActionType, dict[str, object]] = {
        ActionType.PROBE: {},
        ActionType.PURGE_CACHE: {"zone": "z", "urls": ["https://example.com"]},
        ActionType.REVOKE_CREDENTIAL: {"credential_id": "cred-1"},
        ActionType.ROLLBACK_DEPLOYMENT: {"service": "web", "target_version": "1.0.0"},
        ActionType.RESTART_SERVICE: {"service": "web"},
        ActionType.QUARANTINE_MESSAGE: {"message_id": "msg-1"},
        ActionType.BLOCK_SENDER: {"sender": "bad@example.com"},
    }
    return ActionSpec(
        action_type=action_type,
        target="target-1",
        params={**defaults.get(action_type, {}), **params},
        risk=risk,
    )


async def decide(action_spec: ActionSpec, **overrides: object):
    context = CONTEXT.model_copy(update=overrides)
    return await LocalPolicyEngine().decide(PolicyRequest(action=action_spec, context=context))


async def test_read_only_action_is_allowed_without_evidence() -> None:
    result = await decide(action(ActionType.PROBE, RiskClass.R0), evidence_count=0, confidence=0.0)
    assert result.decision is Decision.ALLOW
    assert result.matched_rules == ["read_only_allow"]
    assert result.required_approvals == 0


async def test_no_evidence_is_denied() -> None:
    result = await decide(action(ActionType.PURGE_CACHE, RiskClass.R1), evidence_count=0)
    assert result.decision is Decision.DENY
    assert result.reasons == ["no evidence has been collected"]
    assert result.matched_rules == ["evidence_required"]


async def test_low_confidence_is_denied() -> None:
    result = await decide(action(ActionType.RESTART_SERVICE, RiskClass.R2), confidence=0.49)
    assert result.decision is Decision.DENY
    assert "confidence below 0.50" in result.reasons
    assert "confidence_floor" in result.matched_rules


async def test_change_freeze_denies_every_mutating_action() -> None:
    result = await decide(action(ActionType.ROLLBACK_DEPLOYMENT, RiskClass.R2), change_freeze=True)
    assert result.decision is Decision.DENY
    assert result.reasons == ["change freeze is active"]
    assert result.matched_rules == ["change_freeze"]


async def test_deny_reasons_are_sorted_like_the_rego_policy() -> None:
    result = await decide(
        action(ActionType.REVOKE_CREDENTIAL, RiskClass.R2),
        evidence_count=0,
        confidence=0.1,
        change_freeze=True,
    )
    assert result.reasons == [
        "change freeze is active",
        "confidence below 0.50",
        "no evidence has been collected",
    ]
    assert result.matched_rules == [
        "change_freeze",
        "confidence_floor",
        "evidence_required",
    ]


async def test_reversible_low_risk_action_is_auto_allowed() -> None:
    result = await decide(action(ActionType.PURGE_CACHE, RiskClass.R1))
    assert result.decision is Decision.ALLOW
    assert result.matched_rules == ["low_risk_auto"]
    assert result.reasons == []


async def test_low_risk_with_low_confidence_needs_approval() -> None:
    result = await decide(action(ActionType.PURGE_CACHE, RiskClass.R1), confidence=0.6)
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert result.matched_rules == ["low_risk_needs_review"]
    assert result.required_approvals == 1


async def test_low_risk_with_wide_blast_radius_needs_approval() -> None:
    result = await decide(action(ActionType.PURGE_CACHE, RiskClass.R1), blast_radius=4)
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert result.matched_rules == ["low_risk_needs_review"]


async def test_customer_impact_requires_approval() -> None:
    result = await decide(action(ActionType.RESTART_SERVICE, RiskClass.R2))
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert result.reasons == ["action may affect customers"]
    assert result.required_approvals == 1


async def test_customer_impact_with_existing_approval_is_allowed() -> None:
    result = await decide(action(ActionType.RESTART_SERVICE, RiskClass.R2), approvals_granted=1)
    assert result.decision is Decision.ALLOW
    assert result.matched_rules == []
    assert result.required_approvals == 1


async def test_irreversible_action_is_denied_without_break_glass(monkeypatch) -> None:
    # v1 has no irreversible action in the catalog; exercise the R3 branch directly.
    monkeypatch.setattr("nightjar.core.policy.validate_action", lambda action: None)
    result = await decide(action(ActionType.RESTART_SERVICE, RiskClass.R3))
    assert result.decision is Decision.DENY
    assert result.reasons == ["irreversible action requires break-glass and two approvers"]
    assert result.required_approvals == 2


async def test_irreversible_action_needs_two_approvers_even_with_break_glass(
    monkeypatch,
) -> None:
    monkeypatch.setattr("nightjar.core.policy.validate_action", lambda action: None)
    only_one = await decide(
        action(ActionType.RESTART_SERVICE, RiskClass.R3),
        break_glass=True,
        approvals_granted=1,
    )
    assert only_one.decision is Decision.DENY

    allowed = await decide(
        action(ActionType.RESTART_SERVICE, RiskClass.R3),
        break_glass=True,
        approvals_granted=2,
    )
    assert allowed.decision is Decision.ALLOW
    assert allowed.matched_rules == ["break_glass"]


async def test_invalid_action_fails_closed() -> None:
    invalid = action(ActionType.PURGE_CACHE, RiskClass.R0)
    result = await LocalPolicyEngine().decide(PolicyRequest(action=invalid, context=CONTEXT))
    assert result.decision is Decision.DENY
    assert "fail_closed" in result.matched_rules


async def test_unreachable_opa_fails_closed() -> None:
    engine = OpaPolicyEngine("http://127.0.0.1:9", timeout=0.5)
    result = await engine.decide(
        PolicyRequest(action=action(ActionType.PURGE_CACHE, RiskClass.R1), context=CONTEXT)
    )
    assert result.decision is Decision.DENY
    assert result.engine == "opa"
    assert "fail_closed" in result.matched_rules


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_policy_context_rejects_out_of_range_confidence(confidence: float) -> None:
    payload = {**CONTEXT.model_dump(), "confidence": confidence}
    with pytest.raises(ValueError):
        PolicyContext(**payload)
