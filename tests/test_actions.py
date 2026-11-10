from __future__ import annotations

import pytest

from nightjar.core.actions import (
    ACTION_RISK,
    PROHIBITED_ACTION_NAMES,
    ActionValidationError,
    risk_for,
    validate_action,
    validate_plan,
)
from nightjar.core.enums import ActionType, PredicateKind, RiskClass
from nightjar.core.models import ActionPlan, ActionSpec, VerificationPredicate


def predicate() -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.HTTP,
        description="site is up",
        params={"url": "https://example.com", "expected_status": 200},
    )


def purge(predicate_id: str | None = None) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target="cornershop",
        params={"zone": "cornershop", "urls": ["https://example.com"]},
        risk=RiskClass.R1,
        predicate_ids=[predicate_id] if predicate_id else [],
    )


def test_valid_action_passes() -> None:
    validate_action(purge())


def test_registered_risk_cannot_be_downgraded() -> None:
    action = purge().model_copy(update={"risk": RiskClass.R0})
    with pytest.raises(ActionValidationError, match="must be risk r1"):
        validate_action(action)


def test_required_parameters_are_enforced() -> None:
    action = ActionSpec(
        action_type=ActionType.PURGE_CACHE,
        target="cornershop",
        params={"zone": "cornershop"},
        risk=RiskClass.R1,
    )
    with pytest.raises(ActionValidationError, match="requires parameter 'urls'"):
        validate_action(action)


def test_empty_target_is_rejected() -> None:
    action = purge().model_copy(update={"target": "   "})
    with pytest.raises(ActionValidationError, match="target is empty"):
        validate_action(action)


def test_every_action_type_has_a_risk_mapping() -> None:
    assert set(ACTION_RISK) == set(ActionType)
    for action_type, risk in ACTION_RISK.items():
        assert risk_for(action_type) is risk


def test_irreversible_names_cannot_be_represented() -> None:
    assert "delete_data" in PROHIBITED_ACTION_NAMES
    assert "transfer_money" in PROHIBITED_ACTION_NAMES
    assert "disable_logging" in PROHIBITED_ACTION_NAMES
    assert not any(name in {item.value for item in ActionType} for name in PROHIBITED_ACTION_NAMES)


def test_plan_must_have_actions() -> None:
    plan = ActionPlan(summary="empty", root_cause="unknown", predicates=[predicate()])
    with pytest.raises(ActionValidationError, match="no actions"):
        validate_plan(plan)


def test_plan_must_have_predicates() -> None:
    plan = ActionPlan(summary="blind", root_cause="unknown", actions=[purge()])
    with pytest.raises(ActionValidationError, match="no verification predicates"):
        validate_plan(plan)


def test_plan_rejects_unknown_predicate_references() -> None:
    plan = ActionPlan(
        summary="dangling",
        root_cause="cache",
        actions=[purge("predicate-that-does-not-exist")],
        predicates=[predicate()],
    )
    with pytest.raises(ActionValidationError, match="unknown predicates"):
        validate_plan(plan)


def test_valid_plan_passes() -> None:
    check = predicate()
    plan = ActionPlan(
        summary="purge cache",
        root_cause="poisoned cache",
        actions=[purge(check.id)],
        predicates=[check],
    )
    validate_plan(plan)


def test_action_spec_forbids_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ActionSpec(  # noqa: S604 - proves extra fields are rejected
            action_type=ActionType.PURGE_CACHE,
            target="cornershop",
            params={"zone": "cornershop", "urls": ["https://example.com"]},
            risk=RiskClass.R1,
            shell="rm -rf /",  # type: ignore[call-arg]
        )
