"""Thread pools whose size can change while they run, and timers that can be called off."""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

Task = Callable[[], None]

log = logging.getLogger(__name__)


class Pool(Protocol):
    """Runs tasks in the order they come, in the background."""

    def submit(self, task: Task) -> None: ...

    def resize(self, size: int) -> None: ...

    def close(self) -> None: ...

    def wait_until_idle(self, timeout: float) -> bool: ...


class TaskPool:
    """Runs tasks in order on up to `size` threads, started as they're needed.

    A smaller size takes effect as threads finish their tasks, so a running task is never cut
    short. Threads are daemons, so a task still running when the app quits doesn't hold it open.
    """

    def __init__(self, name: str, size: int) -> None:
        self._name = name
        self._size = size
        self._tasks: deque[Task] = deque()
        self._changed = threading.Condition()
        self._threads: set[threading.Thread] = set()
        self._running = 0
        self._closed = False

    def submit(self, task: Task) -> None:
        """Queue a task. After close() it's dropped."""
        with self._changed:
            if self._closed:
                log.debug("The %s pool is closed, so a task was dropped", self._name)
                return
            self._tasks.append(task)
            self._start_threads_for_waiting_tasks()
            self._changed.notify()

    def resize(self, size: int) -> None:
        """Run up to `size` tasks at once from now on."""
        with self._changed:
            self._size = size
            self._start_threads_for_waiting_tasks()
            # Threads over the new size see it when they wake, and stop.
            self._changed.notify_all()

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
        while len(self._threads) < wanted:
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
                if self._tasks:
                    self._running += 1
                    return self._tasks.popleft()
                self._changed.wait()


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
