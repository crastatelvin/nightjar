# Adopting verification-first incident response

- Status: accepted
- Date: 2026-11-10
- Deciders: Nightjar maintainers

## Context

LLM incident agents are good at producing a plausible root cause and terrible at
knowing whether their fix worked. The common failure is optimistic closure: the
model proposes a remediation, the platform reports the action as succeeded, and the
incident is marked resolved while customers are still broken. Operators learn to
distrust the tool, and the tool's speed becomes a liability.

We need a product where "resolved" is a claim backed by independent observation,
not a model's opinion or an API's `200 OK`.

## Decision

An incident is resolved only when:

1. A stored, typed remediation plan exists.
2. At least one action from that plan executed.
3. Every required T+0 verification predicate passes.
4. Every required stability predicate passes after a configurable window.
5. The evidence and timeline hash chains verify.

Failure at any step reopens or fails the incident. Relapse is a first-class
outcome, not an error.

Verification predicates are plan-specific and executed by a deterministic verifier
that does not involve a model. Predicate results are recorded with the observed
values that produced them.

## Consequences

- Slower apparent resolution than tools that close on action success. That is the
  point.
- The plan must include predicates, which makes planning harder and the planner
  prompt more constrained.
- Relapse handling requires durable scheduling or a long-running workflow.
- The headline evaluation metric becomes false resolution rate, not accuracy.
- The UI must make "resolved" visually distinct from "action succeeded" and must
  keep a reopened incident visibly unsettled.

## Alternatives considered

- **Close on successful action.** Rejected: it is the failure mode we exist to fix.
- **Ask the model to verify its own fix.** Rejected: same model, same blind spots,
  and no independent evidence.
- **Single post-fix health check.** Rejected: catches immediate failure but not
  relapse, which is common for cache and deploy faults.
