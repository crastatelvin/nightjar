from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nightjar.core.enums import ActionType
from nightjar.core.models import ActionSpec

# Where an action lands when the plan does not name a connector: the provider
# that owns the action, and the allowlist key the destination must appear under.
# ``None`` means the action has no destination of its own — revoking a token you
# already hold, notifying a configured webhook, probing a URL — so only the
# provider has to be managed.
ACTION_TARGET: dict[ActionType, tuple[str, str | None]] = {
    ActionType.PURGE_CACHE: ("cloudflare", "zones"),
    ActionType.ENABLE_UNDER_ATTACK: ("cloudflare", "zones"),
    ActionType.ROLLBACK_DEPLOYMENT: ("cloudflare", "projects"),
    ActionType.RESTART_SERVICE: ("fly", "apps"),
    ActionType.QUARANTINE_MESSAGE: ("google", "mailboxes"),
    ActionType.BLOCK_SENDER: ("google", "mailboxes"),
    ActionType.OPEN_ISSUE: ("github", "repos"),
    ActionType.REVOKE_CREDENTIAL: ("github", None),
    ActionType.NOTIFY: ("webhook", None),
    ActionType.PROBE: ("http", None),
}

# Parameter names a planner may use for the destination, most specific first.
_KIND_KEYS: dict[str, tuple[str, ...]] = {
    "zones": ("zone_id", "zone"),
    "repos": ("repository", "repo"),
    "mailboxes": ("mailbox", "user", "upn"),
    "projects": ("project_id", "project"),
    "apps": ("app_id", "app"),
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
    mapping = ACTION_TARGET.get(action.action_type)
    if mapping is None:
        return None
    default_provider, kind = mapping
    provider = str(action.params.get("connector") or default_provider)
    if kind is None:
        return ResolvedTarget(provider=provider, kind=None, value=None)
    value = ""
    for key in _KIND_KEYS.get(kind, ()):
        candidate = action.params.get(key)
        if isinstance(candidate, str) and candidate.strip():
            value = candidate
            break
    if not value:
        value = action.target
    return ResolvedTarget(
        provider=provider,
        kind=kind,
        value=normalize_target_value(kind, value),
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
