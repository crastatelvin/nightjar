"""Telemetry: the spans and counters an operator would look for, or none at all.

The default install has no OpenTelemetry SDK and must not care. These tests cover
both sides: the no-op path, which every other test in the suite exercises by
accident, and the real path, using in-memory exporters so nothing leaves the test
process.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nightjar.api.settings import Settings
from nightjar.core.telemetry import (
    ACTION_TOTAL,
    DOCTOR_VERDICT_TOTAL,
    LOCK_BUSY_TOTAL,
    VERIFICATION_TOTAL,
    Telemetry,
)


def _enabled() -> tuple[Telemetry, InMemorySpanExporter, InMemoryMetricReader]:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    telemetry = Telemetry(
        tracer_provider.get_tracer("nightjar"),
        meter_provider.get_meter("nightjar"),
        providers=(tracer_provider, meter_provider),
    )
    return telemetry, spans, reader


def test_disabled_telemetry_is_inert() -> None:
    telemetry = Telemetry()
    assert telemetry.enabled is False
    with telemetry.span("nightjar.action", incident_id="i-1", target=None) as span:
        span.set(outcome="executed")
    telemetry.count(ACTION_TOTAL, provider="lab")
    telemetry.count(LOCK_BUSY_TOTAL)
    telemetry.shutdown()


def test_a_span_records_its_attributes_and_the_outcome_set_inside_it() -> None:
    telemetry, spans, _reader = _enabled()
    with telemetry.span(
        "nightjar.action",
        incident_id="i-1",
        action_type="purge_cache",
        provider="lab",
        risk="r1",
    ) as span:
        span.set(outcome="executed", attempts=0)

    finished = spans.get_finished_spans()
    assert [span.name for span in finished] == ["nightjar.action"]
    attributes = dict(finished[0].attributes or {})
    assert attributes["incident_id"] == "i-1"
    assert attributes["provider"] == "lab"
    assert attributes["outcome"] == "executed"
    assert "attempts" in attributes


def test_an_escaping_exception_marks_the_span_as_failed() -> None:
    telemetry, spans, _reader = _enabled()
    with pytest.raises(RuntimeError), telemetry.span("nightjar.action", incident_id="i-2"):
        raise RuntimeError("provider said no")

    (span,) = spans.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert [event.name for event in span.events] == ["exception"]


def test_none_attributes_are_dropped_rather_than_exported() -> None:
    telemetry, spans, _reader = _enabled()
    with telemetry.span("nightjar.action", incident_id="i-3", provider=None):
        pass

    (span,) = spans.get_finished_spans()
    assert "provider" not in (span.attributes or {})


def test_a_counter_carries_the_labels_a_dashboard_filters_on() -> None:
    from collections.abc import Iterable
    from typing import Any

    telemetry, _spans, reader = _enabled()
    telemetry.count(ACTION_TOTAL, provider="lab", action="purge_cache", outcome="executed")
    telemetry.count(ACTION_TOTAL, provider="lab", action="purge_cache", outcome="executed")
    telemetry.count(VERIFICATION_TOTAL, kind="http", required=True, passed=True)
    telemetry.count(DOCTOR_VERDICT_TOTAL, provider="lab", verdict="ok")

    data = reader.get_metrics_data()
    assert data is not None
    seen: dict[str, Any] = {}
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                seen[metric.name] = metric

    actions = seen[ACTION_TOTAL]
    points: Iterable[Any] = actions.data.data_points
    (point,) = list(points)
    assert point.value == 2
    assert point.attributes["provider"] == "lab"
    assert point.attributes["outcome"] == "executed"
    assert seen[VERIFICATION_TOTAL].data.data_points[0].value == 1


def test_telemetry_is_off_unless_configured() -> None:
    assert Telemetry.from_settings(Settings(environment="test")).enabled is False


def test_enabling_telemetry_without_an_endpoint_is_refused() -> None:
    with pytest.raises(ValueError, match="NIGHTJAR_OTEL_ENDPOINT"):
        Settings(environment="test", otel_enabled=True)


def test_an_out_of_range_sample_ratio_is_refused() -> None:
    for ratio in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            Settings(environment="test", otel_sample_ratio=ratio)


def test_enabled_telemetry_builds_a_tracer_and_can_be_shut_down() -> None:
    settings = Settings(
        environment="test",
        otel_enabled=True,
        otel_endpoint="http://127.0.0.1:9",
        otel_service_name="nightjar-test",
    )
    telemetry = Telemetry.from_settings(settings)
    try:
        assert telemetry.enabled is True
        with telemetry.span("nightjar.action", incident_id="i-4"):
            pass
    finally:
        telemetry.shutdown()


@pytest.mark.integration
def test_a_resolved_incident_emits_the_spans_an_operator_would_look_for(
    settings: Settings, lab: object
) -> None:
    from fastapi.testclient import TestClient

    from nightjar.api.main import build_service, create_app
    from tests.support import scenario_payload, submit, wait_for_status

    telemetry, spans, _reader = _enabled()
    service = build_service(settings, telemetry=telemetry)
    app = create_app(settings, service=service)
    with TestClient(app) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))  # type: ignore[arg-type]
        wait_for_status(client, incident["id"], {"resolved"})

    finished = spans.get_finished_spans()
    names = [span.name for span in finished]
    assert "nightjar.action" in names
    assert "nightjar.verification" in names

    (action,) = [span for span in finished if span.name == "nightjar.action"]
    attributes = dict(action.attributes or {})
    assert attributes["incident_id"] == incident["id"]
    assert attributes["provider"] == "lab"
    assert attributes["action_type"] == "purge_cache"
    assert attributes["outcome"] == "executed"

    verification = [span for span in finished if span.name == "nightjar.verification"]
    assert verification
    assert all(
        dict(span.attributes or {}).get("incident_id") == incident["id"] for span in verification
    )
