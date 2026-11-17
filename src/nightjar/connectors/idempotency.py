"""One key per (incident, plan, action, parameters), so a mutation happens once.

The failure this prevents is not hypothetical. An approval can be decided by two
requests at once, a worker can be retried after a timeout, and a replica can pick
up an incident whose lock expired. Each of those can call the same connector with
the same arguments. For ``purge_cache`` that is harmless; for ``rollback_deployment``
and ``restart_service`` it is a second outage, and for ``open_issue`` it is a
duplicate ticket an operator has to close.

The key is derived, not random, so the executor can compute it before doing
anything and ask the repository whether this exact mutation already succeeded.
The parameters are canonicalised (sorted keys, no whitespace) because a dict with
the same contents must produce the same key.

``plan_id`` is in the key on purpose. A reopened incident gets a new plan, and a
new plan must be allowed to act again — the fix has to be re-applied. Within one
plan, one success is the limit.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nightjar.core.models import ActionSpec


def canonical_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def action_key(incident_id: str, plan_id: str, action: ActionSpec) -> str:
    """A stable digest for one action in one plan of one incident.

    A planner may pin ``action.idempotency_key``; it is extra material rather than
    a replacement, so a pinned key still cannot leak across incidents or plans.
    """
    parts = [
        incident_id,
        plan_id,
        action.id,
        action.action_type.value,
        action.target,
        canonical_params(action.params),
    ]
    if action.idempotency_key:
        parts.append(f"pinned:{action.idempotency_key}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


__all__ = ["action_key", "canonical_params"]
