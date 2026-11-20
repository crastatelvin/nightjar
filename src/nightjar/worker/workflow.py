"""Durable incident workflow.

The workflow is the orchestration state machine. Activities do the work, signals
resume it when an operator approves a plan, and a timer enforces the stability
window. The inline orchestrator remains the default; this exists for operators
who want Temporal's durability and replay guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from nightjar.worker.activities import (
        execute_and_verify,
        expire_approvals,
        investigate_and_plan,
        verify_manual,
        verify_stability,
    )

ONE_ATTEMPT = RetryPolicy(maximum_attempts=1)
INVESTIGATION_TIMEOUT = timedelta(minutes=6)
EXECUTION_TIMEOUT = timedelta(minutes=10)
VERIFICATION_TIMEOUT = timedelta(minutes=5)


@dataclass
class IncidentWorkflowInput:
    incident_id: str
    approval_ttl_seconds: int
    stability_seconds: int


@dataclass
class IncidentWorkflowResult:
    status: str


@workflow.defn
class IncidentWorkflow:
    def __init__(self) -> None:
        self._approval_received = False
        self._approved = False
        self._manual_step_attested = False

    @workflow.signal
    def approval_decided(self, approved: bool) -> None:
        self._approval_received = True
        self._approved = bool(approved)

    @workflow.signal
    def manual_step_attested(self) -> None:
        self._manual_step_attested = True

    @workflow.query
    def approval_state(self) -> dict[str, bool]:
        return {
            "received": self._approval_received,
            "approved": self._approved,
        }

    @workflow.query
    def manual_state(self) -> dict[str, bool]:
        return {"attested": self._manual_step_attested}

    @workflow.run
    async def run(self, payload: IncidentWorkflowInput) -> IncidentWorkflowResult:
        status = await workflow.execute_activity(
            investigate_and_plan,
            payload.incident_id,
            start_to_close_timeout=INVESTIGATION_TIMEOUT,
            retry_policy=ONE_ATTEMPT,
        )

        if status == "awaiting_approval":
            try:
                await workflow.wait_condition(
                    lambda: self._approval_received,
                    timeout=payload.approval_ttl_seconds,
                )
            except TimeoutError:
                status = await workflow.execute_activity(
                    expire_approvals,
                    payload.incident_id,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ONE_ATTEMPT,
                )
                return IncidentWorkflowResult(status=status)
            if not self._approved:
                return IncidentWorkflowResult(status="rejected")
            status = await workflow.execute_activity(
                execute_and_verify,
                payload.incident_id,
                start_to_close_timeout=EXECUTION_TIMEOUT,
                retry_policy=ONE_ATTEMPT,
            )

        # A plan can pause on steps no provider API can perform. The workflow does
        # not exit: it parks here until an operator attests, then asks the service
        # to continue, which may mean waiting again for the next step.
        while status == "awaiting_manual":
            await workflow.wait_condition(lambda: self._manual_step_attested)
            self._manual_step_attested = False
            status = await workflow.execute_activity(
                verify_manual,
                payload.incident_id,
                start_to_close_timeout=VERIFICATION_TIMEOUT,
                retry_policy=ONE_ATTEMPT,
            )

        if status not in {"executing", "verifying"}:
            return IncidentWorkflowResult(status=status)

        await workflow.sleep(payload.stability_seconds)
        status = await workflow.execute_activity(
            verify_stability,
            payload.incident_id,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ONE_ATTEMPT,
        )
        return IncidentWorkflowResult(status=status)


__all__ = [
    "IncidentWorkflow",
    "IncidentWorkflowInput",
    "IncidentWorkflowResult",
]
