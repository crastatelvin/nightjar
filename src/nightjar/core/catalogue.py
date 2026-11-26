"""The generated capability table.

The README used to describe what each connector can do in prose, which meant a
new provider or a changed action could leave the page claiming something the
code no longer did. This module renders the table from the same
``Capability`` objects the planner is offered and ``/v1/meta/capabilities``
returns, including the read-back that decides whether an action worked.

The table is documentation, not a health report: it says what the deployment
*can* do, and ``nightjarctl doctor`` says whether it can do it right now.
"""

from __future__ import annotations

from collections.abc import Iterable

from nightjar.core.capabilities import ActionCapability
from nightjar.core.enums import ActionType

BEGIN = "<!-- capabilities:begin -->"
END = "<!-- capabilities:end -->"

# A provider name is an identifier; these are the names an operator recognizes.
PROVIDER_NAMES: dict[str, str] = {
    "cloudflare": "Cloudflare",
    "fly": "Fly.io",
    "github": "GitHub",
    "google": "Google Workspace",
    "lab": "Lab",
    "microsoft": "Microsoft 365",
    "render": "Render",
    "stripe": "Stripe",
    "vercel": "Vercel",
    "webhook": "Webhook",
}

ACTION_LABELS: dict[ActionType, str] = {
    ActionType.PROBE: "probe",
    ActionType.PURGE_CACHE: "purge cache",
    ActionType.ENABLE_UNDER_ATTACK: "enable under-attack",
    ActionType.REVOKE_CREDENTIAL: "revoke token",
    ActionType.QUARANTINE_MESSAGE: "quarantine message",
    ActionType.BLOCK_SENDER: "block sender",
    ActionType.ROLLBACK_DEPLOYMENT: "roll back deployment",
    ActionType.RESTART_SERVICE: "restart service",
    ActionType.OPEN_ISSUE: "open issue",
    ActionType.NOTIFY: "notify",
}


def _provider_name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider, provider.replace("_", " ").title())


def _action_label(action: ActionCapability) -> str:
    return ACTION_LABELS.get(action.action_type, action.action_type.value.replace("_", " "))


def render_table(actions: Iterable[ActionCapability]) -> str:
    """Render one row per provider action, with the proof that decides success.

    A capability without a proof is a rendering error rather than an empty
    cell: the table exists to say how Nightjar knows an action worked, and
    "we did not write it down" is not an answer an operator should have to
    discover after an incident.
    """
    ordered = sorted(
        actions,
        key=lambda item: (_provider_name(item.provider).lower(), item.action_type.value),
    )
    lines = [
        "| Provider | Action | Execution | Read-back that counts |",
        "| --- | --- | --- | --- |",
    ]
    for action in ordered:
        if not action.proof.strip():
            raise ValueError(
                f"{action.provider} advertises {action.action_type.value} without a proof"
            )
        mode = "API" if action.automated else "manual"
        lines.append(
            f"| **{_provider_name(action.provider)}** | "
            f"{_action_label(action)} | {mode} | {action.proof} |"
        )
    return "\n".join(lines)


def render_block(actions: Iterable[ActionCapability]) -> str:
    return f"{BEGIN}\n\n{render_table(actions)}\n\n{END}"


def extract_block(text: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"missing {BEGIN} … {END} markers")
    return text[start : end + len(END)]


def replace_block(text: str, block: str) -> str:
    existing = extract_block(text)
    return text.replace(existing, block, 1)


__all__ = [
    "ACTION_LABELS",
    "BEGIN",
    "END",
    "PROVIDER_NAMES",
    "extract_block",
    "render_block",
    "render_table",
    "replace_block",
]
