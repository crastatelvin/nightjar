"""Evaluation harness.

Runs the real service against a private CornerShop lab, approves actions as an
operator would, and scores the resulting case files. Nothing here bypasses the
service: the evaluator only uses the public HTTP surface.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from nightjar import __version__
from nightjar.api import tables as _tables  # noqa: F401 - registers the schema
from nightjar.api.db import Base
from nightjar.api.main import create_app
from nightjar.api.settings import Settings
from nightjar.evals.models import CaseObservation, CaseScore, EvalCase, EvalSummary
from nightjar.evals.scorer import TERMINAL_STATUSES, score_case

DEFAULT_EVAL_DATABASE_URL = "postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar_eval"
SEED_FIELDS = ("title", "description", "incident_class", "severity", "source", "metadata")


class RunBudgetExceeded(RuntimeError):
    """A run-level cost cap stopped the harness between cases.

    The per-incident ceiling bounds one case; this bounds the bill for a whole
    nightly run. It is checked before a case starts, so the overshoot is at most
    the cost of the case that was already running.
    """

    def __init__(self, *, spent_usd: float, cap_usd: float, next_case: str) -> None:
        super().__init__(
            f"eval run spent ${spent_usd:.2f} against a ${cap_usd:.2f} cap before {next_case}"
        )
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd
        self.next_case = next_case


async def _ensure_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def ensure_schema(database_url: str) -> None:
    asyncio.run(_ensure_schema(database_url))


def evaluation_settings(
    *,
    database_url: str,
    lab_url: str,
    stability_seconds: int,
    provider: str,
    model: str,
) -> Settings:
    return Settings(
        environment="test",
        log_level="WARNING",
        tenant_id=str(uuid.uuid4()),
        tenant_name=f"eval-{uuid.uuid4().hex[:12]}",
        database_url=database_url,
        lab_base_url=lab_url,
        lab_enabled=True,
        model_provider=provider,
        model_name=model,
        critic_provider=provider,
        critic_model_name=model,
        stability_seconds=stability_seconds,
        approval_ttl_seconds=300,
        allow_private_probes=True,
        # The harness is the only client and drives hundreds of requests in a
        # burst; the API budget exists to protect against real callers, so it is
        # not in the way of measuring the product.
        api_rate_limit_enabled=False,
    )


def _approve_pending(client: TestClient, incident_id: str, seen: set[str]) -> str | None:
    """Approve everything pending, and report the last refusal.

    Only a decision the API actually recorded is remembered. The incident lock is
    held by the pipeline for part of its run, so an approval posted at the wrong
    moment gets ``409``; marking it seen would leave the case waiting for an
    approval nobody would ever send again, and the case would time out looking
    like a product failure.
    """
    case = client.get(f"/v1/incidents/{incident_id}").json()
    refusal: str | None = None
    for approval in case["approvals"]:
        if approval["status"] != "pending" or approval["id"] in seen:
            continue
        response = client.post(
            f"/v1/incidents/{incident_id}/approvals/{approval['id']}",
            json={"approved": True, "comment": "eval harness auto-approval"},
        )
        if response.status_code == 200:
            seen.add(approval["id"])
        else:
            refusal = f"{response.status_code} {response.text[:160]}"
    return refusal


def _environment(client: TestClient) -> dict[str, Any]:
    """What this deployment can do, read from the API rather than assumed.

    ``manual_capabilities`` is the independent source of truth for the
    manual-step metric: the scorer does not have to trust the ``manual`` flag the
    case file carries, it can check that every action whose bound provider is
    manual was flagged.
    """
    meta = client.get("/v1/meta").json()
    capabilities = client.get("/v1/meta/capabilities", params={"probe": "false"}).json()
    return {
        "connectors": list(meta.get("connectors") or []),
        "manual_capabilities": [
            action for action in capabilities.get("actions") or [] if not action.get("automated")
        ],
    }


def _observation_from_case(
    case: dict[str, Any], environment: dict[str, Any] | None = None
) -> CaseObservation:
    environment = environment or {}
    return CaseObservation(
        status=case["incident"]["status"],
        audit_healthy=True,
        audit_checks={},
        plan=case["plan"],
        actions=case["actions"],
        action_states=case["action_states"],
        approvals=case["approvals"],
        verifications=case["verifications"],
        evidence=case["evidence"],
        agent_runs=case["agent_runs"],
        chain=case["chain"],
        manual_steps=case.get("manual_steps") or [],
        connectors=list(environment.get("connectors") or []),
        manual_capabilities=list(environment.get("manual_capabilities") or []),
        approvals_granted=sum(
            1 for approval in case["approvals"] if approval["status"] == "approved"
        ),
    )


def run_case(
    client: TestClient,
    lab: httpx.Client,
    case: EvalCase,
    *,
    timeout: float = 45.0,
    environment: dict[str, Any] | None = None,
) -> CaseScore:
    environment = environment or _environment(client)
    lab.post("/lab/reset", timeout=10.0)
    seed_response = lab.post(
        f"/lab/scenario/{case.scenario}",
        params={
            "variant": case.variant,
            "relapse": str(case.relapse).lower(),
            "injection": str(case.injection).lower(),
        },
        timeout=10.0,
    )
    if seed_response.status_code != 200:
        observation = CaseObservation(
            status="failed",
            audit_healthy=False,
            audit_checks={},
            plan=None,
            actions=[],
            action_states={},
            approvals=[],
            verifications=[],
            evidence=[],
            agent_runs=[],
            chain={"evidence_valid": False, "timeline_valid": False},
            error=f"lab seed failed: {seed_response.status_code}",
        )
        return score_case(case, observation, latency_seconds=0.0)

    seed = seed_response.json()
    payload = {field: seed[field] for field in SEED_FIELDS}
    started = time.monotonic()
    response = client.post("/v1/incidents", params={"wait": "false"}, json=payload)
    if response.status_code != 201:
        observation = CaseObservation(
            status="failed",
            audit_healthy=False,
            audit_checks={},
            plan=None,
            actions=[],
            action_states={},
            approvals=[],
            verifications=[],
            evidence=[],
            agent_runs=[],
            chain={"evidence_valid": False, "timeline_valid": False},
            error=f"incident intake failed: {response.status_code} {response.text[:200]}",
        )
        return score_case(case, observation, latency_seconds=0.0)

    incident_id = response.json()["id"]
    seen: set[str] = set()
    status = "intake"
    timed_out = False
    refusal: str | None = None
    while True:
        case_file = client.get(f"/v1/incidents/{incident_id}").json()
        refusal = _approve_pending(client, incident_id, seen) or refusal
        status = case_file["incident"]["status"]
        if status in TERMINAL_STATUSES:
            break
        if time.monotonic() - started > timeout:
            timed_out = True
            break
        time.sleep(0.1)

    case_file = client.get(f"/v1/incidents/{incident_id}").json()
    audit = lab.get("/lab/audit", timeout=10.0).json()
    observation = _observation_from_case(case_file, environment)
    observation.audit_healthy = bool(audit.get("healthy"))
    observation.audit_checks = dict(audit.get("checks") or {})
    observation.timed_out = timed_out
    if timed_out:
        observation.error = f"timed out after {timeout:.0f}s in status {status!r}"
        if refusal:
            observation.error += f"; last approval refusal: {refusal}"
    return score_case(case, observation, latency_seconds=time.monotonic() - started)


def _report_score(score: CaseScore) -> None:
    verdict = "PASS" if score.outcome_correct and not score.false_resolution else "FAIL"
    flag = "  FALSE-RESOLUTION" if score.false_resolution else ""
    print(
        f"  {verdict}  {score.case_id:<42} {score.status:<10}{flag}",
        flush=True,
    )


def run_with_budget(
    cases: list[EvalCase],
    run_one: Callable[[EvalCase], CaseScore],
    *,
    max_cost_usd: float | None = None,
    spent_usd: float = 0.0,
    on_score: Callable[[CaseScore], None] | None = None,
) -> list[CaseScore]:
    """Run cases until the list ends or the run-level cap is reached.

    The check is between cases: the cost of a case is only known after the
    provider has answered, and a per-incident ceiling already bounds that
    overshoot. Starting a case after the cap is spent is what this prevents.
    """
    scores: list[CaseScore] = []
    spent = spent_usd
    for case in cases:
        if max_cost_usd is not None and spent >= max_cost_usd:
            raise RunBudgetExceeded(spent_usd=spent, cap_usd=max_cost_usd, next_case=case.id)
        score = run_one(case)
        scores.append(score)
        spent += float(score.cost_usd)
        if on_score is not None:
            on_score(score)
    return scores


def run_cases(
    cases: list[EvalCase],
    *,
    settings: Settings,
    lab_url: str,
    timeout: float = 45.0,
    max_cost_usd: float | None = None,
    spent_usd: float = 0.0,
) -> list[CaseScore]:
    with (
        httpx.Client(base_url=lab_url, timeout=10.0) as lab,
        TestClient(create_app(settings)) as client,
    ):
        environment = _environment(client)
        return run_with_budget(
            cases,
            lambda case: run_case(client, lab, case, timeout=timeout, environment=environment),
            max_cost_usd=max_cost_usd,
            spent_usd=spent_usd,
            on_score=_report_score,
        )


def run_by_stability_group(
    cases: list[EvalCase],
    *,
    database_url: str,
    lab_url: str,
    provider: str,
    model: str,
    timeout: float = 45.0,
    max_cost_usd: float | None = None,
) -> list[CaseScore]:
    groups: dict[int, list[EvalCase]] = {}
    for case in cases:
        groups.setdefault(case.stability_seconds, []).append(case)

    scored: dict[int, list[CaseScore]] = {}
    spent = 0.0
    for stability_seconds, group_cases in sorted(groups.items()):
        settings = evaluation_settings(
            database_url=database_url,
            lab_url=lab_url,
            stability_seconds=stability_seconds,
            provider=provider,
            model=model,
        )
        scored[stability_seconds] = run_cases(
            group_cases,
            settings=settings,
            lab_url=lab_url,
            timeout=timeout,
            max_cost_usd=max_cost_usd,
            spent_usd=spent,
        )
        spent += sum(score.cost_usd for score in scored[stability_seconds])

    results: list[CaseScore] = []
    for case in cases:
        for group in scored.values():
            for score in group:
                if score.case_id == case.id:
                    results.append(score)
                    break
            else:
                continue
            break
    return results


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def summarize(
    scores: list[CaseScore],
    *,
    provider: str,
    model: str,
    suite: str,
) -> EvalSummary:
    count = len(scores)
    outcomes = sum(1 for score in scores if score.outcome_correct)
    false_resolutions = sum(1 for score in scores if score.false_resolution)
    resolved = sum(1 for score in scores if score.status == "resolved")
    reopened = sum(1 for score in scores if score.status == "reopened")
    expected_resolved = sum(1 for score in scores if score.expected_outcome == "resolved")
    verified = sum(1 for score in scores if score.verified)
    injection_cases = sum(1 for score in scores if score.injection)
    injection_resistant = sum(
        1 for score in scores if score.injection and score.injection_resistant
    )
    policy_violations = sum(len(score.policy_violations) for score in scores)
    policy_violating_cases = sum(1 for score in scores if score.policy_violations)
    chain_valid = sum(1 for score in scores if score.chain_valid)
    action_matches = sum(1 for score in scores if score.action_match)
    approval_checks = sum(1 for score in scores if score.approvals_ok)
    unavailable_cases = sum(1 for score in scores if score.unavailable_actions)
    manual_visible = sum(1 for score in scores if score.manual_actions_visible)
    latencies = [score.latency_seconds for score in scores]
    costs = [score.cost_usd for score in scores]
    input_tokens = [score.input_tokens for score in scores]
    output_tokens = [score.output_tokens for score in scores]
    evidence_counts = [score.evidence_count for score in scores]

    by_suite: dict[str, dict[str, int]] = {}
    for suite_name in dict.fromkeys(score.suite for score in scores):
        group = [score for score in scores if score.suite == suite_name]
        by_suite[suite_name] = {
            "cases": len(group),
            "outcome_correct": sum(1 for score in group if score.outcome_correct),
            "false_resolutions": sum(1 for score in group if score.false_resolution),
            "policy_violations": sum(len(score.policy_violations) for score in group),
        }

    return EvalSummary(
        schema_version=1,
        suite=suite,
        nightjar_version=__version__,
        provider=provider,
        model=model,
        case_count=count,
        outcome_correct=outcomes,
        false_resolutions=false_resolutions,
        resolved_cases=resolved,
        expected_resolved=expected_resolved,
        reopened_cases=reopened,
        verified_cases=verified,
        injection_cases=injection_cases,
        injection_resistant=injection_resistant,
        policy_violations=policy_violations,
        policy_violating_cases=policy_violating_cases,
        chain_valid_cases=chain_valid,
        action_matches=action_matches,
        approval_checks=approval_checks,
        total_cost_usd=sum(costs),
        mean_cost_usd=sum(costs) / count if count else 0.0,
        mean_latency_seconds=sum(latencies) / count if count else 0.0,
        p95_latency_seconds=_percentile(latencies, 0.95),
        mean_input_tokens=sum(input_tokens) / count if count else 0.0,
        mean_output_tokens=sum(output_tokens) / count if count else 0.0,
        mean_evidence_count=sum(evidence_counts) / count if count else 0.0,
        accuracy=outcomes / count if count else 0.0,
        false_resolution_rate=false_resolutions / resolved if resolved else 0.0,
        verification_rate=verified / count if count else 0.0,
        injection_resistance_rate=injection_resistant / injection_cases if injection_cases else 1.0,
        chain_integrity_rate=chain_valid / count if count else 0.0,
        action_match_rate=action_matches / count if count else 0.0,
        approval_rate=approval_checks / count if count else 0.0,
        unavailable_action_rate=unavailable_cases / count if count else 0.0,
        manual_step_visibility=manual_visible / count if count else 1.0,
        by_suite=by_suite,
        cases=scores,
    )


__all__ = [
    "DEFAULT_EVAL_DATABASE_URL",
    "ensure_schema",
    "evaluation_settings",
    "run_by_stability_group",
    "run_case",
    "run_cases",
    "summarize",
]
