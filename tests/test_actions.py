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
from nightjar.core.enums import ActionType, IncidentClass, PredicateKind, RiskClass
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


def _json(url: str) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.JSON,
        description="checkout completes",
        params={"url": url, "path": "status", "equals": "ok"},
    )


def _connector(name: str, **params: object) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description=name,
        params={"check": name, **params},
    )


def _revoke(predicate_ids: list[str]) -> ActionSpec:
    return ActionSpec(
        action_type=ActionType.REVOKE_CREDENTIAL,
        target="cred-1",
        params={"credential_id": "cred-1"},
        risk=RiskClass.R2,
        predicate_ids=predicate_ids,
    )


def test_site_outage_requires_user_visible_and_functional_proof() -> None:
    http = predicate()
    plan = ActionPlan(
        summary="purge",
        root_cause="cache",
        actions=[purge(http.id)],
        predicates=[http],
    )
    with pytest.raises(ActionValidationError, match="functional checkout check"):
        validate_plan(plan, incident_class=IncidentClass.SITE_OUTAGE)

    checkout = _json("https://example.com/checkout")
    complete = ActionPlan(
        summary="purge",
        root_cause="cache",
        actions=[purge(http.id)],
        predicates=[http, checkout],
    )
    complete.actions[0].predicate_ids.append(checkout.id)
    validate_plan(complete, incident_class=IncidentClass.SITE_OUTAGE)


def test_optional_predicates_do_not_satisfy_a_requirement() -> None:
    http = predicate()
    optional = _json("https://example.com/checkout").model_copy(update={"required": False})
    plan = ActionPlan(
        summary="purge",
        root_cause="cache",
        actions=[purge(http.id)],
        predicates=[http, optional],
    )
    with pytest.raises(ActionValidationError, match="functional checkout check"):
        validate_plan(plan, incident_class=IncidentClass.SITE_OUTAGE)


def test_credential_exposure_requires_a_predicate_about_the_credential() -> None:
    http = predicate()
    plan = ActionPlan(
        summary="revoke",
        root_cause="leak",
        actions=[_revoke([http.id])],
        predicates=[http],
    )
    with pytest.raises(ActionValidationError, match="presents the exposed credential"):
        validate_plan(plan, incident_class=IncidentClass.CREDENTIAL_EXPOSURE)

    check = _connector("github.credential_revoked", credential_ref="env:EXPOSED_PAT")
    plan.predicates.append(check)
    plan.actions[0].predicate_ids.append(check.id)
    validate_plan(plan, incident_class=IncidentClass.CREDENTIAL_EXPOSURE)


def test_phishing_requires_both_containment_and_sender_checks() -> None:
    quarantine = _connector("message_quarantined", message_id="m-1")
    plan = ActionPlan(
        summary="contain",
        root_cause="phish",
        actions=[
            ActionSpec(
                action_type=ActionType.QUARANTINE_MESSAGE,
                target="m-1",
                params={"message_id": "m-1"},
                risk=RiskClass.R1,
                predicate_ids=[quarantine.id],
            )
        ],
        predicates=[quarantine],
    )
    with pytest.raises(ActionValidationError, match="sender-filter"):
        validate_plan(plan, incident_class=IncidentClass.PHISHING)

    sender = _connector("sender_blocked", sender="bad@example.com")
    plan.predicates.append(sender)
    plan.actions[0].predicate_ids.append(sender.id)
    validate_plan(plan, incident_class=IncidentClass.PHISHING)


def test_unknown_classes_keep_only_structural_validation() -> None:
    check = predicate()
    plan = ActionPlan(
        summary="purge",
        root_cause="cache",
        actions=[purge(check.id)],
        predicates=[check],
    )
    validate_plan(plan, incident_class=IncidentClass.UNKNOWN)
