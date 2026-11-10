from __future__ import annotations

from nightjar.core.enums import ActionType, RiskClass
from nightjar.core.models import ActionPlan, ActionSpec, VerificationPredicate

# The v1 action catalogue. Anything not listed here is rejected before policy.
ACTION_RISK: dict[ActionType, RiskClass] = {
    ActionType.PROBE: RiskClass.R0,
    ActionType.OPEN_ISSUE: RiskClass.R1,
    ActionType.NOTIFY: RiskClass.R1,
    ActionType.PURGE_CACHE: RiskClass.R1,
    ActionType.QUARANTINE_MESSAGE: RiskClass.R1,
    ActionType.ENABLE_UNDER_ATTACK: RiskClass.R2,
    ActionType.REVOKE_CREDENTIAL: RiskClass.R2,
    ActionType.BLOCK_SENDER: RiskClass.R2,
    ActionType.ROLLBACK_DEPLOYMENT: RiskClass.R2,
    ActionType.RESTART_SERVICE: RiskClass.R2,
}

# These are intentionally not representable as ActionType in v1. They exist as
# strings only so a future connector cannot quietly introduce them.
PROHIBITED_ACTION_NAMES: frozenset[str] = frozenset(
    {
        "delete_data",
        "transfer_money",
        "disable_logging",
        "contact_third_party",
        "rotate_root_credential",
        "modify_billing",
        "create_admin_user",
    }
)

REQUIRED_PARAMS: dict[ActionType, tuple[str, ...]] = {
    ActionType.PURGE_CACHE: ("zone", "urls"),
    ActionType.ENABLE_UNDER_ATTACK: ("zone",),
    ActionType.REVOKE_CREDENTIAL: ("credential_id",),
    ActionType.QUARANTINE_MESSAGE: ("message_id",),
    ActionType.BLOCK_SENDER: ("sender",),
    ActionType.ROLLBACK_DEPLOYMENT: ("service", "target_version"),
    ActionType.RESTART_SERVICE: ("service",),
    ActionType.OPEN_ISSUE: ("repository", "title"),
    ActionType.NOTIFY: ("channel", "message"),
}


class ActionValidationError(ValueError):
    pass


def risk_for(action_type: ActionType) -> RiskClass:
    try:
        return ACTION_RISK[action_type]
    except KeyError as exc:  # pragma: no cover - enum exhaustiveness guard
        raise ActionValidationError(f"unregistered action type: {action_type}") from exc


def validate_action(action: ActionSpec) -> None:
    if action.action_type in PROHIBITED_ACTION_NAMES:
        raise ActionValidationError(f"prohibited action: {action.action_type}")
    if not action.target.strip():
        raise ActionValidationError("action target is empty")
    for key in REQUIRED_PARAMS.get(action.action_type, ()):
        if key not in action.params or action.params[key] in ("", None, []):
            raise ActionValidationError(f"{action.action_type} requires parameter {key!r}")
    if action.risk is not risk_for(action.action_type):
        raise ActionValidationError(
            f"{action.action_type} must be risk {risk_for(action.action_type)}, got {action.risk}"
        )


def validate_plan(plan: ActionPlan) -> None:
    if not plan.actions:
        raise ActionValidationError("plan has no actions")
    predicate_ids = {predicate.id for predicate in plan.predicates}
    if not plan.predicates:
        raise ActionValidationError("plan has no verification predicates")
    for action in plan.actions:
        validate_action(action)
        missing = set(action.predicate_ids) - predicate_ids
        if missing:
            raise ActionValidationError(f"action references unknown predicates: {sorted(missing)}")


def predicates_by_id(plan: ActionPlan) -> dict[str, VerificationPredicate]:
    return {predicate.id: predicate for predicate in plan.predicates}
