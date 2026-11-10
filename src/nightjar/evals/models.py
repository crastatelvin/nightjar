"""Eval domain types.

These types deliberately describe what the harness observes rather than what
the service stores, so a scoring run can be replayed from a report and so the
same scorer can be unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ExpectedOutcome = Literal["resolved", "reopened", "failed"]


@dataclass(frozen=True)
class EvalCase:
    """One deterministic fault injection and the behaviour it demands."""

    id: str
    suite: str
    scenario: str
    variant: int
    expected_outcome: ExpectedOutcome
    expect_approval: bool
    expected_actions: tuple[str, ...]
    relapse: bool = False
    injection: bool = False
    stability_seconds: int = 0

    @property
    def requires_stability(self) -> bool:
        return self.stability_seconds > 0


@dataclass
class CaseObservation:
    """The observable end state of one case."""

    status: str
    audit_healthy: bool
    audit_checks: dict[str, bool]
    plan: dict[str, Any] | None
    actions: list[dict[str, Any]]
    action_states: dict[str, str]
    approvals: list[dict[str, Any]]
    verifications: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]]
    chain: dict[str, Any]
    lab_reset: bool = True
    timed_out: bool = False
    approvals_granted: int = 0
    error: str | None = None


@dataclass
class CaseScore:
    """Scored outcome for one case."""

    case_id: str
    suite: str
    scenario: str
    variant: int
    expected_outcome: ExpectedOutcome
    status: str
    outcome_correct: bool
    false_resolution: bool
    verified: bool
    expected_actions: tuple[str, ...]
    executed_actions: tuple[str, ...]
    action_match: bool
    approvals_expected: bool
    approvals_granted: int
    approvals_ok: bool
    injection: bool
    injection_present: bool
    injection_resistant: bool
    policy_violations: list[str] = field(default_factory=list)
    chain_valid: bool = False
    lab_healthy: bool = False
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    evidence_count: int = 0
    plan_confidence: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSummary:
    """Aggregate metrics for one evaluation run."""

    schema_version: int
    suite: str
    nightjar_version: str
    provider: str
    model: str
    cases: int = 0
    outcome_correct: int = 0
    false_resolutions: int = 0
    resolved_cases: int = 0
    expected_resolved: int = 0
    reopened_cases: int = 0
    verified_cases: int = 0
    injection_cases: int = 0
    injection_resistant: int = 0
    policy_violations: int = 0
    policy_violating_cases: int = 0
    chain_valid_cases: int = 0
    total_cost_usd: float = 0.0
    mean_cost_usd: float = 0.0
    mean_latency_seconds: float = 0.0
    p95_latency_seconds: float = 0.0
    mean_input_tokens: float = 0.0
    mean_output_tokens: float = 0.0
    mean_evidence_count: float = 0.0
    accuracy: float = 0.0
    false_resolution_rate: float = 0.0
    verification_rate: float = 0.0
    injection_resistance_rate: float = 0.0
    chain_integrity_rate: float = 0.0
    by_suite: dict[str, dict[str, int]] = field(default_factory=dict)
    cases: list[CaseScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cases"] = [case.to_dict() for case in self.cases]
        return data


__all__ = [
    "CaseObservation",
    "CaseScore",
    "EvalCase",
    "EvalSummary",
    "ExpectedOutcome",
]
