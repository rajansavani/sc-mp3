"""Thread pools whose size can change while they run, and timers that can be called off."""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
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
    started, and other tasks go ahead of it meanwhile. One whose `wanted` says it has nothing left
    to do, such as the download of a removed job, runs at once and leaves the turn to the next.
    """

    def __init__(
        self,
        name: str,
        size: int,
        *,
        spacing: Spacing | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._size = size
        self._tasks: deque[tuple[Task, str | None, StillWanted | None]] = deque()
        self._spacing = dict(spacing or {})
        self._clock = clock
        # When each pace key in `spacing` may start its next task.
        self._next_start: dict[str, float] = {}
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
            task = self._next_task()
            if task is None:
                return
            try:
                task()
            except Exception:
                # Tasks report their own failures, so this is a bug in the task itself.
                log.exception("A task in the %s pool failed", self._name)
            finally:
                with self._changed:
                    self._running -= 1
                    self._changed.notify_all()

    def _next_task(self) -> Task | None:
        """The next task to run, or None when this thread should stop."""
        me = threading.current_thread()
        with self._changed:
            while True:
                if self._closed or len(self._threads) > self._size:
                    self._threads.discard(me)
                    self._changed.notify_all()
                    return None
                task, wait = self._take_ready_task()
                if task is not None:
                    self._running += 1
                    return task
                # None waits for the next submit, and a number for a paced task's turn.
                self._changed.wait(wait)

    def _take_ready_task(self) -> tuple[Task | None, float | None]:
        """The first task that may start now, or else how long until one may."""
        now = self._clock()
        soonest: float | None = None
        for index, (task, pace, wanted) in enumerate(self._tasks):
            paced = pace in self._spacing and (wanted is None or wanted())
            start_at = self._next_start.get(pace, now) if paced else now
            if start_at <= now:
                del self._tasks[index]
                if paced:
                    self._next_start[pace] = now + self._spacing[pace]
                return task, None
            soonest = start_at if soonest is None else min(soonest, start_at)
        return None, None if soonest is None else soonest - now


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
