# Policy as code, outside the model

- Status: accepted
- Date: 2026-11-10
- Deciders: Nightjar maintainers

## Context

An agent that can revoke credentials, roll back deployments, and block senders
needs a hard boundary between "the model proposed this" and "this is allowed."
Prompt instructions are not a boundary: they can be overridden by injection, model
upgrades, or a bug. Approval prompts shown to a tired operator are not a boundary
either if the default is to approve.

We need a deterministic decision procedure that is auditable, testable, and
identical regardless of which model produced the plan.

## Decision

Every mutating action passes a policy gate that returns one of `allow`, `approve`,
or `deny`, plus reasons, matched rule ids, and required approval count.

- Risk classes are fixed per action type: R0 read-only, R1 reversible/no customer
  impact, R2 reversible with customer impact, R3 irreversible.
- Defaults: R0 allow; R1 allow at confidence ≥ 0.70 with blast radius ≤ 1; R2
  require one approval; R3 deny unless break-glass with two approvals.
- No evidence, confidence below 0.50, or an active change freeze denies all
  mutating actions.
- Policy errors deny.
- The reference policy is Rego (`policies/nightjar.rego`). A local Python engine
  mirrors it exactly so Nightjar runs without OPA.
- Parity is enforced by tests over the same input matrix: decision, reasons,
  matched rules, and required approvals must match. Denied actions suppress
  approval reasons and rules.

## Consequences

- Two implementations must be kept in sync. Parity tests are mandatory and run in
  CI alongside `opa test`.
- Policy changes are code changes with review, tests, and evaluation, not prompt
  edits.
- The model cannot be given "emergency" authority. Urgency is expressed through R3
  break-glass, which is explicit and logged.
- Approval UX must show the exact rule that triggered it.

## Alternatives considered

- **Prompt-only guardrails.** Rejected: not enforceable.
- **Model-based policy classifier.** Rejected: non-deterministic and attackable.
- **OPA-only.** Rejected: adds a required service dependency for a small-team
  product. OPA remains supported for operators who want it.
