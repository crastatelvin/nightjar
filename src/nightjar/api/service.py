"""The incident lifecycle.

The service is deliberately boring: deterministic evidence gathering, four
bounded model calls, a policy gate, typed execution and independent
verification. Models never receive a tool that mutates state and never decide
whether an incident is resolved.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nightjar.agents.types import (
    AgentRuntime,
    CritiqueInput,
    Environment,
    InvestigationInput,
    PlanInput,
    ProbeRequest,
    TriageInput,
)
from nightjar.api.locks import LockBusy, incident_lock
from nightjar.api.repository import CaseFile, ConflictError, NotFoundError, Repository
from nightjar.api.settings import Settings
from nightjar.connectors.base import ToolResult
from nightjar.connectors.errors import ConnectorError
from nightjar.connectors.idempotency import action_key
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.connectors.retry import call_with_retries
from nightjar.core.actions import ActionValidationError, validate_plan
from nightjar.core.capabilities import available_actions, unavailable_actions
from nightjar.core.enums import (
    ActionStatus,
    ApprovalStatus,
    ClaimStatus,
    Decision,
    EventType,
    EvidenceKind,
    IncidentClass,
    IncidentSource,
    IncidentStatus,
    PredicateKind,
    Severity,
)
from nightjar.core.ids import utcnow
from nightjar.core.models import (
    ActionPlan,
    ActionSpec,
    AgentRun,
    ClaimVerdict,
    Critique,
    EvidenceItem,
    Finding,
    Incident,
    PolicyResult,
    TriageResult,
    VerificationResult,
)
from nightjar.core.policy import LocalPolicyEngine, PolicyContext, PolicyEngine, PolicyRequest
from nightjar.core.redaction import redact_secrets, redact_secrets_obj, redact_text
from nightjar.core.secrets import EnvVault
from nightjar.core.targets import normalize_managed_targets, resolve_target
from nightjar.core.telemetry import (
    ACTION_TOTAL,
    LOCK_BUSY_TOTAL,
    VERIFICATION_TOTAL,
    SpanHandle,
    Telemetry,
)
from nightjar.core.verification import ConnectorCheck, CredentialCheck, Verifier

log = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    pass


class BudgetExceeded(ServiceError):
    pass


class Forbidden(ServiceError):
    """Authenticated, but not allowed to perform this particular action."""


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What the executor should do after dispatching one action."""

    proceed: bool = True
    manual: bool = False
    unknown: bool = False


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _resolve_params(value: Any, result: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$result."):
        key = value[len("$result.") :]
        return result.get(key)
    if isinstance(value, dict):
        return {key: _resolve_params(item, result) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_params(item, result) for item in value]
    return value


class IncidentService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: Repository,
        runtime: AgentRuntime,
        registry: ConnectorRegistry,
        policy: PolicyEngine | None = None,
        verifier: Verifier | None = None,
        lock_engine: AsyncEngine | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.settings = settings
        self.repo = repository
        self.runtime = runtime
        self.registry = registry
        self.telemetry = telemetry or Telemetry()
        self.policy = policy or LocalPolicyEngine()
        self.verifier = verifier or Verifier(
            checks={
                PredicateKind.CONNECTOR_CHECK: ConnectorCheck(registry.verification_checks()),
                PredicateKind.CREDENTIAL: CredentialCheck(
                    EnvVault(), allow_private=settings.allow_private_probes
                ),
            },
            allow_private=settings.allow_private_probes,
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_engine = lock_engine
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._managed_targets = normalize_managed_targets(settings.managed_targets)

    # ------------------------------------------------------------------ intake
    async def intake(
        self,
        *,
        title: str,
        description: str = "",
        incident_class: IncidentClass = IncidentClass.UNKNOWN,
        severity: Severity = Severity.MEDIUM,
        source: IncidentSource = IncidentSource.API,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        reported_by: str = "",
        wait: bool = False,
    ) -> Incident:
        incident, _ = await self.intake_event(
            title=title,
            description=description,
            incident_class=incident_class,
            severity=severity,
            source=source,
            metadata=metadata,
            dedupe_key=dedupe_key,
            reported_by=reported_by,
            wait=wait,
        )
        return incident

    async def intake_event(
        self,
        *,
        title: str,
        description: str = "",
        incident_class: IncidentClass = IncidentClass.UNKNOWN,
        severity: Severity = Severity.MEDIUM,
        source: IncidentSource = IncidentSource.API,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        reported_by: str = "",
        wait: bool = False,
    ) -> tuple[Incident, bool]:
        """File an incident, returning it and whether it was newly created.

        The flag lets a signed webhook tell a fresh alert from a deduplicated
        retry without the caller re-reading the case list.
        """
        safe_description, _ = redact_text(description)
        safe_meta, _ = redact_secrets_obj(metadata or {})
        incident = Incident(
            tenant_id=self.settings.tenant_id,
            title=title.strip()[:300],
            description=safe_description,
            incident_class=incident_class,
            severity=severity,
            status=IncidentStatus.INTAKE,
            source=source,
            dedupe_key=dedupe_key,
            reported_by=reported_by.strip()[:100],
            metadata=safe_meta,
        )
        stored, created = await self.repo.create_incident(incident)
        if not created:
            return stored, False
        await self.repo.append_event(
            stored.id,
            EventType.INCIDENT_CREATED,
            actor="intake",
            payload={
                "title": stored.title,
                "source": stored.source.value,
                "severity": stored.severity.value,
                "incident_class": stored.incident_class.value,
            },
        )
        if wait:
            await self._start_pipeline(stored.id)
        else:
            self._spawn_or_start(stored.id)
        return await self.repo.get_incident(stored.id), True

    def _spawn_or_start(self, incident_id: str) -> None:
        if self.settings.orchestrator == "temporal":
            from nightjar.worker.client import start_incident_workflow

            self.spawn(
                start_incident_workflow(
                    incident_id,
                    approval_ttl_seconds=self.settings.approval_ttl_seconds,
                    stability_seconds=self.settings.stability_seconds,
                )
            )
        else:
            self.spawn(self._run_pipeline_background(incident_id))

    async def _start_pipeline(self, incident_id: str) -> None:
        if self.settings.orchestrator == "temporal":
            from nightjar.worker.client import start_incident_workflow

            await start_incident_workflow(
                incident_id,
                approval_ttl_seconds=self.settings.approval_ttl_seconds,
                stability_seconds=self.settings.stability_seconds,
            )
        else:
            await self.run_pipeline(incident_id)

    def spawn(self, coro: Any) -> None:
        if self._closed:
            return
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # --------------------------------------------------------------- lifecycle
    @property
    def lock_engine(self) -> AsyncEngine | None:
        return self._lock_engine

    def _lock(self, incident_id: str) -> AbstractAsyncContextManager[None]:
        """Cross-process when a lock engine is configured, in-process otherwise."""
        if self._lock_engine is not None and self.settings.db_locks:
            return incident_lock(self._lock_engine, incident_id)
        return self._local_lock(incident_id)

    @asynccontextmanager
    async def _local_lock(self, incident_id: str) -> AsyncIterator[None]:
        lock = self._locks.get(incident_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[incident_id] = lock
        async with lock:
            yield

    async def run_pipeline(self, incident_id: str) -> None:
        async with self._lock(incident_id):
            await self._run_pipeline_locked(incident_id)

    async def _run_pipeline_background(self, incident_id: str) -> None:
        try:
            await self.run_pipeline(incident_id)
        except LockBusy:
            self.telemetry.count(LOCK_BUSY_TOTAL, scope="incident")
            log.info("incident %s is already being handled by another process", incident_id)

    async def _run_pipeline_locked(self, incident_id: str) -> None:
        incident = await self.repo.get_incident(incident_id)
        if incident.status not in {IncidentStatus.INTAKE, IncidentStatus.REOPENED}:
            return
        try:
            await self._set_status(incident, IncidentStatus.INVESTIGATING, "pipeline started")
            bundle = await self._investigate(incident)
            if (
                bundle.critique.overall.value == "contradicted"
                or not bundle.findings
                or bundle.triage.incident_class is IncidentClass.UNKNOWN
            ):
                await self._fail(
                    incident_id,
                    "investigation did not produce a supported, classifiable finding",
                )
                return
            plan = await self._plan(incident, bundle)
            if plan is None:
                return
            incident = await self.repo.get_incident(incident_id)
            await self._gate_and_execute(incident, plan, bundle)
        except BudgetExceeded as exc:
            log.warning("incident %s exceeded its budget: %s", incident_id, exc)
            await self._fail(incident_id, str(exc))
        except (ServiceError, ActionValidationError) as exc:
            log.warning("incident %s failed: %s", incident_id, exc)
            await self._fail(incident_id, str(exc))
        except Exception as exc:  # pragma: no cover - last-resort containment
            log.exception("incident %s crashed", incident_id)
            await self._fail(incident_id, f"internal error: {type(exc).__name__}")

    async def _set_status(
        self, incident: Incident, status: IncidentStatus, reason: str
    ) -> Incident:
        previous = incident.status
        updated = await self.repo.update_incident(incident.id, status=status)
        await self.repo.append_event(
            incident.id,
            EventType.STATUS_CHANGED,
            actor="orchestrator",
            payload={"from": previous.value, "to": status.value, "reason": reason},
        )
        return updated

    async def _fail(self, incident_id: str, reason: str) -> None:
        incident = await self.repo.get_incident(incident_id)
        if incident.status in {IncidentStatus.RESOLVED, IncidentStatus.FAILED}:
            return
        await self.repo.update_incident(incident_id, status=IncidentStatus.FAILED, summary=reason)
        await self.repo.set_next_check(incident_id, None)
        await self.repo.append_event(
            incident_id,
            EventType.STATUS_CHANGED,
            actor="orchestrator",
            payload={
                "from": incident.status.value,
                "to": IncidentStatus.FAILED.value,
                "reason": reason,
            },
        )

    # ------------------------------------------------------------ investigation
    def _environment(self, incident: Incident) -> Environment:
        site = str(incident.metadata.get("site_url") or f"{self.settings.lab_base_url}/site/")
        checkout = str(incident.metadata.get("checkout_url") or site.rstrip("/") + "/checkout")
        return Environment(
            tenant_name=self.settings.tenant_name,
            site_url=site,
            checkout_url=checkout,
            state_url=f"{self.settings.lab_base_url}/lab/state",
        )

    def _initial_probes(self, incident: Incident) -> list[ProbeRequest]:
        available = set(self.registry.tools())
        candidates: list[ProbeRequest] = []
        if incident.incident_class is IncidentClass.SITE_OUTAGE:
            site = str(incident.metadata.get("site_url") or f"{self.settings.lab_base_url}/site/")
            candidates.extend(
                [
                    ProbeRequest(tool="http.get", params={"url": site}, reason="confirm impact"),
                    ProbeRequest(tool="lab.site_state", params={}, reason="environment state"),
                    ProbeRequest(tool="lab.deployments", params={}, reason="recent changes"),
                ]
            )
        elif incident.incident_class is IncidentClass.CREDENTIAL_EXPOSURE:
            credential_id = incident.metadata.get("credential_id")
            if credential_id:
                candidates.append(
                    ProbeRequest(
                        tool="lab.credential",
                        params={"credential_id": str(credential_id)},
                        reason="credential state",
                    )
                )
            else:
                candidates.append(
                    ProbeRequest(tool="lab.credentials", params={}, reason="credential inventory")
                )
        elif incident.incident_class is IncidentClass.PHISHING:
            message_id = incident.metadata.get("message_id")
            if message_id:
                candidates.extend(
                    [
                        ProbeRequest(
                            tool="lab.mail_message",
                            params={"message_id": str(message_id)},
                            reason="message contents",
                        ),
                        ProbeRequest(
                            tool="lab.mail_reputation",
                            params={"message_id": str(message_id)},
                            reason="sender reputation",
                        ),
                    ]
                )
        return [probe for probe in candidates if probe.tool in available]

    async def _collect_probe(self, incident: Incident, probe: ProbeRequest) -> EvidenceItem | None:
        try:
            result: ToolResult = await self.registry.collect(probe.tool, probe.params)
        except ConnectorError as exc:
            await self.repo.append_event(
                incident.id,
                EventType.NOTE,
                actor="investigator",
                payload={"probe": probe.tool, "error": str(exc)},
            )
            return None
        content, _ = redact_secrets_obj(result.content)
        summary, _ = redact_secrets(result.summary)
        item = EvidenceItem(
            incident_id=incident.id,
            kind=result.kind,
            source=result.source,
            summary=summary,
            content=content,
            collected_by=f"tool:{probe.tool}",
        )
        sealed = await self.repo.append_evidence(item)
        await self.repo.append_event(
            incident.id,
            EventType.EVIDENCE_COLLECTED,
            actor=f"tool:{probe.tool}",
            payload={
                "evidence_id": sealed.id,
                "kind": sealed.kind.value,
                "source": sealed.source,
                "summary": sealed.summary,
            },
        )
        return sealed

    async def _collect_round(
        self, incident: Incident, probes: list[ProbeRequest], seen: set[str]
    ) -> list[EvidenceItem]:
        collected: list[EvidenceItem] = []
        for probe in probes:
            key = f"{probe.tool}:{sorted(probe.params.items())}"
            if key in seen:
                continue
            if len(seen) >= self.settings.max_investigation_tool_calls:
                break
            seen.add(key)
            item = await self._collect_probe(incident, probe)
            if item is not None:
                collected.append(item)
        return collected

    async def _record_agent(self, incident_id: str, role: str, outcome: Any) -> None:
        await self.repo.record_agent_run(
            AgentRun(
                incident_id=incident_id,
                role=role,
                provider=outcome.provider,
                model=outcome.model,
                latency_ms=outcome.latency_ms,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=outcome.cost_usd,
                output=_jsonable(outcome.output),
            )
        )

    async def _investigate(self, incident: Incident) -> Any:
        from nightjar.agents.types import InvestigationBundle

        env = self._environment(incident)
        evidence: list[EvidenceItem] = []
        seen: set[str] = set()
        evidence.extend(await self._collect_round(incident, self._initial_probes(incident), seen))

        cost = 0.0
        triage_outcome = await self.runtime.triage(TriageInput(incident=incident, environment=env))
        await self._record_agent(incident.id, "triage", triage_outcome)
        cost += triage_outcome.cost_usd
        triage: TriageResult = triage_outcome.output
        incident = await self.repo.update_incident(
            incident.id,
            incident_class=triage.incident_class,
            severity=triage.severity,
            summary=triage.summary,
            confidence=triage.confidence,
        )
        await self.repo.append_event(
            incident.id,
            EventType.TRIAGE_COMPLETED,
            actor="triage",
            payload=triage.model_dump(mode="json"),
        )

        findings: list[Finding] = []
        available = self.registry.tools()
        for round_number in (1, 2):
            outcome = await self.runtime.investigate(
                InvestigationInput(
                    incident=incident,
                    triage=triage,
                    evidence=evidence,
                    environment=env,
                    round=round_number,
                    available_tools=available,
                )
            )
            await self._record_agent(incident.id, "investigator", outcome)
            cost += outcome.cost_usd
            if cost > self.settings.max_cost_usd:
                raise BudgetExceeded(
                    f"investigation cost ${cost:.2f} exceeded the "
                    f"${self.settings.max_cost_usd:.2f} ceiling"
                )
            output = outcome.output
            findings.extend(output.findings)
            if not output.probes or round_number == 2:
                break
            new_evidence = await self._collect_round(incident, output.probes, seen)
            if not new_evidence:
                break
            evidence.extend(new_evidence)

        critique_outcome = await self.runtime.critique(
            CritiqueInput(incident=incident, findings=findings, evidence=evidence)
        )
        await self._record_agent(incident.id, "critic", critique_outcome)
        cost += critique_outcome.cost_usd
        critique: Critique = critique_outcome.output

        self._apply_verdicts(findings, critique)
        await self.repo.append_event(
            incident.id,
            EventType.FINDING_RECORDED,
            actor="investigator",
            payload={
                "findings": [finding.model_dump(mode="json") for finding in findings],
                "critique": critique.model_dump(mode="json"),
            },
        )
        return InvestigationBundle(
            triage=triage,
            findings=findings,
            critique=critique,
            evidence=evidence,
            cost_usd=cost,
        )

    @staticmethod
    def _apply_verdicts(findings: list[Finding], critique: Critique) -> None:
        verdicts: dict[str, ClaimVerdict] = {
            verdict.claim_id: verdict for verdict in critique.verdicts
        }
        mapping = {
            "supported": ClaimStatus.SUPPORTED,
            "partial": ClaimStatus.PARTIAL,
            "unsupported": ClaimStatus.UNSUPPORTED,
            "contradicted": ClaimStatus.CONTRADICTED,
        }
        for finding in findings:
            for claim in finding.claims:
                verdict = verdicts.get(claim.id)
                if verdict is not None:
                    claim.status = mapping.get(verdict.verdict.value, ClaimStatus.UNVERIFIED)

    def _bind_providers(self, actions: list[ActionSpec]) -> tuple[list[ActionSpec], list[str]]:
        """Turn configuration into an explicit provider on each action.

        The planner names an action, not a vendor. When exactly one configured
        connector implements it, that connector is bound here so policy resolves
        the same target the executor will use. With more than one, the planner
        has to say which, and the plan fails before approval rather than at
        execution.
        """
        bound: list[ActionSpec] = []
        ambiguous: list[str] = []
        for action in actions:
            if action.params.get("connector"):
                bound.append(action)
                continue
            providers = self.registry.providers_for(action.action_type)
            if len(providers) == 1:
                bound.append(
                    action.model_copy(
                        update={"params": {**action.params, "connector": providers[0]}}
                    )
                )
            else:
                ambiguous.append(f"{action.action_type.value} ({', '.join(providers) or 'none'})")
                bound.append(action)
        return bound, ambiguous

    async def _plan(self, incident: Incident, bundle: Any) -> ActionPlan | None:
        outcome = await self.runtime.plan(
            PlanInput(
                incident=incident,
                triage=bundle.triage,
                findings=bundle.findings,
                critique=bundle.critique,
                evidence=bundle.evidence,
                environment=self._environment(incident),
                capabilities=available_actions(self.registry.connectors),
            )
        )
        await self._record_agent(incident.id, "planner", outcome)
        plan: ActionPlan = outcome.output
        unavailable = unavailable_actions(plan.actions, self.registry.connectors)
        if unavailable:
            await self._fail(
                incident.id,
                "planner proposed actions this deployment cannot perform: "
                + "; ".join(unavailable),
            )
            return None
        bound, ambiguous = self._bind_providers(plan.actions)
        if ambiguous:
            await self._fail(
                incident.id,
                "planner did not choose a provider for: " + "; ".join(ambiguous),
            )
            return None
        plan = plan.model_copy(update={"actions": bound})
        if not plan.actions:
            await self._fail(
                incident.id,
                plan.summary or "planner produced no supported remediation",
            )
            return None
        try:
            validate_plan(plan, incident_class=incident.incident_class)
        except ActionValidationError as exc:
            await self._fail(incident.id, f"planner produced an invalid plan: {exc}")
            return None
        await self.repo.save_plan(incident.id, plan)
        await self.repo.update_incident(
            incident.id,
            root_cause=plan.root_cause,
            confidence=plan.confidence,
        )
        await self.repo.append_event(
            incident.id,
            EventType.PLAN_PROPOSED,
            actor="planner",
            payload=plan.model_dump(mode="json"),
        )
        return plan

    # ------------------------------------------------------------ policy/execute
    async def _gate_and_execute(self, incident: Incident, plan: ActionPlan, bundle: Any) -> None:
        denied: list[tuple[ActionSpec, PolicyResult]] = []
        approvals: list[tuple[ActionSpec, Any]] = []
        for action in plan.actions:
            target = resolve_target(action)
            request = PolicyRequest(
                action=action,
                context=PolicyContext(
                    tenant_id=incident.tenant_id,
                    incident_id=incident.id,
                    incident_class=incident.incident_class,
                    severity=incident.severity,
                    confidence=plan.confidence,
                    evidence_count=len(bundle.evidence),
                    blast_radius=len(plan.blast_radius),
                    managed_targets=self._managed_targets,
                    target_provider=target.provider if target else None,
                    target_kind=target.kind if target else None,
                    target_value=target.value if target else None,
                ),
            )
            try:
                decision = await self.policy.decide(request)
            except Exception as exc:
                log.warning("policy engine error for %s: %s", action.id, exc)
                decision = PolicyResult(
                    decision=Decision.DENY,
                    risk=action.risk,
                    reasons=[f"policy engine error: {exc}"],
                    matched_rules=["fail_closed"],
                )
            await self.repo.append_event(
                incident.id,
                EventType.POLICY_DECIDED,
                actor="policy",
                payload={
                    "action_id": action.id,
                    "action_type": action.action_type.value,
                    "decision": decision.decision.value,
                    "risk": decision.risk.value,
                    "reasons": decision.reasons,
                    "matched_rules": decision.matched_rules,
                    "required_approvals": decision.required_approvals,
                    "engine": decision.engine,
                },
            )
            if decision.decision is Decision.DENY:
                denied.append((action, decision))
            elif decision.decision is Decision.REQUIRE_APPROVAL:
                approval = await self.repo.create_approval(
                    incident.id,
                    action.id,
                    self.settings.approval_ttl_seconds,
                    requested_by=incident.reported_by or "system",
                )
                await self.repo.set_action_status(action.id, ActionStatus.AWAITING_APPROVAL)
                approvals.append((action, approval))
                await self.repo.append_event(
                    incident.id,
                    EventType.APPROVAL_REQUESTED,
                    actor="policy",
                    payload={
                        "approval_id": approval.id,
                        "action_id": action.id,
                        "action_type": action.action_type.value,
                        "expires_at": approval.expires_at.isoformat(),
                        "reasons": decision.reasons,
                    },
                )
            else:
                await self.repo.set_action_status(action.id, ActionStatus.APPROVED)

        if denied:
            for action, _ in denied:
                await self.repo.set_action_status(action.id, ActionStatus.DENIED)
            for action in plan.actions:
                if action.id not in {item[0].id for item in denied}:
                    await self.repo.set_action_status(action.id, ActionStatus.CANCELLED)
            reasons = "; ".join(reason for _, decision in denied for reason in decision.reasons)
            await self._fail(incident.id, f"policy denied the plan: {reasons}")
            return

        if approvals:
            await self._set_status(
                incident,
                IncidentStatus.AWAITING_APPROVAL,
                f"{len(approvals)} action(s) require operator approval",
            )
            return

        await self._execute_and_verify(incident.id)

    async def _execute_and_verify(self, incident_id: str) -> None:
        incident = await self.repo.get_incident(incident_id)
        plan = await self.repo.latest_plan(incident_id)
        if plan is None:
            await self._fail(incident_id, "no plan available for execution")
            return
        actions = await self.repo.list_actions(incident_id)
        states = await self.repo.action_states(incident_id)
        not_ready = [
            action
            for action in actions
            if states.get(action.id)
            not in {
                ActionStatus.APPROVED,
                ActionStatus.EXECUTED,
                ActionStatus.AWAITING_MANUAL,
                ActionStatus.ATTESTED,
            }
        ]
        if not_ready:
            await self._fail(
                incident_id,
                "refusing to execute: actions are not approved "
                f"({', '.join(action.action_type.value for action in not_ready)})",
            )
            return

        incident = await self._set_status(
            incident, IncidentStatus.EXECUTING, "all actions allowed or approved"
        )
        manual_actions: list[ActionSpec] = []
        for action in actions:
            if states.get(action.id) in {
                ActionStatus.EXECUTED,
                ActionStatus.AWAITING_MANUAL,
                ActionStatus.ATTESTED,
            }:
                continue
            provider = self._provider_name(action)
            with self.telemetry.span(
                "nightjar.action",
                incident_id=incident_id,
                action_type=action.action_type.value,
                provider=provider,
                target=action.target,
                risk=action.risk.value,
            ) as span:
                outcome = await self._execute_one(incident_id, plan, action, provider, span)
            if outcome.manual:
                manual_actions.append(action)
            if outcome.unknown:
                await self._reconcile_unknown(incident_id, action, provider)
                return
            if not outcome.proceed:
                return
        if manual_actions:
            await self._set_status(
                incident,
                IncidentStatus.AWAITING_MANUAL,
                f"{len(manual_actions)} manual step(s) require a human; "
                "verification runs after attestation",
            )
            return
        await self._verify(incident_id, phase="t0")

    def _provider_name(self, action: ActionSpec) -> str:
        """The connector that will run this action, or ``unbound``.

        A span attribute is not a place to raise: an action that policy denied, or
        one whose connector is ambiguous, still gets a span, labelled honestly.
        """
        try:
            return self.registry.connector_for(action).name
        except ConnectorError:
            return "unbound"

    async def _execute_one(
        self,
        incident_id: str,
        plan: ActionPlan,
        action: ActionSpec,
        provider: str,
        span: SpanHandle,
    ) -> _Outcome:
        """One action, and what the executor should do next."""
        manual = self.registry.manual_step(action)
        if manual is not None:
            # No mutation happens here. The provider has no API for it, so
            # Nightjar records the human step and refuses to resolve until
            # the verification predicate passes.
            execution = await self.repo.record_execution_start(
                incident_id,
                action,
                before_state={"action": action.action_type.value, "target": action.target},
            )
            await self.repo.record_execution_finish(
                execution.id,
                status=ActionStatus.AWAITING_MANUAL,
                after_state=manual.as_dict(),
            )
            await self.repo.set_action_status(action.id, ActionStatus.AWAITING_MANUAL)
            await self.repo.append_event(
                incident_id,
                EventType.MANUAL_STEP_REQUIRED,
                actor="executor",
                payload={
                    "action_id": action.id,
                    "action_type": action.action_type.value,
                    "manual": manual.as_dict(),
                },
            )
            span.set(outcome="manual")
            self.telemetry.count(
                ACTION_TOTAL,
                provider=provider,
                action=action.action_type.value,
                outcome="manual",
            )
            return _Outcome(proceed=True, manual=True)
        key = action_key(incident_id, plan.id, action)
        replay = await self.repo.find_execution_by_key(key)
        if replay is not None:
            # This exact mutation already succeeded in this plan. A second
            # approval decision or a retried worker must not do it again.
            await self.repo.set_action_status(action.id, ActionStatus.EXECUTED)
            await self.repo.append_event(
                incident_id,
                EventType.ACTION_REPLAYED,
                actor="executor",
                payload={
                    "action_id": action.id,
                    "action_type": action.action_type.value,
                    "idempotency_key": key,
                    "replay_of": replay.id,
                    "result": replay.after_state,
                },
            )
            span.set(outcome="replayed")
            self.telemetry.count(
                ACTION_TOTAL,
                provider=provider,
                action=action.action_type.value,
                outcome="replayed",
            )
            return _Outcome()
        execution = await self.repo.record_execution_start(
            incident_id,
            action,
            before_state={"action": action.action_type.value, "target": action.target},
            idempotency_key=key,
        )
        await self.repo.set_action_status(action.id, ActionStatus.EXECUTING)
        await self.repo.append_event(
            incident_id,
            EventType.ACTION_STARTED,
            actor="executor",
            payload={
                "action_id": action.id,
                "action_type": action.action_type.value,
                "target": action.target,
                "execution_id": execution.id,
            },
        )

        async def _run(action: ActionSpec = action) -> dict[str, Any]:
            return await self.registry.execute(action)

        try:
            result, attempts = await call_with_retries(
                _run,
                attempts=self.settings.connector_max_attempts,
                max_wait=self.settings.connector_retry_max_wait_seconds,
            )
        except Exception as exc:
            detail = exc.as_dict() if isinstance(exc, ConnectorError) else {"message": str(exc)}
            maybe_applied = isinstance(exc, ConnectorError) and exc.maybe_applied
            status = ActionStatus.UNKNOWN if maybe_applied else ActionStatus.FAILED
            await self.repo.record_execution_finish(
                execution.id,
                status=status,
                after_state={"error": detail, "outcome": status.value},
                error=str(exc),
            )
            await self.repo.set_action_status(action.id, status)
            await self.repo.append_event(
                incident_id,
                EventType.ACTION_OUTCOME_UNKNOWN if maybe_applied else EventType.ACTION_FAILED,
                actor="executor",
                payload={"action_id": action.id, "error": str(exc), "detail": detail},
            )
            span.set(
                outcome=status.value,
                error=type(exc).__name__,
                maybe_applied=maybe_applied,
            )
            self.telemetry.count(
                ACTION_TOTAL,
                provider=provider,
                action=action.action_type.value,
                outcome=status.value,
            )
            if maybe_applied:
                # The request left and the answer did not come back. Retrying can
                # duplicate the change; assuming failure can leave a real outage
                # unfixed while the case closes as failed. Stop and let the
                # verifier decide from the target's actual state.
                return _Outcome(proceed=False, unknown=True)
            await self._fail(incident_id, f"action {action.action_type.value} failed: {exc}")
            return _Outcome(proceed=False)
        await self.repo.record_execution_finish(
            execution.id,
            status=ActionStatus.EXECUTED,
            after_state=result,
        )
        await self.repo.set_action_status(action.id, ActionStatus.EXECUTED)
        payload: dict[str, Any] = {
            "action_id": action.id,
            "action_type": action.action_type.value,
            "result": result,
        }
        if attempts:
            # A success after a retry is worth recording: the provider was
            # flaky, and the operator should be able to see that.
            payload["attempts"] = [attempt.as_dict() for attempt in attempts]
        await self.repo.append_event(
            incident_id,
            EventType.ACTION_COMPLETED,
            actor="executor",
            payload=payload,
        )
        span.set(outcome="executed", attempts=len(attempts))
        self.telemetry.count(
            ACTION_TOTAL,
            provider=provider,
            action=action.action_type.value,
            outcome="executed",
        )
        return _Outcome()

    async def _reconcile_unknown(self, incident_id: str, action: ActionSpec, provider: str) -> None:
        """Settle an action whose outcome the provider never confirmed.

        A read timeout or a 5xx after a mutation means the change may or may not
        have landed. Re-dispatching the same action risks doing it twice, and
        declaring failure risks closing a case that was actually fixed. The
        verification predicates are the only honest arbiter: they read the
        target, not the request.

        Passing predicates resolve the case through the normal stability window.
        Failing predicates fail it with the action left ``unknown``, because only
        a human who can see the provider's console can say what happened.
        """
        log.warning(
            "action %s on %s against %s has an unknown outcome; reconciling through verification",
            action.action_type.value,
            action.target,
            provider,
        )
        await self._verify(incident_id, phase="reconcile", unknown=action)

    # ---------------------------------------------------------------- approvals
    async def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        actor: str,
        comment: str = "",
    ) -> CaseFile:
        approval = await self.repo.get_approval(approval_id)
        if (
            self.settings.require_separation_of_duties
            and approval.requested_by
            and actor == approval.requested_by
        ):
            # One person filing an incident and then approving its own R2 action is
            # the control failure separation of duties exists to prevent.
            raise Forbidden(
                f"{actor} filed this incident and cannot approve its remediation; "
                "a second operator must decide"
            )
        async with self._lock(approval.incident_id):
            decided = await self.repo.decide_approval(
                approval_id, approved=approved, actor=actor, comment=comment
            )
            await self.repo.set_action_status(
                decided.action_id,
                ActionStatus.APPROVED if approved else ActionStatus.DENIED,
            )
            await self.repo.append_event(
                approval.incident_id,
                EventType.APPROVAL_DECIDED,
                actor=actor,
                payload={
                    "approval_id": approval_id,
                    "action_id": decided.action_id,
                    "status": decided.status.value,
                    "comment": comment,
                    "requested_by": approval.requested_by,
                    "separation_of_duties": self.settings.require_separation_of_duties,
                },
            )
            incident = await self.repo.get_incident(approval.incident_id)
            if not approved:
                await self.repo.expire_pending_approvals(approval.incident_id)
                states = await self.repo.action_states(approval.incident_id)
                for action in await self.repo.list_actions(approval.incident_id):
                    state = states.get(action.id)
                    if state not in {
                        ActionStatus.EXECUTED,
                        ActionStatus.FAILED,
                        ActionStatus.UNKNOWN,
                        ActionStatus.DENIED,
                    }:
                        await self.repo.set_action_status(action.id, ActionStatus.CANCELLED)
                await self._fail(approval.incident_id, f"operator {actor} rejected the plan")
                if self.settings.orchestrator == "temporal":
                    from nightjar.worker.client import signal_approval

                    await signal_approval(approval.incident_id, False)
                return await self.repo.get_case_file(approval.incident_id)

            remaining = [
                item
                for item in await self.repo.list_approvals(approval.incident_id)
                if item.status is ApprovalStatus.PENDING
            ]
            if remaining:
                return await self.repo.get_case_file(approval.incident_id)

            if self.settings.orchestrator == "temporal":
                from nightjar.worker.client import signal_approval

                await signal_approval(approval.incident_id, True)
                return await self.repo.get_case_file(approval.incident_id)

            if incident.status is IncidentStatus.AWAITING_APPROVAL:
                await self._execute_and_verify(approval.incident_id)
            return await self.repo.get_case_file(approval.incident_id)

    # ------------------------------------------------------------- manual steps
    async def attest_manual_step(
        self,
        incident_id: str,
        action_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> None:
        """Record a human's claim and let verification decide whether it is true.

        Attestation is evidence, not authority. It never resolves an incident by
        itself: the required predicate still has to pass, and a failed predicate
        reopens the incident.
        """
        async with self._lock(incident_id):
            incident = await self.repo.get_incident(incident_id)
            states = await self.repo.action_states(incident_id)
            state = states.get(action_id)
            if state is None:
                raise NotFoundError(f"action {action_id} is not part of this incident")
            if state not in {ActionStatus.AWAITING_MANUAL, ActionStatus.ATTESTED}:
                raise ConflictError(f"action is {state.value}, not awaiting a manual step")
            if state is ActionStatus.ATTESTED and incident.status is not IncidentStatus.REOPENED:
                raise ConflictError("manual step is already attested")

            actions = await self.repo.list_actions(incident_id)
            action = next((item for item in actions if item.id == action_id), None)
            if action is None:
                raise NotFoundError(f"action {action_id} is not part of this incident")

            manual: dict[str, Any] | None = None
            for execution in reversed(await self.repo.list_executions(incident_id)):
                if execution.action_id == action_id and execution.after_state.get("provider"):
                    manual = execution.after_state
                    break
            if manual is None:
                raise ConflictError("no manual instructions are recorded for this action")

            evidence = await self.repo.append_evidence(
                EvidenceItem(
                    incident_id=incident_id,
                    kind=EvidenceKind.HUMAN_NOTE,
                    source=f"operator:{actor}",
                    summary=(
                        f"Manual step attested: {manual.get('title') or action.action_type.value}"
                    ),
                    content={
                        "action_id": action_id,
                        "action_type": action.action_type.value,
                        "provider": manual.get("provider"),
                        "fingerprint": manual.get("fingerprint"),
                        "dashboard_url": manual.get("dashboard_url"),
                        "note": note,
                    },
                    collected_by=actor,
                )
            )
            await self.repo.set_action_status(action_id, ActionStatus.ATTESTED)
            await self.repo.append_event(
                incident_id,
                EventType.MANUAL_STEP_ATTESTED,
                actor=actor,
                payload={
                    "action_id": action_id,
                    "evidence_id": evidence.id,
                    "note": note,
                },
            )

            remaining = [
                item
                for item in actions
                if item.id != action_id and states.get(item.id) is ActionStatus.AWAITING_MANUAL
            ]
            if remaining:
                return
            # The human's word is not proof. Run the required predicates.
            await self._verify(incident_id, phase="t0")

    # -------------------------------------------------------------- verification
    async def _verify(
        self, incident_id: str, *, phase: str, unknown: ActionSpec | None = None
    ) -> bool:
        """Run every predicate and act on the outcome.

        ``unknown`` marks the run as the reconciliation of an action whose
        outcome the provider never confirmed. The pass path is the same — the
        target is healthy, which is the only thing that matters. The fail path
        must not reopen: a fresh plan would re-dispatch a mutation that may
        already exist.
        """
        incident = await self.repo.get_incident(incident_id)
        plan = await self.repo.latest_plan(incident_id)
        if plan is None:
            await self._fail(incident_id, "no plan available for verification")
            return False
        executions = await self.repo.list_executions(incident_id)
        results_by_action = {execution.action_id: execution.after_state for execution in executions}
        action_for_predicate: dict[str, str] = {}
        for action in plan.actions:
            for predicate_id in action.predicate_ids:
                action_for_predicate[predicate_id] = action.id

        results: list[VerificationResult] = []
        for predicate in plan.predicates:
            action_id = action_for_predicate.get(predicate.id)
            resolved = predicate.model_copy(
                update={
                    "params": _resolve_params(
                        predicate.params, results_by_action.get(action_id or "", {})
                    )
                }
            )
            with self.telemetry.span(
                "nightjar.verification",
                incident_id=incident_id,
                predicate_id=predicate.id,
                kind=predicate.kind.value,
                required=predicate.required,
                phase=phase,
            ) as span:
                result = await self.verifier.run(
                    resolved, incident_id=incident_id, action_id=action_id
                )
                span.set(passed=result.passed, error=result.error)
            self.telemetry.count(
                VERIFICATION_TOTAL,
                kind=predicate.kind.value,
                required=predicate.required,
                passed=result.passed,
            )
            result.phase = phase
            result.required = predicate.required
            await self.repo.record_verification(result, phase=phase, required=predicate.required)
            await self.repo.append_evidence(
                EvidenceItem(
                    incident_id=incident_id,
                    kind=EvidenceKind.VERIFICATION,
                    source=f"verifier:{predicate.kind.value}",
                    summary=(
                        f"{'PASS' if result.passed else 'FAIL'} {predicate.description}"
                        + (f" ({result.error})" if result.error else "")
                    ),
                    content={
                        "predicate_id": predicate.id,
                        "phase": phase,
                        "passed": result.passed,
                        "observed": result.observed,
                        "error": result.error,
                    },
                    collected_by="verifier",
                )
            )
            results.append(result)

        required = [result for result in results if result.required]
        passed = bool(required) and all(result.passed for result in required)
        await self.repo.append_event(
            incident_id,
            EventType.VERIFICATION_COMPLETED,
            actor="verifier",
            payload={
                "phase": phase,
                "passed": passed,
                "results": [
                    {
                        "predicate_id": result.predicate_id,
                        "kind": result.kind.value,
                        "passed": result.passed,
                        "observed": result.observed,
                        "error": result.error,
                    }
                    for result in results
                ],
            },
        )

        if phase in {"t0", "reconcile"}:
            if not passed:
                if unknown is not None:
                    await self._fail(
                        incident_id,
                        f"action {unknown.action_type.value} on {unknown.target} may or "
                        "may not have applied; the provider did not confirm it and the "
                        "required predicates still fail. Check the provider before "
                        "filing a new incident.",
                    )
                    return False
                await self._reopen(incident, "post-remediation verification failed")
                return False
            if self.settings.stability_seconds <= 0:
                await self._resolve(incident, phase)
                return True
            await self._set_status(
                incident,
                IncidentStatus.VERIFYING,
                f"initial checks passed; stability window is {self.settings.stability_seconds}s",
            )
            await self.repo.set_next_check(
                incident_id, utcnow() + timedelta(seconds=self.settings.stability_seconds)
            )
            return True

        if passed:
            await self._resolve(incident, phase)
            return True
        await self._reopen(incident, "stability window failed; the fix did not hold")
        return False

    async def _resolve(self, incident: Incident, phase: str) -> None:
        await self.repo.update_incident(
            incident.id,
            status=IncidentStatus.RESOLVED,
            resolved_at=utcnow(),
        )
        await self.repo.set_next_check(incident.id, None)
        await self.repo.append_event(
            incident.id,
            EventType.INCIDENT_RESOLVED,
            actor="verifier",
            payload={"phase": phase, "message": "all required predicates passed"},
        )

    async def _reopen(self, incident: Incident, reason: str) -> None:
        await self.repo.increment_reopen(incident.id)
        await self.repo.update_incident(incident.id, status=IncidentStatus.REOPENED)
        await self.repo.set_next_check(incident.id, None)
        await self.repo.append_event(
            incident.id,
            EventType.INCIDENT_REOPENED,
            actor="verifier",
            payload={"reason": reason},
        )

    async def run_due_verifications(self) -> int:
        due = await self.repo.due_for_verification(utcnow())
        checked = 0
        for incident_id in due:
            try:
                async with self._lock(incident_id):
                    try:
                        await self._verify(incident_id, phase="stability")
                        checked += 1
                    except Exception:
                        log.exception("stability verification failed for %s", incident_id)
                        await self._fail(incident_id, "stability verification crashed")
            except LockBusy:
                self.telemetry.count(LOCK_BUSY_TOTAL, scope="scheduler")
                continue
        return checked

    # --------------------------------------------------------------------- misc
    async def retry(self, incident_id: str, *, wait: bool = False) -> Incident:
        incident = await self.repo.get_incident(incident_id)
        if incident.status not in {
            IncidentStatus.FAILED,
            IncidentStatus.REOPENED,
            IncidentStatus.CANCELLED,
        }:
            raise ConflictError(f"incident {incident_id} is {incident.status.value}, not retryable")
        await self.repo.update_incident(incident_id, status=IncidentStatus.INTAKE)
        await self.repo.set_next_check(incident_id, None)
        await self.repo.append_event(
            incident_id,
            EventType.NOTE,
            actor="operator",
            payload={"message": "re-investigation requested"},
        )
        if wait:
            await self._start_pipeline(incident_id)
        else:
            self._spawn_or_start(incident_id)
        return await self.repo.get_incident(incident_id)


__all__ = ["BudgetExceeded", "Forbidden", "IncidentService", "NotFoundError", "ServiceError"]
