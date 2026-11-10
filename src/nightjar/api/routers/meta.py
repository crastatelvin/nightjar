from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from nightjar import __version__
from nightjar.api import schemas
from nightjar.api.deps import get_service, require_auth
from nightjar.api.service import IncidentService
from nightjar.core.actions import ACTION_RISK

router = APIRouter(prefix="/v1", tags=["meta"], dependencies=[Depends(require_auth)])


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


__all__ = ["router"]
