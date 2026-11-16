from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from nightjar import __version__
from nightjar.agents.runtime import build_runtime
from nightjar.api import db
from nightjar.api.locks import LockBusy, scheduler_leader
from nightjar.api.replay import PostgresReplayStore
from nightjar.api.repository import ConflictError, NotFoundError, Repository
from nightjar.api.routers import approvals, health, incidents, intake, meta
from nightjar.api.service import IncidentService
from nightjar.api.settings import Settings, get_settings
from nightjar.connectors.registry import build_registry
from nightjar.core.actions import ActionValidationError
from nightjar.core.enums import PredicateKind
from nightjar.core.policy import LocalPolicyEngine, OpaPolicyEngine
from nightjar.core.verification import ConnectorCheck, Verifier

log = logging.getLogger(__name__)


def build_service(settings: Settings) -> IncidentService:
    db.configure(settings.database_url, echo=settings.db_echo)
    repository = Repository(db.get_session_factory())
    runtime = build_runtime(settings)
    registry = build_registry(settings)
    policy = OpaPolicyEngine(settings.opa_url) if settings.opa_url else LocalPolicyEngine()
    verifier = Verifier(
        checks={PredicateKind.CONNECTOR_CHECK: ConnectorCheck(registry.verification_checks())},
        allow_private=settings.allow_private_probes,
    )
    return IncidentService(
        settings=settings,
        repository=repository,
        runtime=runtime,
        registry=registry,
        policy=policy,
        verifier=verifier,
        lock_engine=db.get_lock_engine(),
    )


def create_app(
    settings: Settings | None = None,
    service: IncidentService | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    if service is None:
        service = build_service(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.repo.ensure_tenant(resolved.tenant_id, resolved.tenant_name)
        lock_engine = service.lock_engine if resolved.db_locks else None
        scheduler = asyncio.create_task(
            _scheduler_loop(service, lock_engine), name="nightjar-stability"
        )
        try:
            yield
        finally:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
            await service.shutdown()
            await db.dispose()

    api = FastAPI(
        title="Nightjar API",
        version=__version__,
        description=(
            "Open-source incident response that proves it fixed the problem. "
            "Typed actions, deterministic policy, hash-chained evidence, independent verification."
        ),
        lifespan=lifespan,
    )
    api.state.settings = resolved
    api.state.service = service
    api.state.intake_replay = PostgresReplayStore(
        db.get_session_factory,
        tenant_id=resolved.tenant_id,
        ttl_seconds=max(resolved.intake_webhook_tolerance_seconds * 2, 600),
    )

    api.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @api.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @api.exception_handler(ActionValidationError)
    async def invalid_action(request: Request, exc: ActionValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @api.exception_handler(LockBusy)
    async def lock_busy(request: Request, exc: LockBusy) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
            headers={"retry-after": "2"},
        )

    api.include_router(health.router)
    api.include_router(incidents.router)
    api.include_router(approvals.router)
    api.include_router(meta.router)
    api.include_router(intake.router)
    return api


async def _scheduler_loop(service: IncidentService, lock_engine: AsyncEngine | None) -> None:
    while True:
        try:
            if lock_engine is None:
                await service.run_due_verifications()
            else:
                async with scheduler_leader(lock_engine) as leader:
                    if leader:
                        await service.run_due_verifications()
        except Exception:
            log.exception("stability scheduler iteration failed")
        await asyncio.sleep(2.0)


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


__all__ = ["build_service", "create_app", "run"]
