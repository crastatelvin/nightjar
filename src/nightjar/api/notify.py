"""Timeline change notifications over PostgreSQL ``LISTEN``/``NOTIFY``.

A case file that updates the moment something happens is the difference between
a dashboard someone watches and a dashboard someone reloads. Polling every second
gave that, at the cost of one query per open case per second, forever, whether or
not anything changed.

Writers now emit a ``pg_notify`` inside the same transaction that appends the
timeline event, and each API process holds one connection listening for those
notifications. The notifier is transactional by construction: PostgreSQL delivers
the payload only when the transaction commits, so a reader woken by it always
finds the row it was woken for. A failed transaction notifies nobody.

Polling does not go away. It is the fallback when the listener has not attached
yet, has lost its connection, or is talking to a database where the operator
turned notifications off. A missed notification costs latency, never an event,
because the stream still reads the table on its own schedule.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import TracebackType

import asyncpg
from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

# One channel for every incident. The payload narrows it to one case, which is
# cheaper than a channel per incident and has no lifecycle to get wrong.
TIMELINE_CHANNEL = "nightjar_timeline"
NOTIFY_SQL = "SELECT pg_notify(:channel, :payload)"

HEARTBEAT_SECONDS = 30.0
RECONNECT_MAX_SECONDS = 30.0


def dsn_from_url(database_url: str) -> str:
    """Turn a SQLAlchemy URL into a libpq URL ``asyncpg`` understands."""
    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class TimelineWatch:
    """One stream's interest in one incident.

    ``seen`` counts notifications observed since the watch was created. A reader
    samples it *before* querying: if it moved during the query, the reader may
    have missed a row written between the query and the wait, so ``wait`` returns
    immediately and the reader queries again. With a plain ``asyncio.Event`` that
    race loses a wakeup and the update waits for the polling tick.
    """

    def __init__(self, incident_id: str) -> None:
        self.incident_id = incident_id
        self.seen = 0
        self._event = asyncio.Event()

    def bump(self) -> None:
        self.seen += 1
        self._event.set()

    async def wait(self, seen_before: int, *, seconds: float) -> bool:
        """Block until a notification, or until ``seconds``. True means notify."""
        if self.seen != seen_before:
            self._event.clear()
            return True
        try:
            await asyncio.wait_for(self._event.wait(), seconds)
            return True
        except TimeoutError:
            return False
        finally:
            self._event.clear()


class TimelineHub:
    """The process's listener, and the watchers it wakes.

    One hub per API process. It owns a dedicated ``asyncpg`` connection rather
    than borrowing from the request pool: a listening connection holds its socket
    for the life of the process and must never be handed to a request.
    """

    def __init__(self, database_url: str, *, heartbeat_seconds: float = HEARTBEAT_SECONDS):
        self._dsn = dsn_from_url(database_url)
        self._heartbeat_seconds = heartbeat_seconds
        self._watchers: dict[str, set[TimelineWatch]] = {}
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closed = False
        self._listening = False

    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def watcher_count(self) -> int:
        return sum(len(watchers) for watchers in self._watchers.values())

    async def start(self, *, seconds: float = 5.0) -> bool:
        """Attach the listener; return whether it is listening right now.

        A failure is not fatal. The task keeps retrying in the background, and
        until it succeeds every stream runs on its polling fallback.
        """
        if self._task is None:
            self._closed = False
            self._ready.clear()
            self._task = asyncio.create_task(self._run(), name="nightjar-timeline-listener")
        try:
            await asyncio.wait_for(self._ready.wait(), seconds)
        except TimeoutError:
            log.warning("timeline listener did not attach within %.1fs; streams will poll", seconds)
        return self._listening

    async def stop(self) -> None:
        self._closed = True
        self._ready.clear()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._listening = False

    def watch(self, incident_id: str) -> _WatchContext:
        return _WatchContext(self, incident_id)

    def _add(self, watch: TimelineWatch) -> None:
        self._watchers.setdefault(watch.incident_id, set()).add(watch)

    def _remove(self, watch: TimelineWatch) -> None:
        watchers = self._watchers.get(watch.incident_id)
        if not watchers:
            return
        watchers.discard(watch)
        if not watchers:
            self._watchers.pop(watch.incident_id, None)

    def _dispatch(self, incident_id: str) -> None:
        for watch in tuple(self._watchers.get(incident_id, ())):
            watch.bump()

    def _on_notify(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        # Called from asyncpg's protocol loop, on this event loop, between awaits.
        self._dispatch(payload)

    async def _run(self) -> None:
        backoff = 0.5
        while not self._closed:
            connection: asyncpg.Connection | None = None
            try:
                connection = await asyncpg.connect(self._dsn, timeout=5.0)
                await connection.add_listener(TIMELINE_CHANNEL, self._on_notify)
                self._listening = True
                self._ready.set()
                backoff = 0.5
                log.info("listening for timeline changes on %s", TIMELINE_CHANNEL)
                while not self._closed:
                    # A silent connection loss is undetectable otherwise: the
                    # listener just stops being called. A cheap round trip makes
                    # the breakage loud and triggers the reconnect below.
                    await asyncio.sleep(self._heartbeat_seconds)
                    await connection.execute("SELECT 1")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._listening = False
                log.warning(
                    "timeline listener dropped (%s: %s); streams poll until it reconnects",
                    type(exc).__name__,
                    exc,
                )
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()
            if self._closed:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)


class _WatchContext:
    """Registers a watch for the duration of an ``async with`` block."""

    def __init__(self, hub: TimelineHub, incident_id: str) -> None:
        self._hub = hub
        self._watch = TimelineWatch(incident_id)

    async def __aenter__(self) -> TimelineWatch:
        self._hub._add(self._watch)
        return self._watch

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._hub._remove(self._watch)


__all__ = ["NOTIFY_SQL", "TIMELINE_CHANNEL", "TimelineHub", "TimelineWatch", "dsn_from_url"]
