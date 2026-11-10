"""``nightjar-worker``: run Temporal activities and workflows."""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from nightjar.api.settings import get_settings
from nightjar.worker.activities import (
    execute_and_verify,
    expire_approvals,
    investigate_and_plan,
    verify_stability,
)
from nightjar.worker.client import get_temporal_client
from nightjar.worker.workflow import IncidentWorkflow

log = logging.getLogger(__name__)


async def _run() -> None:
    settings = get_settings()
    client = await get_temporal_client()
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[IncidentWorkflow],
        activities=[
            investigate_and_plan,
            execute_and_verify,
            verify_stability,
            expire_approvals,
        ],
    )
    log.info(
        "nightjar worker listening on namespace %s queue %s",
        settings.temporal_namespace,
        settings.temporal_task_queue,
    )
    await worker.run()


def run() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover - operator stop
        log.info("nightjar worker stopped")


__all__ = ["run"]
