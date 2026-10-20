"""Pushes events from Python to the web UI, which receives them in `window.scmp3.emit`.

Job and progress events are merged per job, and batches go out at most about ten times a second
from the bus's own thread, so no worker or bridge thread ever waits on the window.
"""

import itertools
import json
import logging
import threading
import time
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

# The function api.js puts on the page to receive one event.
EMIT_FUNCTION = "window.scmp3.emit"

# Seconds between batches, so the window gets about ten a second.
SEND_INTERVAL = 0.1

# How long close() waits for a send in progress, since evaluate_js can hang on a closing window.
CLOSE_TIMEOUT = 2.0

Event = Mapping[str, Any]

log = logging.getLogger(__name__)


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
    """Events waiting to be sent, as JSON in send order, with stale ones already dropped."""

    def __init__(self) -> None:
        # A dict keeps insertion order, and a replaced event moves to the end, after older ones.
        self._events: dict[Hashable, str] = {}
        self._unmerged_ids = itertools.count()

    def add(self, event: Event) -> None:
        """Add an event, dropping the waiting events it replaces."""
        # Serializing now raises in the caller for a bad event, and freezes its content.
        text = json.dumps(event)
        rule = MERGE_RULES.get(event["type"])
        if rule is None:
            self._events[next(self._unmerged_ids)] = text
            return
        key = rule.key(event)
        for stale_type in rule.replaces:
            self._events.pop((stale_type, key), None)
        self._events[(event["type"], key)] = text

    def take(self) -> list[str]:
        """Remove and return every waiting event, oldest first."""
        events = list(self._events.values())
        self._events.clear()
        return events

    def __bool__(self) -> bool:
        return bool(self._events)


def batch_script(events: list[str]) -> str:
    """JavaScript that hands each event, already JSON, to the UI in order."""
    return f"for (const event of [{','.join(events)}]) {EMIT_FUNCTION}(event);"


class EventBus:
    """Sends events to the window from any thread, merged and capped at about ten batches a second.

    Events wait until `attach` hands over a window that has loaded the UI.
    """

    def __init__(
        self, *, interval: float = SEND_INTERVAL, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._wake = threading.Condition()
        self._pending = PendingEvents()
        self._window: Window | None = None
        self._last_send: float | None = None
        self._closed = False
        self._thread: threading.Thread | None = None

    def emit(self, event: Event) -> None:
        """Queue an event for the UI. Safe from any thread, and never waits on the window."""
        with self._wake:
            if self._closed:
                log.debug("The window is closed, so a %s event wasn't sent", event.get("type"))
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

    def _wait_for_batch(self) -> tuple[Window, list[str]] | None:
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
                return self._window, self._pending.take()
            return None

    def _delay(self) -> float:
        """Seconds until the next batch may go, which is zero after a quiet spell."""
        if self._last_send is None:
            return 0.0
        return self._last_send + self._interval - self._clock()

    def _send(self, window: Window, events: list[str]) -> None:
        try:
            window.evaluate_js(batch_script(events))
        except Exception:
            # A failed batch is lost, but the next one carries newer job snapshots.
            log.warning("Sending %d events to the window failed", len(events), exc_info=True)
