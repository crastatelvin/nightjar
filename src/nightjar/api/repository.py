"""Persistence for the Nightjar case file.

Every write that extends a hash chain takes a row lock on the parent incident,
so concurrent workers cannot fork a chain. Repository methods open short-lived
sessions; the service never holds a transaction across a network call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightjar.api.tables import (
    ActionRow,
    AgentRunRow,
    ApprovalRow,
    EvidenceRow,
    ExecutionRow,
    IncidentRow,
    NoteRow,
    PlanRow,
    TenantRow,
    TimelineRow,
    VerificationRow,
)
from nightjar.core.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    EventType,
    EvidenceKind,
    IncidentClass,
    IncidentSource,
    IncidentStatus,
    PredicateKind,
    RiskClass,
    Severity,
)
from nightjar.core.evidence import (
    seal_event,
    seal_evidence,
    verify_event_chain,
    verify_evidence_chain,
)
from nightjar.core.ids import ensure_utc, new_id, utcnow
from nightjar.core.models import (
    ActionExecution,
    ActionPlan,
    ActionSpec,
    AgentRun,
    ApprovalRequest,
    ChainVerification,
    EvidenceItem,
    Finding,
    Incident,
    TimelineEvent,
    VerificationPredicate,
    VerificationResult,
)


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


@dataclass
class CaseFile:
    incident: Incident
    evidence: list[EvidenceItem] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    plan: ActionPlan | None = None
    actions: list[ActionSpec] = field(default_factory=list)
    action_states: dict[str, ActionStatus] = field(default_factory=dict)
    approvals: list[ApprovalRequest] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)
    executions: list[ActionExecution] = field(default_factory=list)
    agent_runs: list[AgentRun] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def _incident(row: IncidentRow) -> Incident:
    return Incident(
        id=row.id,
        tenant_id=row.tenant_id,
        title=row.title,
        description=row.description,
        incident_class=IncidentClass(row.incident_class),
        severity=Severity(row.severity),
        status=IncidentStatus(row.status),
        source=IncidentSource(row.source),
        dedupe_key=row.dedupe_key,
        reported_by=row.reported_by,
        summary=row.summary,
        root_cause=row.root_cause,
        confidence=row.confidence,
        metadata=row.meta or {},
        created_at=ensure_utc(row.created_at),
        updated_at=ensure_utc(row.updated_at),
        resolved_at=ensure_utc(row.resolved_at) if row.resolved_at else None,
    )


def _evidence(row: EvidenceRow) -> EvidenceItem:
    return EvidenceItem(
        id=row.id,
        incident_id=row.incident_id,
        kind=EvidenceKind(row.kind),
        source=row.source,
        summary=row.summary,
        content=row.content or {},
        observed_at=ensure_utc(row.observed_at),
        collected_by=row.collected_by,
        content_hash=row.content_hash,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def _event(row: TimelineRow) -> TimelineEvent:
    return TimelineEvent(
        id=row.id,
        incident_id=row.incident_id,
        seq=row.seq,
        event_type=EventType(row.event_type),
        actor=row.actor,
        payload=row.payload or {},
        created_at=ensure_utc(row.created_at),
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def _plan(row: PlanRow) -> ActionPlan:
    return ActionPlan(
        id=row.id,
        summary=row.summary,
        root_cause=row.root_cause,
        confidence=row.confidence,
        actions=[ActionSpec.model_validate(item) for item in row.actions or []],
        predicates=[VerificationPredicate.model_validate(item) for item in row.predicates or []],
        blast_radius=list(row.blast_radius or []),
        rollback_plan=row.rollback_plan,
        created_at=ensure_utc(row.created_at),
    )


def _action(row: ActionRow) -> ActionSpec:
    return ActionSpec(
        id=row.id,
        action_type=ActionType(row.action_type),
        target=row.target,
        params=row.params or {},
        risk=RiskClass(row.risk),
        description=row.description,
        rollback=row.rollback,
        idempotency_key=row.idempotency_key,
        predicate_ids=list(row.predicate_ids or []),
        evidence_ids=list(row.evidence_ids or []),
    )


def _approval(row: ApprovalRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        incident_id=row.incident_id,
        action_id=row.action_id,
        status=ApprovalStatus(row.status),
        requested_at=ensure_utc(row.requested_at),
        expires_at=ensure_utc(row.expires_at),
        approved_by=row.approved_by,
        approved_at=ensure_utc(row.approved_at) if row.approved_at else None,
        comment=row.comment,
        token_hash=row.token_hash,
        requested_by=row.requested_by,
    )


def _verification(row: VerificationRow) -> VerificationResult:
    return VerificationResult(
        id=row.id,
        incident_id=row.incident_id,
        action_id=row.action_id,
        predicate_id=row.predicate_id,
        kind=PredicateKind(row.kind),
        passed=row.passed,
        observed=row.observed or {},
        checked_at=ensure_utc(row.checked_at),
        error=row.error,
        phase=row.phase,
        required=row.required,
    )


def _execution(row: ExecutionRow) -> ActionExecution:
    return ActionExecution(
        id=row.id,
        incident_id=row.incident_id,
        action_id=row.action_id,
        status=ActionStatus(row.status),
        before_state=row.before_state or {},
        after_state=row.after_state or {},
        error=row.error,
        started_at=ensure_utc(row.started_at) if row.started_at else None,
        finished_at=ensure_utc(row.finished_at) if row.finished_at else None,
        idempotency_key=row.idempotency_key,
    )


def _agent_run(row: AgentRunRow) -> AgentRun:
    return AgentRun(
        id=row.id,
        incident_id=row.incident_id,
        role=row.role,
        provider=row.provider,
        model=row.model,
        latency_ms=row.latency_ms,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        output=row.output or {},
        error=row.error,
        created_at=ensure_utc(row.created_at),
    )


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_tenant(self, tenant_id: str, name: str) -> None:
        async with self._session_factory() as session, session.begin():
            if await session.get(TenantRow, tenant_id) is None:
                session.add(TenantRow(id=tenant_id, name=name))

    async def create_incident(self, incident: Incident) -> tuple[Incident, bool]:
        async with self._session_factory() as session, session.begin():
            if incident.dedupe_key:
                existing = await session.scalar(
                    select(IncidentRow).where(
                        IncidentRow.tenant_id == incident.tenant_id,
                        IncidentRow.dedupe_key == incident.dedupe_key,
                    )
                )
                if existing is not None:
                    return _incident(existing), False
            row = IncidentRow(
                id=incident.id,
                tenant_id=incident.tenant_id,
                title=incident.title,
                description=incident.description,
                incident_class=incident.incident_class.value,
                severity=incident.severity.value,
                status=incident.status.value,
                source=incident.source.value,
                dedupe_key=incident.dedupe_key,
                reported_by=incident.reported_by,
                summary=incident.summary,
                root_cause=incident.root_cause,
                confidence=incident.confidence,
                meta=incident.metadata,
                created_at=incident.created_at,
                updated_at=incident.updated_at,
                resolved_at=incident.resolved_at,
            )
            session.add(row)
        return incident, True

    async def get_incident(self, incident_id: str) -> Incident:
        async with self._session_factory() as session:
            row = await session.get(IncidentRow, incident_id)
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            return _incident(row)

    async def list_incidents(
        self,
        tenant_id: str,
        *,
        status: IncidentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Incident], int]:
        async with self._session_factory() as session:
            query = select(IncidentRow).where(IncidentRow.tenant_id == tenant_id)
            count_query = (
                select(func.count())
                .select_from(IncidentRow)
                .where(IncidentRow.tenant_id == tenant_id)
            )
            if status is not None:
                query = query.where(IncidentRow.status == status.value)
                count_query = count_query.where(IncidentRow.status == status.value)
            rows = (
                await session.scalars(
                    query.order_by(IncidentRow.created_at.desc()).limit(limit).offset(offset)
                )
            ).all()
            total = await session.scalar(count_query) or 0
            return [_incident(row) for row in rows], int(total)

    async def update_incident(self, incident_id: str, **fields: Any) -> Incident:
        allowed = {
            "title",
            "description",
            "incident_class",
            "severity",
            "status",
            "summary",
            "root_cause",
            "confidence",
            "metadata",
            "resolved_at",
            "reopen_count",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown incident fields: {sorted(unknown)}")
        async with self._session_factory() as session, session.begin():
            row = await session.get(IncidentRow, incident_id, with_for_update=True)
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            for key, value in fields.items():
                if key == "metadata":
                    row.meta = value
                elif hasattr(value, "value"):
                    setattr(row, key, value.value)
                else:
                    setattr(row, key, value)
            row.updated_at = utcnow()
            await session.flush()
            return _incident(row)

    async def set_next_check(self, incident_id: str, when: datetime | None) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IncidentRow, incident_id)
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            row.next_check_at = when

    async def increment_reopen(self, incident_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IncidentRow, incident_id, with_for_update=True)
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            row.reopen_count += 1

    async def due_for_verification(self, now: datetime) -> list[str]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(IncidentRow.id).where(
                    IncidentRow.status == IncidentStatus.VERIFYING.value,
                    IncidentRow.next_check_at.is_not(None),
                    IncidentRow.next_check_at <= now,
                )
            )
            return list(rows)

    async def append_event(
        self,
        incident_id: str,
        event_type: EventType,
        *,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> TimelineEvent:
        async with self._session_factory() as session, session.begin():
            return await self._append_event_in_session(
                session, incident_id, event_type, actor=actor, payload=payload
            )

    async def _append_event_in_session(
        self,
        session: AsyncSession,
        incident_id: str,
        event_type: EventType,
        *,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> TimelineEvent:
        """Seal one event onto the chain inside an existing transaction.

        Callers that write other rows in the same transaction (a note, an
        approval) use this so the record and its chain entry commit together.
        """
        locked = await session.scalar(
            select(IncidentRow.id).where(IncidentRow.id == incident_id).with_for_update()
        )
        if locked is None:
            raise NotFoundError(f"incident {incident_id} not found")
        row = await session.execute(
            select(TimelineRow.seq, TimelineRow.hash)
            .where(TimelineRow.incident_id == incident_id)
            .order_by(TimelineRow.seq.desc())
            .limit(1)
        )
        last = row.first()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else ""
        event = seal_event(
            TimelineEvent(
                incident_id=incident_id,
                seq=seq,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
            ),
            prev_hash,
        )
        session.add(
            TimelineRow(
                id=event.id,
                incident_id=incident_id,
                seq=event.seq,
                event_type=event.event_type.value,
                actor=event.actor,
                payload=event.payload,
                created_at=event.created_at,
                prev_hash=event.prev_hash,
                hash=event.hash,
            )
        )
        return event

    async def append_evidence(self, item: EvidenceItem) -> EvidenceItem:
        async with self._session_factory() as session, session.begin():
            locked = await session.scalar(
                select(IncidentRow.id).where(IncidentRow.id == item.incident_id).with_for_update()
            )
            if locked is None:
                raise NotFoundError(f"incident {item.incident_id} not found")
            last = (
                await session.execute(
                    select(EvidenceRow.seq, EvidenceRow.hash)
                    .where(EvidenceRow.incident_id == item.incident_id)
                    .order_by(EvidenceRow.seq.desc())
                    .limit(1)
                )
            ).first()
            seq = (last.seq + 1) if last else 1
            prev_hash = last.hash if last else ""
            sealed = seal_evidence(item, prev_hash)
            session.add(
                EvidenceRow(
                    id=sealed.id,
                    incident_id=sealed.incident_id,
                    seq=seq,
                    kind=sealed.kind.value,
                    source=sealed.source,
                    summary=sealed.summary,
                    content=sealed.content,
                    observed_at=sealed.observed_at,
                    collected_by=sealed.collected_by,
                    content_hash=sealed.content_hash,
                    prev_hash=sealed.prev_hash,
                    hash=sealed.hash,
                )
            )
            return sealed

    async def list_evidence(self, incident_id: str) -> list[EvidenceItem]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(EvidenceRow)
                .where(EvidenceRow.incident_id == incident_id)
                .order_by(EvidenceRow.seq)
            )
            return [_evidence(row) for row in rows]

    async def list_timeline(self, incident_id: str, *, after_seq: int = 0) -> list[TimelineEvent]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(TimelineRow)
                .where(TimelineRow.incident_id == incident_id, TimelineRow.seq > after_seq)
                .order_by(TimelineRow.seq)
            )
            return [_event(row) for row in rows]

    async def verify_chains(self, incident_id: str) -> tuple[ChainVerification, ChainVerification]:
        evidence = await self.list_evidence(incident_id)
        timeline = await self.list_timeline(incident_id)
        return verify_evidence_chain(evidence), verify_event_chain(timeline)

    async def save_plan(self, incident_id: str, plan: ActionPlan) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                PlanRow(
                    id=plan.id,
                    incident_id=incident_id,
                    summary=plan.summary,
                    root_cause=plan.root_cause,
                    confidence=plan.confidence,
                    actions=[action.model_dump(mode="json") for action in plan.actions],
                    predicates=[predicate.model_dump(mode="json") for predicate in plan.predicates],
                    blast_radius=plan.blast_radius,
                    rollback_plan=plan.rollback_plan,
                    created_at=plan.created_at,
                )
            )
            await session.flush()
            for index, action in enumerate(plan.actions):
                session.add(
                    ActionRow(
                        id=action.id,
                        incident_id=incident_id,
                        plan_id=plan.id,
                        action_type=action.action_type.value,
                        target=action.target,
                        params=action.params,
                        risk=action.risk.value,
                        description=action.description,
                        rollback=action.rollback,
                        idempotency_key=action.idempotency_key,
                        predicate_ids=action.predicate_ids,
                        evidence_ids=action.evidence_ids,
                        status=ActionStatus.PROPOSED.value,
                        sequence=index,
                    )
                )

    async def latest_plan(self, incident_id: str) -> ActionPlan | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PlanRow)
                .where(PlanRow.incident_id == incident_id)
                .order_by(PlanRow.created_at.desc(), PlanRow.id.desc())
                .limit(1)
            )
            return _plan(row) if row else None

    async def list_actions(self, incident_id: str) -> list[ActionSpec]:
        """Return the actions of the current plan only; older plans stay in history."""
        async with self._session_factory() as session:
            plan_id = await session.scalar(
                select(PlanRow.id)
                .where(PlanRow.incident_id == incident_id)
                .order_by(PlanRow.created_at.desc(), PlanRow.id.desc())
                .limit(1)
            )
            if plan_id is None:
                return []
            rows = await session.scalars(
                select(ActionRow).where(ActionRow.plan_id == plan_id).order_by(ActionRow.sequence)
            )
            return [_action(row) for row in rows]

    async def action_states(self, incident_id: str) -> dict[str, ActionStatus]:
        async with self._session_factory() as session:
            plan_id = await session.scalar(
                select(PlanRow.id)
                .where(PlanRow.incident_id == incident_id)
                .order_by(PlanRow.created_at.desc(), PlanRow.id.desc())
                .limit(1)
            )
            if plan_id is None:
                return {}
            rows = await session.execute(
                select(ActionRow.id, ActionRow.status).where(ActionRow.plan_id == plan_id)
            )
            return {row.id: ActionStatus(row.status) for row in rows}

    async def set_action_status(self, action_id: str, status: ActionStatus) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(ActionRow, action_id, with_for_update=True)
            if row is None:
                raise NotFoundError(f"action {action_id} not found")
            row.status = status.value
            row.updated_at = utcnow()

    async def create_approval(
        self, incident_id: str, action_id: str, ttl_seconds: int, *, requested_by: str = ""
    ) -> ApprovalRequest:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            action = await session.get(ActionRow, action_id)
            if action is None or action.incident_id != incident_id:
                raise NotFoundError(f"action {action_id} not found on incident {incident_id}")
            row = ApprovalRow(
                id=new_id(),
                incident_id=incident_id,
                action_id=action_id,
                status=ApprovalStatus.PENDING.value,
                requested_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                requested_by=requested_by[:100],
            )
            session.add(row)
            await session.flush()
            return _approval(row)

    async def get_approval(self, approval_id: str) -> ApprovalRequest:
        async with self._session_factory() as session:
            row = await session.get(ApprovalRow, approval_id)
            if row is None:
                raise NotFoundError(f"approval {approval_id} not found")
            return _approval(row)

    async def list_approvals(self, incident_id: str) -> list[ApprovalRequest]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ApprovalRow)
                .where(ApprovalRow.incident_id == incident_id)
                .order_by(ApprovalRow.requested_at)
            )
            return [_approval(row) for row in rows]

    async def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        actor: str,
        comment: str = "",
    ) -> ApprovalRequest:
        async with self._session_factory() as session, session.begin():
            row = await session.get(ApprovalRow, approval_id, with_for_update=True)
            if row is None:
                raise NotFoundError(f"approval {approval_id} not found")
            if row.status != ApprovalStatus.PENDING.value:
                raise ConflictError(f"approval {approval_id} is already {row.status}")
            now = utcnow()
            if ensure_utc(row.expires_at) <= now:
                row.status = ApprovalStatus.EXPIRED.value
                await session.flush()
                raise ConflictError(f"approval {approval_id} has expired")
            row.status = (
                ApprovalStatus.APPROVED.value if approved else ApprovalStatus.REJECTED.value
            )
            row.approved_by = actor
            row.approved_at = now
            row.comment = comment
            await session.flush()
            return _approval(row)

    async def record_execution_start(
        self,
        incident_id: str,
        action: ActionSpec,
        before_state: dict[str, Any],
        *,
        idempotency_key: str = "",
    ) -> ActionExecution:
        async with self._session_factory() as session, session.begin():
            row = ExecutionRow(
                id=new_id(),
                incident_id=incident_id,
                action_id=action.id,
                status=ActionStatus.EXECUTING.value,
                before_state=before_state,
                started_at=utcnow(),
                idempotency_key=idempotency_key,
            )
            session.add(row)
            await session.flush()
            return _execution(row)

    async def record_execution_finish(
        self,
        execution_id: str,
        *,
        status: ActionStatus,
        after_state: dict[str, Any],
        error: str | None = None,
    ) -> ActionExecution:
        async with self._session_factory() as session, session.begin():
            row = await session.get(ExecutionRow, execution_id, with_for_update=True)
            if row is None:
                raise NotFoundError(f"execution {execution_id} not found")
            row.status = status.value
            row.after_state = after_state
            row.error = error
            row.finished_at = utcnow()
            await session.flush()
            return _execution(row)

    async def find_execution_by_key(self, idempotency_key: str) -> ActionExecution | None:
        """The one successful execution for this key, if the mutation already ran."""
        if not idempotency_key:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ExecutionRow)
                .where(
                    ExecutionRow.idempotency_key == idempotency_key,
                    ExecutionRow.status == ActionStatus.EXECUTED.value,
                )
                .order_by(ExecutionRow.finished_at.desc())
                .limit(1)
            )
            return _execution(row) if row else None

    async def list_executions(self, incident_id: str) -> list[ActionExecution]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ExecutionRow)
                .where(ExecutionRow.incident_id == incident_id)
                .order_by(ExecutionRow.started_at)
            )
            return [_execution(row) for row in rows]

    async def record_verification(
        self,
        result: VerificationResult,
        *,
        phase: str,
        required: bool,
    ) -> VerificationResult:
        async with self._session_factory() as session, session.begin():
            row = VerificationRow(
                id=result.id,
                incident_id=result.incident_id,
                action_id=result.action_id,
                predicate_id=result.predicate_id,
                kind=result.kind.value,
                phase=phase,
                passed=result.passed,
                required=required,
                observed=result.observed,
                error=result.error,
                checked_at=result.checked_at,
            )
            session.add(row)
            await session.flush()
            return result

    async def list_verifications(self, incident_id: str) -> list[VerificationResult]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(VerificationRow)
                .where(VerificationRow.incident_id == incident_id)
                .order_by(VerificationRow.checked_at)
            )
            return [_verification(row) for row in rows]

    async def record_agent_run(self, run: AgentRun) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                AgentRunRow(
                    id=run.id,
                    incident_id=run.incident_id,
                    role=run.role,
                    provider=run.provider,
                    model=run.model,
                    latency_ms=run.latency_ms,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    cost_usd=run.cost_usd,
                    output=run.output,
                    error=run.error,
                    created_at=run.created_at,
                )
            )

    async def list_agent_runs(self, incident_id: str) -> list[AgentRun]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AgentRunRow)
                .where(AgentRunRow.incident_id == incident_id)
                .order_by(AgentRunRow.created_at)
            )
            return [_agent_run(row) for row in rows]

    async def add_note(self, incident_id: str, author: str, body: str) -> dict[str, Any]:
        """Store an operator note and seal it onto the timeline.

        The note and its chain entry commit in one transaction: a note that is
        not in the ledger would be an unaudited claim, and the live stream uses
        the timeline entry to push the note to every open case file.
        """
        note_id = new_id()
        async with self._session_factory() as session, session.begin():
            session.add(NoteRow(id=note_id, incident_id=incident_id, author=author, body=body))
            await self._append_event_in_session(
                session,
                incident_id,
                EventType.NOTE,
                actor=author,
                payload={"note_id": note_id, "body": body},
            )
        return {"id": note_id, "author": author, "body": body}

    async def list_notes(self, incident_id: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(NoteRow)
                .where(NoteRow.incident_id == incident_id)
                .order_by(NoteRow.created_at)
            )
            return [
                {
                    "id": row.id,
                    "author": row.author,
                    "body": row.body,
                    "created_at": ensure_utc(row.created_at).isoformat(),
                }
                for row in rows
            ]

    async def expire_pending_approvals(self, incident_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            rows = await session.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.incident_id == incident_id,
                    ApprovalRow.status == ApprovalStatus.PENDING.value,
                )
            )
            for row in rows:
                row.status = ApprovalStatus.EXPIRED.value

    async def get_case_file(self, incident_id: str) -> CaseFile:
        async with self._session_factory() as session:
            row = await session.get(IncidentRow, incident_id)
            if row is None:
                raise NotFoundError(f"incident {incident_id} not found")
            incident = _incident(row)
        timeline = await self.list_timeline(incident_id)
        findings: list[Finding] = []
        for event in reversed(timeline):
            if event.event_type is EventType.FINDING_RECORDED:
                findings = [
                    Finding.model_validate(item) for item in event.payload.get("findings", [])
                ]
                break
        return CaseFile(
            incident=incident,
            evidence=await self.list_evidence(incident_id),
            timeline=timeline,
            plan=await self.latest_plan(incident_id),
            actions=await self.list_actions(incident_id),
            action_states=await self.action_states(incident_id),
            approvals=await self.list_approvals(incident_id),
            verifications=await self.list_verifications(incident_id),
            executions=await self.list_executions(incident_id),
            agent_runs=await self.list_agent_runs(incident_id),
            notes=await self.list_notes(incident_id),
            findings=findings,
        )

    async def count_open(self, tenant_id: str) -> int:
        async with self._session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(IncidentRow)
                    .where(
                        IncidentRow.tenant_id == tenant_id,
                        IncidentRow.status.not_in(
                            [
                                IncidentStatus.RESOLVED.value,
                                IncidentStatus.CANCELLED.value,
                            ]
                        ),
                    )
                )
                or 0
            )


__all__ = [
    "CaseFile",
    "ConflictError",
    "NotFoundError",
    "Repository",
]
