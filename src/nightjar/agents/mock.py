"""Deterministic reference runtime.

This is not a fake: it is a rules-based responder used as the CI baseline and as
the fallback when no model provider is configured. It consumes exactly the same
typed evidence as the model-backed runtime, and it cannot execute actions
itself. Model providers are measured against this baseline, not the other way
around.
"""

from __future__ import annotations

from typing import Any

from nightjar.agents.types import (
    AgentOutcome,
    CritiqueInput,
    InvestigationInput,
    InvestigationOutput,
    PlanInput,
    ProbeRequest,
    TriageInput,
    claim,
    classify_text,
)
from nightjar.core.enums import (
    ActionType,
    CritiqueVerdict,
    IncidentClass,
    PredicateKind,
    RiskClass,
)
from nightjar.core.models import (
    ActionPlan,
    ActionSpec,
    ClaimVerdict,
    Critique,
    EvidenceItem,
    Finding,
    TriageResult,
    VerificationPredicate,
)


def _outcome(output: Any) -> AgentOutcome[Any]:
    return AgentOutcome(
        output=output,
        provider="mock",
        model="rules-v1",
        latency_ms=1,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )


def _by_source(evidence: list[EvidenceItem], prefix: str) -> EvidenceItem | None:
    for item in evidence:
        if item.source == prefix or item.source.startswith(prefix):
            return item
    return None


def _http(url: str, description: str, *, consecutive: int = 5) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.HTTP,
        description=description,
        params={
            "url": url,
            "expected_status": 200,
            "consecutive": consecutive,
            "interval_seconds": 0.05,
        },
        required=True,
    )


def _json(url: str, path: str, equals: Any, description: str) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.JSON,
        description=description,
        params={"url": url, "path": path, "equals": equals},
        required=True,
    )


def _connector(check: str, description: str, params: dict[str, Any]) -> VerificationPredicate:
    return VerificationPredicate(
        kind=PredicateKind.CONNECTOR_CHECK,
        description=description,
        params={"check": check, **params},
        required=True,
    )


class MockAgentRuntime:
    async def triage(self, payload: TriageInput) -> AgentOutcome[TriageResult]:
        incident = payload.incident
        incident_class = incident.incident_class
        title = incident.title
        if incident_class is IncidentClass.UNKNOWN:
            incident_class, title = classify_text(incident.title, incident.description)
        summary = (
            f"{title}. Classified as {incident_class.value.replace('_', ' ')} with "
            f"{len(incident.metadata)} intake attributes."
        )
        return _outcome(
            TriageResult(
                incident_class=incident_class,
                severity=incident.severity,
                title=incident.title,
                summary=summary,
                affected_assets=[
                    str(incident.metadata.get(key))
                    for key in ("service", "repository", "message_id", "credential_id")
                    if incident.metadata.get(key)
                ],
                confidence=0.9 if incident_class is not IncidentClass.UNKNOWN else 0.4,
                suggested_checks=["environment state", "recent changes", "customer impact"],
            )
        )

    async def investigate(self, payload: InvestigationInput) -> AgentOutcome[InvestigationOutput]:
        incident = payload.incident
        evidence = payload.evidence
        incident_class = payload.triage.incident_class

        if incident_class is IncidentClass.SITE_OUTAGE:
            state = _by_source(evidence, "lab.site_state")
            if state is None:
                return _outcome(
                    InvestigationOutput(
                        probes=[
                            ProbeRequest(
                                tool="lab.site_state",
                                params={},
                                reason="Site status and checkout reachability are unknown.",
                            )
                        ],
                        note="Need the current site state before forming a finding.",
                    )
                )
            mode = str(state.content.get("mode", "unknown"))
            homepage = state.content.get("homepage_status")
            checkout = state.content.get("checkout_status")
            if mode == "cache_poisoned":
                title = "A poisoned CDN cache entry is serving 5xx responses"
                detail = (
                    f"Homepage returned {homepage}, checkout returned {checkout}, and the edge "
                    "response reports a stale cache hit. The application version is healthy."
                )
            elif mode == "bad_deploy":
                title = "The current deployment is faulty"
                detail = (
                    f"Homepage returned {homepage} immediately after deploy "
                    f"{state.content.get('deploy_version')}; the previous good release is "
                    f"{state.content.get('previous_good')}."
                )
            elif mode == "db_down":
                title = "The checkout dependency is down while the homepage stays up"
                detail = (
                    f"Homepage returned {homepage} but checkout returned {checkout}. This is a "
                    "single dependency failure, not a site-wide edge problem."
                )
            else:
                title = "The site is currently healthy"
                detail = "All probes returned success. The failure may have been transient."
            return _outcome(
                InvestigationOutput(
                    findings=[
                        Finding(
                            title=title,
                            detail=detail,
                            confidence=0.93 if mode != "unknown" else 0.5,
                            claims=[
                                claim(
                                    f"{title}. Observed mode={mode}, homepage={homepage}, "
                                    f"checkout={checkout}.",
                                    0.93,
                                    [state.id],
                                )
                            ],
                            author="investigator",
                        )
                    ],
                    note="Root cause is supported by a live state probe and HTTP responses.",
                )
            )

        if incident_class is IncidentClass.CREDENTIAL_EXPOSURE:
            credential = _by_source(evidence, "lab.credential:")
            if credential is None:
                credential_id = incident.metadata.get("credential_id")
                if credential_id:
                    return _outcome(
                        InvestigationOutput(
                            probes=[
                                ProbeRequest(
                                    tool="lab.credential",
                                    params={"credential_id": str(credential_id)},
                                    reason="The credential state is unknown.",
                                )
                            ],
                            note="The exposed credential state is unknown.",
                        )
                    )
                return _outcome(
                    InvestigationOutput(
                        probes=[
                            ProbeRequest(
                                tool="lab.credentials",
                                params={},
                                reason="The alert supplied no credential identifier.",
                            )
                        ]
                    )
                )
            status = str(credential.content.get("status", "unknown"))
            provider = credential.content.get("provider", "unknown")
            preview = credential.content.get("preview", "redacted")
            if status == "active":
                title = f"The exposed {provider} credential is still active"
                detail = (
                    f"Credential {credential.content.get('id')} ({preview}) is active and was "
                    f"last used at {credential.content.get('last_used')}. Exposure is confirmed."
                )
                confidence = 0.95
            else:
                title = "The exposed credential has already been revoked"
                detail = (
                    f"Credential {credential.content.get('id')} is {status}; exposure is contained."
                )
                confidence = 0.8
            return _outcome(
                InvestigationOutput(
                    findings=[
                        Finding(
                            title=title,
                            detail=detail,
                            confidence=confidence,
                            claims=[
                                claim(
                                    f"Credential {credential.content.get('id')} is {status}.",
                                    confidence,
                                    [credential.id],
                                )
                            ],
                        )
                    ],
                )
            )

        if incident_class is IncidentClass.PHISHING:
            message = _by_source(evidence, "lab.mail:")
            reputation = _by_source(evidence, "lab.mail_reputation:")
            probes: list[ProbeRequest] = []
            message_id = incident.metadata.get("message_id")
            if message is None and message_id:
                probes.append(
                    ProbeRequest(
                        tool="lab.mail_message",
                        params={"message_id": str(message_id)},
                        reason="The reported message has not been read yet.",
                    )
                )
            if reputation is None and message_id:
                probes.append(
                    ProbeRequest(
                        tool="lab.mail_reputation",
                        params={"message_id": str(message_id)},
                        reason="Sender authentication and domain age are unknown.",
                    )
                )
            if probes:
                return _outcome(
                    InvestigationOutput(
                        probes=probes, note="Gathering message and sender evidence."
                    )
                )
            if message is None:
                return _outcome(
                    InvestigationOutput(
                        note="No message identifier was supplied; phishing analysis cannot proceed."
                    )
                )
            spf = message.content.get("spf", "unknown")
            dkim = message.content.get("dkim", "unknown")
            links = message.content.get("links", [])
            domain_age = reputation.content.get("domain_age_days") if reputation else None
            title = "Spoofed sender delivered a credential-harvesting link"
            detail = (
                f"Sender {message.content.get('sender')} failed SPF ({spf}) with DKIM {dkim}. "
                f"The domain is {domain_age} days old and the message links to {links}."
            )
            ids = [message.id] + ([reputation.id] if reputation else [])
            return _outcome(
                InvestigationOutput(
                    findings=[
                        Finding(
                            title=title,
                            detail=detail,
                            confidence=0.91,
                            claims=[
                                claim(
                                    "Sender authentication failed and the linked domain is "
                                    "newly registered, consistent with phishing.",
                                    0.91,
                                    ids,
                                )
                            ],
                        )
                    ],
                )
            )

        return _outcome(
            InvestigationOutput(
                note="No playbook matches this incident class.",
            )
        )

    async def critique(self, payload: CritiqueInput) -> AgentOutcome[Critique]:
        known = {item.id for item in payload.evidence}
        verdicts: list[ClaimVerdict] = []
        missing: list[str] = []
        for finding in payload.findings:
            for item in finding.claims:
                cited = [evidence_id for evidence_id in item.evidence_ids if evidence_id in known]
                if not item.evidence_ids:
                    verdict = CritiqueVerdict.UNSUPPORTED
                    reason = "The claim cites no evidence."
                elif len(cited) == len(item.evidence_ids):
                    verdict = CritiqueVerdict.SUPPORTED
                    reason = "Every cited evidence item exists in the ledger."
                else:
                    verdict = CritiqueVerdict.PARTIAL
                    reason = "Some cited evidence is not in the ledger."
                verdicts.append(ClaimVerdict(claim_id=item.id, verdict=verdict, reason=reason))
        if payload.findings:
            missing.extend(
                [
                    "no independent deployment history was checked"
                    if payload.incident.incident_class is IncidentClass.SITE_OUTAGE
                    else "no second source corroborates the finding"
                ]
            )
        if not verdicts:
            overall = CritiqueVerdict.UNSUPPORTED
        elif any(v.verdict is CritiqueVerdict.PARTIAL for v in verdicts):
            overall = CritiqueVerdict.PARTIAL
        elif all(v.verdict is CritiqueVerdict.SUPPORTED for v in verdicts):
            overall = CritiqueVerdict.SUPPORTED
        else:
            overall = CritiqueVerdict.UNSUPPORTED
        return _outcome(
            Critique(
                verdicts=verdicts,
                missing_evidence=missing,
                overall=overall,
                confidence_delta=-0.05 if missing else 0.0,
                notes="Deterministic citation check.",
            )
        )

    async def plan(self, payload: PlanInput) -> AgentOutcome[ActionPlan]:
        incident = payload.incident
        evidence = payload.evidence
        incident_class = payload.triage.incident_class
        findings = payload.findings

        if incident_class is IncidentClass.SITE_OUTAGE:
            state = _by_source(evidence, "lab.site_state")
            if state is None or not findings:
                return _outcome(
                    ActionPlan(
                        summary="No safe remediation can be planned without site evidence.",
                        root_cause="Unknown",
                        confidence=0.0,
                    )
                )
            mode = str(state.content.get("mode"))
            service = str(incident.metadata.get("service", "cornershop-web"))
            predicates = [
                _http(
                    payload.environment.site_url,
                    "Storefront returns 5xx free responses",
                    consecutive=5,
                ),
                _json(
                    payload.environment.checkout_url,
                    "status",
                    "ok",
                    "Checkout completes successfully",
                ),
            ]
            if mode == "bad_deploy":
                target = str(
                    state.content.get("previous_good")
                    or incident.metadata.get("previous_good")
                    or "1.4.2"
                )
                actions = [
                    ActionSpec(
                        action_type=ActionType.ROLLBACK_DEPLOYMENT,
                        target=service,
                        params={
                            "connector": "lab",
                            "service": service,
                            "target_version": target,
                        },
                        risk=RiskClass.R2,
                        description=f"Roll {service} back to {target}.",
                        rollback="Re-deploy the failed version after a fix.",
                        predicate_ids=[p.id for p in predicates],
                        evidence_ids=[state.id],
                    )
                ]
                predicates.append(
                    _json(
                        payload.environment.state_url,
                        "deploy_version",
                        target,
                        f"Running version is {target}",
                    )
                )
                root = str(findings[0].title)
                rollback_plan = (
                    f"Re-deploy {service} at the failed version if the rollback regresses."
                )
            elif mode == "db_down":
                actions = [
                    ActionSpec(
                        action_type=ActionType.RESTART_SERVICE,
                        target=service,
                        params={"connector": "lab", "service": service},
                        risk=RiskClass.R2,
                        description="Restart the checkout service to recover its database pool.",
                        rollback="Restart again if the dependency does not recover.",
                        predicate_ids=[p.id for p in predicates],
                        evidence_ids=[state.id],
                    )
                ]
                root = str(findings[0].title)
                rollback_plan = "Restarting is idempotent; a second failure requires DB failover."
            else:
                actions = [
                    ActionSpec(
                        action_type=ActionType.PURGE_CACHE,
                        target=service,
                        params={
                            "connector": "lab",
                            "zone": "cornershop",
                            "urls": [payload.environment.site_url],
                        },
                        risk=RiskClass.R1,
                        description="Purge the poisoned storefront cache entries.",
                        rollback="Cache repopulates from origin; no destructive change.",
                        predicate_ids=[p.id for p in predicates],
                        evidence_ids=[state.id],
                    )
                ]
                root = str(findings[0].title)
                rollback_plan = "If purging does not help, escalate to a deployment rollback."
            return _outcome(
                ActionPlan(
                    summary=f"Remediate {root.lower()}",
                    root_cause=root,
                    confidence=min(0.97, max(finding.confidence for finding in findings)),
                    actions=actions,
                    predicates=predicates,
                    blast_radius=[service],
                    rollback_plan=rollback_plan,
                )
            )

        if incident_class is IncidentClass.CREDENTIAL_EXPOSURE:
            credential = _by_source(evidence, "lab.credential:")
            if credential is None or not findings:
                return _outcome(
                    ActionPlan(
                        summary="No remediation without credential state.",
                        root_cause="Unknown",
                        confidence=0.0,
                    )
                )
            credential_id = str(credential.content.get("id", ""))
            if credential.content.get("status") != "active":
                return _outcome(
                    ActionPlan(
                        summary="The exposed credential is already revoked.",
                        root_cause=str(findings[0].title),
                        confidence=0.85,
                        predicates=[
                            _connector(
                                "credential_revoked",
                                "Exposed credential is rejected",
                                {"credential_id": credential_id},
                            )
                        ],
                    )
                )
            predicates = [
                _connector(
                    "credential_revoked",
                    "The exposed credential returns 401",
                    {"credential_id": credential_id},
                ),
                _connector(
                    "credential_works",
                    "The replacement credential is valid",
                    {"credential_id": "$result.new_credential_id"},
                ),
            ]
            action = ActionSpec(
                action_type=ActionType.REVOKE_CREDENTIAL,
                target=credential_id,
                params={"connector": "lab", "credential_id": credential_id},
                risk=RiskClass.R2,
                description=f"Revoke exposed credential {credential_id} and issue a replacement.",
                rollback="Issuing a new credential is irreversible; the old key must stay dead.",
                predicate_ids=[p.id for p in predicates],
                evidence_ids=[credential.id],
            )
            return _outcome(
                ActionPlan(
                    summary="Revoke the live credential and confirm the replacement works.",
                    root_cause=str(findings[0].title),
                    confidence=max(finding.confidence for finding in findings),
                    actions=[action],
                    predicates=predicates,
                    blast_radius=[f"credential:{credential_id}"],
                    rollback_plan="Rotate again and audit provider logs for use after revocation.",
                )
            )

        if incident_class is IncidentClass.PHISHING:
            message = _by_source(evidence, "lab.mail:")
            if message is None or not findings:
                return _outcome(
                    ActionPlan(
                        summary="No remediation without message evidence.",
                        root_cause="Unknown",
                        confidence=0.0,
                    )
                )
            message_id = str(message.content.get("id", ""))
            sender = str(message.content.get("sender", ""))
            predicates = [
                _connector(
                    "message_quarantined",
                    "The reported message is quarantined",
                    {"message_id": message_id},
                ),
                _connector(
                    "sender_blocked",
                    "A test message from the sender is rejected",
                    {"sender": sender},
                ),
            ]
            actions = [
                ActionSpec(
                    action_type=ActionType.QUARANTINE_MESSAGE,
                    target=message_id,
                    params={"connector": "lab", "message_id": message_id},
                    risk=RiskClass.R1,
                    description="Quarantine the reported message for all recipients.",
                    rollback="Release the message from quarantine if it is reclassified.",
                    predicate_ids=[p.id for p in predicates],
                    evidence_ids=[message.id],
                ),
                ActionSpec(
                    action_type=ActionType.BLOCK_SENDER,
                    target=sender,
                    params={"connector": "lab", "sender": sender},
                    risk=RiskClass.R2,
                    description=f"Block {sender} at the mail gateway.",
                    rollback="Remove the sender from the blocklist if it is a false positive.",
                    predicate_ids=[p.id for p in predicates],
                    evidence_ids=[message.id],
                ),
            ]
            return _outcome(
                ActionPlan(
                    summary="Contain the phishing campaign at the mail gateway.",
                    root_cause=str(findings[0].title),
                    confidence=max(finding.confidence for finding in findings),
                    actions=actions,
                    predicates=predicates,
                    blast_radius=[f"sender:{sender}"],
                    rollback_plan="Remove the block and release quarantined mail after review.",
                )
            )

        return _outcome(
            ActionPlan(
                summary="No supported remediation for this incident class.",
                root_cause="Unknown",
                confidence=0.0,
            )
        )


__all__ = ["MockAgentRuntime"]
