# Observability

Nightjar emits OpenTelemetry traces and metrics about the things an operator has to
be able to explain after an incident: which action ran, against which provider,
whether it succeeded, and whether the check that decided the outcome passed.

It is optional and off by default. The base install does not contain the SDK, and
every instrumentation point degrades to a no-op, so nothing here changes behaviour
when nobody is listening.

## Turning it on

```bash
uv sync --extra otel
export NIGHTJAR_OTEL_ENABLED=true
export NIGHTJAR_OTEL_ENDPOINT=http://127.0.0.1:4318   # OTLP/HTTP base URL
```

Enabling it without an endpoint is refused at startup. So is enabling it without
the extra installed: the process says `NIGHTJAR_OTEL_ENABLED requires the
OpenTelemetry SDK` rather than starting with an empty dashboard that looks like a
working one. The tracing and metrics exporters append `/v1/traces` and `/v1/metrics`
to the endpoint, so point it at the collector's HTTP receiver, not at a
vendor-specific path.

| Variable | Default | Notes |
| --- | --- | --- |
| `NIGHTJAR_OTEL_ENABLED` | `false` | requires the `otel` extra and an endpoint |
| `NIGHTJAR_OTEL_ENDPOINT` | — | OTLP/HTTP base, e.g. `http://collector:4318` |
| `NIGHTJAR_OTEL_SERVICE_NAME` | `nightjar` | `service.name` resource attribute |
| `NIGHTJAR_OTEL_SAMPLE_RATIO` | `1.0` | head sampling ratio, `0 < r <= 1` |

Both the API and the Temporal worker export, because both run the service.

## What is exported

### Spans

| Span | Attributes |
| --- | --- |
| `nightjar.action` | `incident_id`, `action_type`, `provider`, `target`, `risk`, `outcome`, `attempts`, `error` |
| `nightjar.verification` | `incident_id`, `predicate_id`, `kind`, `required`, `phase`, `passed`, `error` |

`outcome` is one of `executed`, `replayed` (the idempotency key said this mutation
already happened in this plan), `manual` (the provider has no API, so a human was
asked), or `failed`. A span that ends in an exception carries the exception event
and an `ERROR` status; an action failure that Nightjar handled is recorded as
`outcome=failed` with the exception's type in `error`, because the handler swallowed
it deliberately.

`provider` is `unbound` when no connector could be resolved. That is itself useful:
a plan that failed before approval shows up as an action span with no provider.

### Metrics

| Metric | Labels |
| --- | --- |
| `nightjar_action_total` | `provider`, `action`, `outcome` |
| `nightjar_verification_total` | `kind`, `required`, `passed` |
| `nightjar_lock_busy_total` | `scope` (`incident`, `scheduler`) |
| `nightjar_doctor_verdict` | `provider`, `verdict` |

`nightjar_doctor_verdict` is recorded by `nightjarctl doctor`, so it answers "when
did the CDN stop being healthy" rather than "is it healthy now".

**No secrets, no prompts, no evidence content.** Attributes are an explicit
allowlist of identifiers and enum values. The model prompt, evidence bodies, and
credential values never reach a span or a metric label; a target is the managed
resource name the operator configured, not a URL with a token in it.

## Service objectives

These are the numbers the instrumentation is meant to make checkable, not
guarantees the product enforces:

| Objective | Target | Measured from |
| --- | --- | --- |
| Action duration | p95 < 5 s, excluding provider propagation | `nightjar.action` span duration |
| Verification duration | p95 < 10 s | `nightjar.verification` span duration |
| Verification consistency | < 1% of required checks flip pass→fail inside a stability window | recorded verifications in the case file (not a metric label: predicate ids are unbounded) |
| Case-file stream latency | p95 < 150 ms from event to client | span timestamp on `nightjar.action` vs. stream receive, or the SSE route's own timing |
| Lock contention | < 1% of pipeline runs refused by the lock | `nightjar_lock_busy_total` over runs |

A failing action is not an SLO breach; a failing action that takes four minutes is.
Separate the two when alerting.

## A local collector

The quickest way to see a span end to end is the collector's debug exporter:

```yaml
# otel-collector.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  debug:
    verbosity: detailed
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
    metrics:
      receivers: [otlp]
      exporters: [debug]
```

```bash
docker run --rm -p 4318:4318 \
  -v "$PWD/otel-collector.yaml:/etc/otelcol/config.yaml" \
  otel/opentelemetry-collector:latest
```

File an incident, then look for `nightjar.action` in the collector's output. With
`NIGHTJAR_OTEL_ENABLED=true` and a collector that is down, Nightjar keeps working:
export failures are the collector's problem, never the incident's.

## What is deliberately not here

- No log shipping. Nightjar logs to stdout as JSON; your platform collects it.
- No tracing of model prompts or completions. An agent run is recorded in the case
  file, with its provider, model, tokens, and cost — the case file is the audited
  record, and prompts can contain evidence.
- No per-tenant metrics. v1 is one deployment for one organization; a tenant label
  would be a label with one value.
