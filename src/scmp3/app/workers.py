"""Thread pools whose size can change while they run, and timers that can be called off."""

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Collection, Mapping
from typing import Protocol

Task = Callable[[], None]
# Whether a queued task still has work to do. One that doesn't runs without taking a paced turn.
StillWanted = Callable[[], bool]
# Seconds between the starts of two tasks that share a pace key, such as downloads from one source.
Spacing = Mapping[str, float]

log = logging.getLogger(__name__)


class Pool(Protocol):
    """Runs tasks in the order they come, in the background."""

    def submit(
        self, task: Task, *, pace: str | None = None, wanted: StillWanted | None = None
    ) -> None: ...

    def resize(self, size: int) -> None: ...

    def close(self) -> None: ...

    def wait_until_idle(self, timeout: float) -> bool: ...

    def start_threads(self) -> None: ...


class TaskPool:
    """Runs tasks in order on up to `size` threads, started as they're needed or all at once.

    A smaller size takes effect as threads finish their tasks, so a running task is never cut
    short. Threads are daemons, so a task still running when the app quits doesn't hold it open.
    A task with a pace key in `spacing` waits until that long after the last one with its key
    started, and other tasks go ahead of it meanwhile. A key in `serial` runs one task at a time,
    and its spacing counts from when the last one ended. One whose `wanted` says it has nothing
    left to do, such as the download of a removed job, runs at once and leaves the turn to the next.
    """

    def __init__(
        self,
        name: str,
        size: int,
        *,
        spacing: Spacing | None = None,
        serial: Collection[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._size = size
        self._tasks: deque[tuple[Task, str | None, StillWanted | None]] = deque()
        self._spacing = dict(spacing or {})
        self._serial = frozenset(serial)
        self._clock = clock
        # When each pace key in `spacing` may start its next task.
        self._next_start: dict[str, float] = {}
        # How many tasks of each serial key are running: one at most.
        self._running_serial: Counter[str] = Counter()
        self._changed = threading.Condition()
        self._threads: set[threading.Thread] = set()
        self._running = 0
        self._closed = False
        # Set once start_threads() has run, so a bigger size starts its threads at once too.
        self._eager = False

    def submit(
        self, task: Task, *, pace: str | None = None, wanted: StillWanted | None = None
    ) -> None:
        """Queue a task, paced with the others that share its key. After close() it's dropped."""
        with self._changed:
            if self._closed:
                log.debug("The %s pool is closed, so a task was dropped", self._name)
                return
            self._tasks.append((task, pace, wanted))
            self._start_threads_for_waiting_tasks()
            self._changed.notify()

    def resize(self, size: int) -> None:
        """Run up to `size` tasks at once from now on."""
        with self._changed:
            self._size = size
            if self._eager:
                self._start_threads_up_to_size()
            self._start_threads_for_waiting_tasks()
            # Threads over the new size see it when they wake, and stop.
            self._changed.notify_all()

    def start_threads(self) -> None:
        """Start every thread now, as the app does before its window opens.

        Starting a thread waits for the OS to run it, which takes tens of milliseconds on a busy
        machine. Done here, no bridge call that queues a task waits for that.
        """
        with self._changed:
            self._eager = True
            self._start_threads_up_to_size()

    def close(self) -> None:
        """Drop the tasks still waiting. Running tasks finish on their own."""
        with self._changed:
            self._closed = True
            self._tasks.clear()
            self._changed.notify_all()

    def wait_until_idle(self, timeout: float) -> bool:
        """Wait until no task is waiting or running. Returns False if that took too long."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._tasks or self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True

    def _start_threads_for_waiting_tasks(self) -> None:
        idle = len(self._threads) - self._running
        wanted = min(self._size, len(self._threads) + max(0, len(self._tasks) - idle))
        self._start_threads_up_to(wanted)

    def _start_threads_up_to_size(self) -> None:
        if not self._closed:
            self._start_threads_up_to(self._size)

    def _start_threads_up_to(self, count: int) -> None:
        while len(self._threads) < count:
            thread = threading.Thread(target=self._work, name=f"scmp3-{self._name}", daemon=True)
            self._threads.add(thread)
            thread.start()

    def _work(self) -> None:
        while True:
            taken = self._next_task()
            if taken is None:
                return
            task, serial_key = taken
            try:
                task()
            except Exception:
                # Tasks report their own failures, so this is a bug in the task itself.
                log.exception("A task in the %s pool failed", self._name)
            finally:
                with self._changed:
                    self._running -= 1
                    if serial_key is not None:
                        self._end_serial_turn(serial_key)
                    self._changed.notify_all()

    def _end_serial_turn(self, key: str) -> None:
        """Let the next task of a serial key start, its spacing after this one ended."""
        self._running_serial[key] -= 1
        self._next_start[key] = self._clock() + self._spacing[key]

    def _next_task(self) -> tuple[Task, str | None] | None:
        """The next task to run with its serial key, or None when this thread should stop."""
        me = threading.current_thread()
        with self._changed:
            while True:
                if self._closed or len(self._threads) > self._size:
                    self._threads.discard(me)
                    self._changed.notify_all()
                    return None
                taken, wait = self._take_ready_task()
                if taken is not None:
                    self._running += 1
                    return taken
                # None waits for the next submit or a serial task's end, a number for a turn.
                self._changed.wait(wait)

    def _take_ready_task(self) -> tuple[tuple[Task, str | None] | None, float | None]:
        """The first task that may start now with its serial key, or else how long until one may."""
        now = self._clock()
        soonest: float | None = None
        for index, (task, pace, wanted) in enumerate(self._tasks):
            paced = pace is not None and pace in self._spacing and (wanted is None or wanted())
            if paced and self._running_serial[pace] > 0:
                # Its key's running task wakes the pool when it ends.
                continue
            start_at = self._next_start.get(pace, now) if paced else now
            if start_at <= now:
                del self._tasks[index]
                return (task, self._take_turn(pace, now) if paced else None), None
            soonest = start_at if soonest is None else min(soonest, start_at)
        return None, None if soonest is None else soonest - now

    def _take_turn(self, key: str, now: float) -> str | None:
        """Start a paced task's turn. Returns the key when it's serial, so its end is counted."""
        if key in self._serial:
            self._running_serial[key] += 1
            return key
        self._next_start[key] = now + self._spacing[key]
        return None


class Cancellable(Protocol):
    def cancel(self) -> None: ...


class Timers(Protocol):
    """Calls a function once after a delay, unless it's called off first."""

    def call_later(self, delay: float, callback: Callable[[], None]) -> Cancellable: ...


class ThreadTimers:
    """Timers on threads of their own."""

    def call_later(self, delay: float, callback: Callable[[], None]) -> Cancellable:
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.name = "scmp3-timer"
        timer.start()
        return timer
