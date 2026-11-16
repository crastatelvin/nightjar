from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec


@dataclass(frozen=True)
class TargetSpec:
    provider: str
    kind: str | None
    keys: tuple[str, ...] = ()
    fallback_to_target: bool = True


# Where an action lands when the plan does not name a connector: the provider
# that owns the action, the allowlist key the destination must appear under, and
# the parameter names a planner may use for it. ``kind=None`` means the action
# has no destination of its own — revoking a token you already hold, notifying a
# configured webhook, probing a URL — so only the provider has to be managed.
#
# Mail actions never fall back to ``action.target``: that is the message id or
# the sender, not the mailbox that must be managed.
ACTION_TARGET: dict[ActionType, TargetSpec] = {
    ActionType.PURGE_CACHE: TargetSpec("cloudflare", "zones", ("zone_id", "zone")),
    ActionType.ENABLE_UNDER_ATTACK: TargetSpec("cloudflare", "zones", ("zone_id", "zone")),
    ActionType.ROLLBACK_DEPLOYMENT: TargetSpec(
        "cloudflare", "projects", ("project_id", "project", "service", "service_id")
    ),
    ActionType.RESTART_SERVICE: TargetSpec(
        "fly", "apps", ("app_id", "app", "service", "service_id")
    ),
    ActionType.QUARANTINE_MESSAGE: TargetSpec(
        "google", "mailboxes", ("mailbox",), fallback_to_target=False
    ),
    ActionType.BLOCK_SENDER: TargetSpec(
        "google", "mailboxes", ("mailbox",), fallback_to_target=False
    ),
    ActionType.OPEN_ISSUE: TargetSpec("github", "repos", ("repository", "repo")),
    ActionType.REVOKE_CREDENTIAL: TargetSpec("github", None),
    ActionType.NOTIFY: TargetSpec("webhook", None),
    ActionType.PROBE: TargetSpec("http", None),
}

# The same action names a different destination at different providers. Render
# calls it a service, Fly calls it an app, Cloudflare Pages and Vercel call it a
# project. The allowlist must use the provider's own vocabulary or an operator
# cannot tell what a rule protects.
_TARGET_OVERRIDES: dict[tuple[str, ActionType], TargetSpec] = {
    ("render", ActionType.ROLLBACK_DEPLOYMENT): TargetSpec(
        "render", "services", ("service_id", "service")
    ),
    ("render", ActionType.RESTART_SERVICE): TargetSpec(
        "render", "services", ("service_id", "service")
    ),
    ("fly", ActionType.RESTART_SERVICE): TargetSpec(
        "fly", "apps", ("app_id", "app", "service_id", "service")
    ),
    ("microsoft", ActionType.QUARANTINE_MESSAGE): TargetSpec(
        "microsoft", "mailboxes", ("mailbox",), fallback_to_target=False
    ),
    ("microsoft", ActionType.BLOCK_SENDER): TargetSpec(
        "microsoft", "mailboxes", ("mailbox",), fallback_to_target=False
    ),
    ("microsoft", ActionType.REVOKE_CREDENTIAL): TargetSpec(
        "microsoft",
        "mailboxes",
        ("mailbox", "upn", "user", "user_id"),
        fallback_to_target=False,
    ),
}

# Repository and mailbox identifiers are case-insensitive at every provider.
_CASE_INSENSITIVE = frozenset({"repos", "mailboxes"})

TARGET_DENIED_REASON = "target is not in NIGHTJAR_MANAGED_TARGETS"
TARGET_DENIED_RULE = "target_not_managed"

ManagedTargets = dict[str, dict[str, list[str]]]


@dataclass(frozen=True)
class ResolvedTarget:
    provider: str
    kind: str | None
    value: str | None


def normalize_target_value(kind: str | None, value: str) -> str:
    cleaned = value.strip()
    return cleaned.lower() if kind in _CASE_INSENSITIVE else cleaned


def normalize_managed_targets(raw: Mapping[str, Any]) -> ManagedTargets:
    """Validate and canonicalise ``NIGHTJAR_MANAGED_TARGETS``.

    Values are compared, never used to build a URL or a request. Normalising
    both sides here keeps the local engine and the Rego policy to a plain
    equality check.
    """
    normalized: ManagedTargets = {}
    for provider, kinds in raw.items():
        if not isinstance(kinds, Mapping):
            raise ValueError(f"managed target provider {provider!r} must map to a JSON object")
        entry: dict[str, list[str]] = {}
        for kind, values in kinds.items():
            items = [values] if isinstance(values, str) else values
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise ValueError(f"managed target {provider}.{kind} must be a list of strings")
            cleaned = [
                normalize_target_value(str(kind), str(item)) for item in items if str(item).strip()
            ]
            entry[str(kind)] = cleaned
        normalized[str(provider)] = entry
    return normalized


def resolve_target(action: ActionSpec) -> ResolvedTarget | None:
    """Work out which real destination an action would touch."""
    spec = ACTION_TARGET.get(action.action_type)
    if spec is None:
        return None
    provider = str(action.params.get("connector") or spec.provider)
    spec = _TARGET_OVERRIDES.get((provider, action.action_type), spec)
    if spec.kind is None:
        return ResolvedTarget(provider=provider, kind=None, value=None)
    value = ""
    for key in spec.keys:
        candidate = action.params.get(key)
        if isinstance(candidate, str) and candidate.strip():
            value = candidate
            break
    if not value and spec.fallback_to_target:
        value = action.target
    return ResolvedTarget(
        provider=provider,
        kind=spec.kind,
        value=normalize_target_value(spec.kind, value),
    )


def target_allowed(
    target: ResolvedTarget | None, managed: Mapping[str, Mapping[str, list[str]]]
) -> bool:
    """Fail closed unless the destination is explicitly managed.

    An empty allowlist means unmanaged development mode: every action is
    allowed. Production refuses to start with an empty allowlist, so the
    permissive branch cannot be reached by a real deployment.
    """
    if not managed:
        return True
    if target is None:
        return True
    provider = managed.get(target.provider)
    if provider is None:
        return False
    if target.kind is None:
        return True
    allowed = provider.get(target.kind) or []
    if not allowed or not target.value:
        return False
    return "*" in allowed or target.value in allowed
