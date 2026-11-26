from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from nightjar.connectors.errors import ConnectorError
from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate

CheckFn = Callable[[VerificationPredicate], Awaitable[dict[str, Any]]]

Verdict = Literal["ok", "degraded", "unconfigured", "error"]


@dataclass(frozen=True)
class Capability:
    """One action a connector can really perform, and for which targets.

    ``automated=False`` means the provider has no API for this and the action is
    a manual step with a machine-checkable predicate. The planner is only ever
    offered capabilities that exist, so it cannot invent a fix.

    ``proof`` is the read-back that decides whether the action worked. It is not
    marketing copy: the verifier implements it, and the README renders it from
    here so the two cannot drift.
    """

    action_type: ActionType
    provider: str
    automated: bool = True
    target_kinds: tuple[str, ...] = ()
    notes: str = ""
    proof: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "provider": self.provider,
            "automated": self.automated,
            "target_kinds": list(self.target_kinds),
            "notes": self.notes,
            "proof": self.proof,
        }


@dataclass
class CapabilityReport:
    """The result of asking one provider "can you do your job right now?"."""

    provider: str
    verdict: Verdict
    identity: str = ""
    detail: str = ""
    missing_scopes: tuple[str, ...] = ()
    latency_ms: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def healthy(self) -> bool:
        return self.verdict == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "verdict": self.verdict,
            "identity": self.identity,
            "detail": self.detail,
            "missing_scopes": list(self.missing_scopes),
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class ManualStep:
    """A provider action a human must perform, with machine-checkable proof.

    Nightjar does not pretend to execute these. The service records the
    instructions, moves the incident to ``awaiting_manual``, and refuses to
    resolve until the verification predicate passes.
    """

    provider: str
    title: str
    instructions: tuple[str, ...]
    dashboard_url: str = ""
    fingerprint: str = ""
    probe_url: str = ""
    auth: str = "bearer"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "title": self.title,
            "instructions": list(self.instructions),
            "dashboard_url": self.dashboard_url,
            "fingerprint": self.fingerprint,
            "probe_url": self.probe_url,
            "auth": self.auth,
        }


@dataclass
class ToolResult:
    kind: EvidenceKind
    source: str
    summary: str
    content: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    name: str
    action_types: frozenset[ActionType]
    tool_names: frozenset[str]

    async def execute(self, action: ActionSpec) -> dict[str, Any]: ...

    async def collect(self, tool: str, params: dict[str, Any]) -> ToolResult: ...

    def verification_checks(self) -> dict[str, CheckFn]: ...

    def capabilities(self) -> tuple[Capability, ...]: ...

    async def preflight(self, *, deep: bool = False) -> CapabilityReport: ...


__all__ = [
    "Capability",
    "CapabilityReport",
    "CheckFn",
    "Connector",
    "ConnectorError",
    "ManualStep",
    "ToolResult",
    "Verdict",
]
