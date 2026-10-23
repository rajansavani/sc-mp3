"""Pushes events from Python to the web UI, which receives them in `window.scmp3.emit`.

Job and progress events are merged per job, and batches go out at most about ten times a second
from the bus's own thread, so no worker or bridge thread ever waits on the window. A `Deferred`
event is built on that thread too, just before it's sent, and only if nothing replaced it first.
"""

import itertools
import json
import logging
import threading
import time
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

# The function api.js puts on the page to receive one event.
EMIT_FUNCTION = "window.scmp3.emit"

# Seconds between batches, so the window gets about ten a second.
SEND_INTERVAL = 0.1

# How long close() waits for a send in progress, since evaluate_js can hang on a closing window.
CLOSE_TIMEOUT = 2.0

# The most JSON one script carries. A bigger batch, such as every job after Save all, goes in
# several, so the answer to a bridge call, which pywebview also sends as a script, never waits
# long behind it.
MAX_SCRIPT_CHARACTERS = 200_000
# The most events one batch carries. After Save all of 2,000, the rest go in the next batches,
# merged with whatever changes meanwhile, so the window's thread is never busy for long at once.
MAX_BATCH_EVENTS = 200

Event = Mapping[str, Any]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deferred:
    """An event built when it's sent, so a burst of changes to one job builds its dict once.

    `build` runs on the bus's thread, outside the caller's locks, so it may only read what was
    copied for it. `key` is what MERGE_RULES would read from the built event.
    """

    type: str
    key: str
    build: Callable[[], Event]


# A waiting event: JSON already, or one still to build.
Pending = str | Deferred


class Window(Protocol):
    """The part of a pywebview window the bus needs."""

    def evaluate_js(self, script: str) -> Any: ...


def _job_event_key(event: Event) -> str:
    return event["job"]["id"]


def _job_id_key(event: Event) -> str:
    """The job an event is about, for events that name it by id."""
    return event["job_id"]


def _single_key(event: Event) -> str:
    """The same key for every event of a type, for events that carry a whole object."""
    return ""


@dataclass(frozen=True)
class MergeRule:
    """How one event type is merged with the waiting events that have the same key."""

    key: Callable[[Event], str]
    # The waiting event types with that key that this event makes stale.
    replaces: tuple[str, ...]


# Event types not listed here are never merged, so every toast is shown.
MERGE_RULES: dict[str, MergeRule] = {
    # A full job snapshot already holds the job's progress, so waiting progress is stale too.
    "job": MergeRule(key=_job_event_key, replaces=("job", "progress")),
    "progress": MergeRule(key=_job_id_key, replaces=("progress",)),
    # The job has left the queue, so nothing waiting for it is worth sending.
    "job_removed": MergeRule(key=_job_id_key, replaces=("job", "progress")),
    # These carry the whole account or components object, so only the newest one counts.
    "account": MergeRule(key=_single_key, replaces=("account",)),
    "components": MergeRule(key=_single_key, replaces=("components",)),
}


class PendingEvents:
    """Events waiting to be sent, in send order, with stale ones already dropped."""

    def __init__(self) -> None:
        # A dict keeps insertion order, and a replaced event moves to the end, after older ones.
        self._events: dict[Hashable, Pending] = {}
        self._unmerged_ids = itertools.count()

    def add(self, event: Event | Deferred) -> None:
        """Add an event, dropping the waiting events it replaces."""
        if isinstance(event, Deferred):
            self._merge(event.type, event.key, event)
            return
        # Serializing now raises in the caller for a bad event, and freezes its content.
        text = json.dumps(event)
        rule = MERGE_RULES.get(event["type"])
        self._merge(event["type"], rule.key(event) if rule else None, text)

    def _merge(self, event_type: str, key: str | None, pending: Pending) -> None:
        rule = MERGE_RULES.get(event_type)
        if rule is None or key is None:
            self._events[next(self._unmerged_ids)] = pending
            return
        for stale_type in rule.replaces:
            self._events.pop((stale_type, key), None)
        self._events[(event_type, key)] = pending

    def take(self, limit: int | None = None) -> list[Pending]:
        """Remove and return the oldest waiting events, up to `limit`, oldest first."""
        keys = list(itertools.islice(self._events, limit))
        return [self._events.pop(key) for key in keys]

    def __bool__(self) -> bool:
        return bool(self._events)


def batch_script(events: list[str]) -> str:
    """JavaScript that hands each event, already JSON, to the UI in order."""
    return f"for (const event of [{','.join(events)}]) {EMIT_FUNCTION}(event);"


def as_json(pending: Iterable[Pending]) -> Iterator[str]:
    """The waiting events as JSON, each deferred one built as it comes. A failed one is left out."""
    for item in pending:
        if isinstance(item, str):
            yield item
            continue
        try:
            yield json.dumps(item.build())
        except Exception:
            # The next change to the same job sends it again.
            log.exception("A %s event couldn't be built, so it wasn't sent", item.type)


def in_chunks(events: Iterable[str], limit: int = MAX_SCRIPT_CHARACTERS) -> Iterator[list[str]]:
    """The events in order, in groups of about `limit` characters. A bigger event goes alone."""
    chunk: list[str] = []
    size = 0
    for event in events:
        if chunk and size + len(event) > limit:
            yield chunk
            chunk, size = [], 0
        chunk.append(event)
        size += len(event)
    if chunk:
        yield chunk


class EventBus:
    """Sends events to the window from any thread, merged and capped at about ten batches a second.

    Events wait until `attach` hands over a window that has loaded the UI.
    """

    def __init__(
        self,
        *,
        interval: float = SEND_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        script_limit: int = MAX_SCRIPT_CHARACTERS,
        batch_limit: int = MAX_BATCH_EVENTS,
    ) -> None:
        self._interval = interval
        self._script_limit = script_limit
        self._batch_limit = batch_limit
        self._clock = clock
        self._wake = threading.Condition()
        self._pending = PendingEvents()
        self._window: Window | None = None
        self._last_send: float | None = None
        self._closed = False
        self._thread: threading.Thread | None = None

    def emit(self, event: Event | Deferred) -> None:
        """Queue an event for the UI. Safe from any thread, and never waits on the window."""
        with self._wake:
            if self._closed:
                kind = event.type if isinstance(event, Deferred) else event.get("type")
                log.debug("The window is closed, so a %s event wasn't sent", kind)
                return
            self._pending.add(event)
            self._wake.notify()

    def attach(self, window: Window) -> None:
        """Start sending to a window whose page is ready. Events that waited go out first."""
        with self._wake:
            if self._closed:
                return
            self._window = window
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="scmp3-events", daemon=True)
                self._thread.start()
            self._wake.notify()

    def close(self) -> None:
        """Stop sending and drop whatever is still waiting, since the window is gone."""
        with self._wake:
            self._closed = True
            self._wake.notify()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(CLOSE_TIMEOUT)

    def _run(self) -> None:
        while True:
            batch = self._wait_for_batch()
            if batch is None:
                return
            window, events = batch
            self._send(window, events)

    def _wait_for_batch(self) -> tuple[Window, list[Pending]] | None:
        """Block until a batch is due and return it with its window, or return None once closed."""
        with self._wake:
            while not self._closed:
                if self._window is None or not self._pending:
                    self._wake.wait()
                    continue
                delay = self._delay()
                if delay > 0:
                    self._wake.wait(delay)
                    continue
                self._last_send = self._clock()
                return self._window, self._pending.take(self._batch_limit)
            return None

    def _delay(self) -> float:
        """Seconds until the next batch may go, which is zero after a quiet spell."""
        if self._last_send is None:
            return 0.0
        return self._last_send + self._interval - self._clock()

    def _send(self, window: Window, pending: list[Pending]) -> None:
        # Each part goes as soon as it's built, so the first events don't wait for the last.
        for events in in_chunks(as_json(pending), self._script_limit):
            try:
                window.evaluate_js(batch_script(events))
            except Exception:
                # Those events are lost, but the next batch carries newer job snapshots.
                log.warning("Sending %d events to the window failed", len(events), exc_info=True)
