# Provider-agnostic agent roles

- Status: accepted
- Date: 2026-11-10
- Deciders: Nightjar maintainers

## Context

Model providers change quarterly: pricing, latency, tool-calling behaviour, context
limits, and availability. A product that assumes one provider either locks
customers in or breaks when that provider changes. Agent frameworks add another
layer of churn.

At the same time, "provider-agnostic" often means an abstraction so thin that
quality depends on which provider is configured — exactly the outcome we want to
avoid.

## Decision

Four typed roles — triage, investigator, critic, remediation planner — are defined
by input/output models and prompts, not by a provider SDK. A runtime interface
executes a role and returns a validated result plus token/cost metadata.

- `MockAgentRuntime` is deterministic, rules-based, and used by CI and the
  evaluation baseline. It implements the same interface as any model runtime.
- `PydanticAgentRuntime` wraps PydanticAI V2 and adapts OpenAI, Anthropic, Google,
  and Ollama.
- Provider and model are configuration (`NIGHTJAR_MODEL_PROVIDER`,
  `NIGHTJAR_MODEL_NAME`), including a separate critic provider/model.
- No domain module imports a provider SDK. No test depends on a live provider.
- Outputs are validated against the role schema before use; invalid output fails
  closed.

Quality is held constant by the control plane, not by the provider: the action
catalogue, policy, verification predicates, critic role, and evaluation suite are
identical for every provider. The provider changes the prose and the diagnosis
quality; it cannot change what is allowed to execute or what counts as resolved.

## Consequences

- Adding a provider is a factory entry plus an optional dependency extra, not a
  refactor.
- The evaluation suite can compare providers on the same cases with the same
  gates.
- The abstraction limits provider-specific features (e.g. native code
  interpreters). We accept that for portability and safety.
- The mock runtime is a first-class citizen, which keeps the product runnable and
  testable with no API key.

## Alternatives considered

- **Direct SDK calls in each role.** Rejected: provider logic leaks everywhere.
- **A hosted model gateway.** Rejected: contradicts self-hosting and adds a
  dependency the operator cannot audit.
- **One "best" provider.** Rejected: forces a choice the operator should own.
