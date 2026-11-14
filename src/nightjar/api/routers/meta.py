from __future__ import annotations

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nightjar import __version__
from nightjar.api import schemas
from nightjar.api.deps import get_service, require_auth
from nightjar.api.service import IncidentService
from nightjar.connectors.registry import ConnectorRegistry
from nightjar.core.actions import ACTION_RISK
from nightjar.core.capabilities import DoctorReport, assess

router = APIRouter(prefix="/v1", tags=["meta"], dependencies=[Depends(require_auth)])

# Preflights are one cheap GET per provider, but the UI asks on every page load.
# Cache for a short window so a dashboard refresh cannot become an API amplifier.
_PROBE_TTL_SECONDS = 30.0
_probe_cache: dict[int, tuple[float, DoctorReport]] = {}


@router.get("/meta", response_model=schemas.MetaInfo)
async def meta(
    service: Annotated[IncidentService, Depends(get_service)],
) -> schemas.MetaInfo:
    return schemas.MetaInfo(
        name="Nightjar",
        version=__version__,
        provider=service.settings.model_provider,
        model=service.settings.model_name,
        connectors=sorted(connector.name for connector in service.registry.connectors),
        tools=service.registry.tools(),
        action_catalog={action.value: risk.value for action, risk in ACTION_RISK.items()},
    )


async def _probe(registry: ConnectorRegistry, environment: str) -> DoctorReport:
    key = id(registry)
    cached = _probe_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _PROBE_TTL_SECONDS:
        return cached[1]
    reports = await asyncio.gather(*(connector.preflight() for connector in registry.connectors))
    report = assess(registry.connectors, list(reports), environment=environment)
    _probe_cache[key] = (now, report)
    return report


@router.get("/meta/capabilities", response_model=schemas.CapabilitiesResponse)
async def capabilities(
    service: Annotated[IncidentService, Depends(get_service)],
    probe: Annotated[
        bool,
        Query(description="ask each provider whether it can work right now"),
    ] = True,
) -> schemas.CapabilitiesResponse:
    if probe:
        report = await _probe(service.registry, service.settings.environment)
    else:
        report = assess(
            service.registry.connectors,
            [],
            environment=service.settings.environment,
            probed=False,
        )
    return schemas.CapabilitiesResponse(
        ok=report.ok,
        environment=report.environment,
        probed=probe,
        providers=[
            schemas.ProviderHealth(
                provider=item.provider,
                verdict=item.verdict,
                identity=item.identity,
                detail=item.detail,
                missing_scopes=list(item.missing_scopes),
                latency_ms=item.latency_ms,
            )
            for item in report.providers
        ],
        actions=[
            schemas.ActionCapabilityView(
                action_type=action.action_type.value,
                provider=action.provider,
                automated=action.automated,
                target_kinds=list(action.target_kinds),
                notes=action.notes,
                verdict=action.verdict,
            )
            for action in report.actions
        ],
        missing=[action.value for action in report.missing],
        problems=list(report.problems),
    )


__all__ = ["router"]
