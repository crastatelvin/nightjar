# Temporal is optional, not required

- Status: accepted
- Date: 2026-11-10
- Deciders: Nightjar maintainers

## Context

The incident lifecycle includes a human approval that may take minutes and a
stability window that may take fifteen. Both need to survive a process restart. A
naive in-process scheduler loses pending approvals and stability checks on deploy,
which is unacceptable for the one workflow where durability matters most.

Temporal solves this properly. It also adds a server, a namespace, a task queue, a
worker deployment, and operational knowledge that a two-person team may not have.

## Decision

Ship two orchestrators behind one service:

- **Inline (default).** `IncidentService` runs the pipeline in-process, with a
  database-backed scheduler that polls for due stability checks and expires
  approvals. Simple to run; a restart during a pending approval is recoverable via
  the scheduler, and the incident state lives in PostgreSQL.
- **Temporal (optional).** A workflow owns the state machine with one-attempt
  activities for investigation, approval expiry, execution, and stability
  verification. Approval decisions signal the workflow. The API signals instead of
  executing inline when `NIGHTJAR_ORCHESTRATOR=temporal`.

Both paths call the same `IncidentService` methods. Activities are thin wrappers;
they do not reimplement policy, execution, or verification.

## Consequences

- The default installation is a database and two processes (API, optional worker),
  not a cluster.
- Durable mode requires a Temporal server and `nightjar-worker`. Compose provides
  it; it is not mandatory.
- Two orchestration paths must stay behaviourally identical. Tests cover both, and
  a live end-to-end run verifies the approval signal path.
- Long stability windows are reliable in Temporal and best-effort with the inline
  scheduler. Operators who need strict durability should enable Temporal.

## Alternatives considered

- **Temporal required.** Rejected: too much operational weight for the target user.
- **Celery/Redis.** Rejected: another broker, weaker workflow semantics, and
  approval signalling is awkward.
- **In-process only.** Rejected: loses approvals and stability checks on restart.
