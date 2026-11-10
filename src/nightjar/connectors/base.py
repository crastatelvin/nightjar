from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from nightjar.core.enums import ActionType, EvidenceKind
from nightjar.core.models import ActionSpec, VerificationPredicate

CheckFn = Callable[[VerificationPredicate], Awaitable[dict[str, Any]]]


class ConnectorError(RuntimeError):
    """A typed operation failed. The executor records this and stops the plan."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


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
