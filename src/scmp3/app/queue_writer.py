"""Writes queue.json a moment after the queue changes, on a thread of its own.

Changes that come close together go into one write, writes come at most every 10 seconds while
jobs keep changing, and a change queue.json doesn't hold, such as a download's progress, writes
nothing. No bridge call or worker waits for the disk. Closing the app writes one last time.
"""

import logging
import threading
import time
from collections.abc import Callable

from scmp3.app.queue_file import QueueFile
from scmp3.core.errors import ScmpError

# Seconds from a change to the write that includes it, after a quiet spell.
WRITE_DELAY = 2.0
# The fewest seconds between two writes. A crash loses at most this much, and a batch of 2,000
# jobs, about 6 MB of queue.json, is written at most this often while it runs.
WRITE_INTERVAL = 10.0
# How long closing waits for a write already under way, before writing again itself.
CLOSE_WAIT = 10.0

log = logging.getLogger(__name__)


class QueueWriter:
    """Keeps queue.json up to date. `entries` gives each job's entry as JSON, in queue order."""

    def __init__(
        self,
        file: QueueFile,
        entries: Callable[[], list[str]],
        *,
        delay: float = WRITE_DELAY,
        interval: float = WRITE_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._file = file
        self._entries = entries
        self._delay = delay
        self._interval = interval
        self._clock = clock
        # What the last successful write held, and when it ended.
        self._written: list[str] | None = None
        self._written_at: float | None = None
        self._wake = threading.Condition()
        self._dirty = False
        self._closed = False
        self._thread: threading.Thread | None = None
        # Only one write at a time, so an older queue can never land after a newer one.
        self._write_lock = threading.Lock()

    def start(self) -> None:
        """Start the thread that writes after each change."""
        with self._wake:
            if self._thread is not None or self._closed:
                return
            self._thread = threading.Thread(target=self._run, name="scmp3-queue", daemon=True)
            self._thread.start()

    def changed(self) -> None:
        """Note that the queue changed. Quick, and safe to call under the job manager's lock."""
        with self._wake:
            if self._closed:
                return
            self._dirty = True
            self._wake.notify()

    def write_now(self) -> bool:
        """Write the queue now, unless queue.json holds it already. Returns whether it does.

        A failure is logged.
        """
        with self._write_lock:
            with self._wake:
                # A change from here on comes after what's collected below, so it's written later.
                self._dirty = False
            try:
                entries = self._entries()
                if entries == self._written:
                    return True
                self._file.save(entries)
                self._written = entries
                self._written_at = self._clock()
            except ScmpError as error:
                log.warning("The queue couldn't be kept in queue.json: %s", error)
                return False
            except Exception:
                # A bug in building an entry mustn't stop the thread, or closing the app.
                log.exception("Keeping the queue in queue.json failed unexpectedly")
                return False
            return True

    def close(self) -> bool:
        """Stop the thread and write a last time. Returns whether queue.json holds the queue."""
        with self._wake:
            self._closed = True
            self._wake.notify()
            thread = self._thread
        if thread is not None:
            thread.join(CLOSE_WAIT)
        return self.write_now()

    def _run(self) -> None:
        while self._wait_for_change():
            self.write_now()

    def _wait_for_change(self) -> bool:
        """Wait for a change, then until the next write may go. Returns False once closed."""
        with self._wake:
            self._wake.wait_for(lambda: self._dirty or self._closed)
            # Changes meanwhile go into the same write.
            self._wake.wait_for(lambda: self._closed, timeout=self._wait_before_writing())
            return not self._closed

    def _wait_before_writing(self) -> float:
        soonest = self._clock() + self._delay
        if self._written_at is not None:
            soonest = max(soonest, self._written_at + self._interval)
        return soonest - self._clock()
