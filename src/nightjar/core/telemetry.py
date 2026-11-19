"""Traces and metrics for the parts an operator has to be able to explain.

Telemetry is optional. The base install has no OpenTelemetry SDK, and every call
here degrades to a no-op, so importing this module never costs anything and never
fails. Turning it on without the ``otel`` extra is a startup error rather than a
silently empty dashboard: "the trace is missing" is the wrong thing to discover
during an incident.

The spans are deliberately few and named after the product's own steps, not after
HTTP calls:

* ``nightjar.action`` — one planned action, with the provider that performed it and
  the outcome (``executed``, ``replayed``, ``manual``, ``failed``).
* ``nightjar.verification`` — one predicate, with its kind and whether it passed.

Anything an operator would ask after the fact — which provider, which incident,
which predicate, did it pass — is an attribute, so it can be filtered in the
collector without correlating logs.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, not runtime
    from nightjar.api.settings import Settings

# Metric names are part of the operator contract: dashboards and alerts are
# written against them, so they are constants rather than strings at call sites.
ACTION_TOTAL = "nightjar_action_total"
VERIFICATION_TOTAL = "nightjar_verification_total"
LOCK_BUSY_TOTAL = "nightjar_lock_busy_total"
DOCTOR_VERDICT_TOTAL = "nightjar_doctor_verdict"


class SpanHandle(Protocol):
    """The part of a span this code uses. No-ops accept the same calls."""

    def set(self, **attributes: Any) -> None: ...


class _NoopSpan:
    def set(self, **attributes: Any) -> None:
        return None


class _Span:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set(self, **attributes: Any) -> None:
        for key, value in _attributes(attributes).items():
            self._span.set_attribute(key, value)


def _attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` and keep only types the OTel exporters accept."""
    accepted: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, bool | int | float | str):
            accepted[key] = value
    return accepted


class Telemetry:
    """A tracer, a meter, and a shutdown hook — or none of them."""

    def __init__(
        self,
        tracer: Any = None,
        meter: Any = None,
        *,
        providers: tuple[Any, ...] = (),
    ) -> None:
        self._tracer = tracer
        self._meter = meter
        self._providers = list(providers)
        self._counters: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[SpanHandle]:
        """A span that records an exception and marks the status, if one escapes."""
        if self._tracer is None:
            yield _NoopSpan()
            return
        with self._tracer.start_as_current_span(
            name, attributes=_attributes(attributes)
        ) as current:
            yield _Span(current)

    def count(self, name: str, amount: int = 1, **attributes: Any) -> None:
        if self._meter is None:
            return
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name, unit="1", description=name)
            self._counters[name] = counter
        counter.add(amount, _attributes(attributes))

    def shutdown(self) -> None:
        """Flush and stop the exporters. Called once, from the app lifespan."""
        for provider in self._providers:
            provider.shutdown()
        self._providers.clear()

    # ------------------------------------------------------------ constructors
    @classmethod
    def from_settings(cls, settings: Settings) -> Telemetry:
        if not settings.otel_enabled:
            return cls()
        if not settings.otel_endpoint:
            raise RuntimeError(
                "NIGHTJAR_OTEL_ENABLED requires NIGHTJAR_OTEL_ENDPOINT, "
                "for example http://127.0.0.1:4318"
            )
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError as exc:  # pragma: no cover - the extra is installed in CI
            raise RuntimeError(
                "NIGHTJAR_OTEL_ENABLED requires the OpenTelemetry SDK: "
                "install with `uv sync --extra otel`"
            ) from exc

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.environment,
            }
        )
        base = settings.otel_endpoint.rstrip("/")
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
        )
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"))
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        return cls(
            tracer_provider.get_tracer("nightjar"),
            meter_provider.get_meter("nightjar"),
            providers=(tracer_provider, meter_provider),
        )
