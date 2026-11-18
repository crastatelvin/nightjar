"""Timeline notifications: a committed write wakes the streams watching it.

Polling is still the fallback, so these tests care about two things: that the
notification path actually fires, and that it never fires for work that did not
commit.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from nightjar.api import db
from nightjar.api.main import create_app
from nightjar.api.notify import TimelineHub, TimelineWatch, dsn_from_url
from nightjar.api.repository import Repository
from nightjar.api.settings import Settings
from nightjar.core.enums import EventType
from nightjar.core.models import Incident
from tests.support import scenario_payload, submit


def test_the_dsn_is_what_asyncpg_expects() -> None:
    assert (
        dsn_from_url("postgresql+asyncpg://nightjar:pw@127.0.0.1:55432/nightjar_test")
        == "postgresql://nightjar:pw@127.0.0.1:55432/nightjar_test"
    )


async def test_wait_returns_immediately_when_a_watch_moved() -> None:
    watch = TimelineWatch("inc_1")
    seen = watch.seen
    watch.bump()
    assert await watch.wait(seen, seconds=30.0) is True


async def test_wait_times_out_and_stays_usable() -> None:
    watch = TimelineWatch("inc_1")
    seen = watch.seen
    started = time.monotonic()
    assert await watch.wait(seen, seconds=0.05) is False
    assert time.monotonic() - started < 1.0

    watch.bump()
    assert await watch.wait(watch.seen - 1, seconds=0.05) is True


async def test_hub_dispatch_is_per_incident() -> None:
    hub = TimelineHub("postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar_test")
    async with hub.watch("inc_a") as a, hub.watch("inc_a") as also_a, hub.watch("inc_b") as b:
        assert hub.watcher_count == 3
        hub._dispatch("inc_a")
        assert a.seen == 1
        assert also_a.seen == 1
        assert b.seen == 0
    assert hub.watcher_count == 0


async def test_a_committed_timeline_write_wakes_the_listener(database_url: str) -> None:
    db.configure(database_url)
    hub = TimelineHub(database_url)
    try:
        assert await hub.start(seconds=10.0) is True
        repo = Repository(db.get_session_factory())
        tenant = str(uuid.uuid4())
        await repo.ensure_tenant(tenant, f"notify-{tenant[:8]}")
        incident, _ = await repo.create_incident(
            Incident(tenant_id=tenant, title="Checkout is returning 502")
        )
        async with hub.watch(incident.id) as watch:
            seen = watch.seen
            await repo.append_event(incident.id, EventType.NOTE, payload={"body": "hi"})
            assert await watch.wait(seen, seconds=5.0) is True
    finally:
        await hub.stop()
        await db.dispose()


async def test_an_aborted_transaction_notifies_nobody(database_url: str) -> None:
    db.configure(database_url)
    hub = TimelineHub(database_url)
    try:
        assert await hub.start(seconds=10.0) is True
        repo = Repository(db.get_session_factory())
        tenant = str(uuid.uuid4())
        await repo.ensure_tenant(tenant, f"notify-{tenant[:8]}")
        incident, _ = await repo.create_incident(
            Incident(tenant_id=tenant, title="Checkout is returning 502")
        )
        async with hub.watch(incident.id) as watch:
            seen = watch.seen
            with pytest.raises(RuntimeError, match="rolled back"):
                async with db.get_session_factory()() as session, session.begin():
                    await repo._append_event_in_session(session, incident.id, EventType.NOTE)
                    raise RuntimeError("rolled back")
            assert await watch.wait(seen, seconds=1.0) is False
    finally:
        await hub.stop()
        await db.dispose()


def _stream_first_event(
    client: TestClient, incident_id: str, after_seq: int, timeout: float
) -> tuple[dict[str, object] | None, float]:
    """Open the stream, then add a note from another thread and time the arrival."""
    posted = threading.Event()

    def add_note() -> None:
        try:
            client.post(
                f"/v1/incidents/{incident_id}/notes",
                json={"body": "streamed note"},
            )
        finally:
            posted.set()

    started = time.monotonic()
    with client.stream(
        "GET",
        f"/v1/incidents/{incident_id}/stream",
        params={"after_seq": after_seq},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        thread = threading.Thread(target=add_note, daemon=True)
        thread.start()
        event: dict[str, object] | None = None
        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: ") :])
                break
            if time.monotonic() - started > timeout:
                break
        thread.join(timeout=30.0)
        assert posted.is_set()
    return event, time.monotonic() - started


@pytest.fixture
def notify_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"sse_notify_enabled": True, "sse_poll_seconds": 30.0})


def test_an_open_stream_is_woken_by_a_committed_note(notify_settings: Settings, lab) -> None:
    """No polling can explain this: the fallback interval is 30 seconds."""
    with TestClient(create_app(notify_settings)) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))
        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        event, elapsed = _stream_first_event(client, incident["id"], events[-1]["seq"], 10.0)

    assert event is not None, "the stream never delivered the note"
    assert event["event_type"] == "note"
    # The polling fallback is 30 s; anything close to it means the notification
    # path did nothing and this test is only measuring the timer.
    assert elapsed < 5.0


def test_polling_still_delivers_when_notifications_are_off(settings: Settings, lab) -> None:
    polled = settings.model_copy(update={"sse_notify_enabled": False, "sse_poll_seconds": 0.25})
    with TestClient(create_app(polled)) as client:
        incident = submit(client, scenario_payload(lab, "site_cache_poisoned"))
        events = client.get(f"/v1/incidents/{incident['id']}/timeline").json()["events"]
        event, _ = _stream_first_event(client, incident["id"], events[-1]["seq"], 10.0)

    assert event is not None, "the polling fallback delivered nothing"
    assert event["event_type"] == "note"


async def test_the_listener_survives_a_failed_start(database_url: str) -> None:
    """A database that refuses the listener must not break the app."""
    hub = TimelineHub("postgresql+asyncpg://nightjar@127.0.0.1:1/nightjar_test")
    try:
        assert await hub.start(seconds=1.5) is False
        assert hub.listening is False
    finally:
        await hub.stop()
    assert hub.watcher_count == 0


async def test_waiting_does_not_block_other_work() -> None:
    """The fallback wait is a suspension, not a sleep in the event loop."""
    hub = TimelineHub("postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar_test")
    async with hub.watch("inc_a") as watch:
        seen = watch.seen
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1

        await asyncio.gather(ticker(), watch.wait(seen, seconds=0.2))
    assert ticks == 5
