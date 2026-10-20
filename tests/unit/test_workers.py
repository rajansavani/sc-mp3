"""Tests for the resizable thread pools and the timers the job manager uses."""

import logging
import threading
import time

import pytest

from scmp3.app.workers import TaskPool, ThreadTimers

WAIT = 5.0


class Gate:
    """Tasks that wait until the test opens them, counting how many run at once."""

    def __init__(self) -> None:
        self.open = threading.Event()
        self.running = 0
        self.most = 0
        self.finished: list[int] = []
        self._lock = threading.Lock()

    def task(self, number: int) -> object:
        def run() -> None:
            with self._lock:
                self.running += 1
                self.most = max(self.most, self.running)
            self.open.wait(WAIT)
            with self._lock:
                self.running -= 1
                self.finished.append(number)

        return run

    def wait_for_running(self, count: int) -> None:
        deadline = time.monotonic() + WAIT
        while self.running < count:
            assert time.monotonic() < deadline, f"only {self.running} started"
            time.sleep(0.005)


def test_one_thread_runs_tasks_in_the_order_they_came() -> None:
    pool = TaskPool("test", 1)
    order: list[int] = []
    for number in range(20):
        pool.submit(lambda number=number: order.append(number))
    assert pool.wait_until_idle(WAIT)
    assert order == list(range(20))
    pool.close()


def test_no_more_tasks_run_at_once_than_the_size() -> None:
    pool = TaskPool("test", 2)
    gate = Gate()
    for number in range(6):
        pool.submit(gate.task(number))
    gate.wait_for_running(2)
    time.sleep(0.05)
    assert gate.running == 2
    gate.open.set()
    assert pool.wait_until_idle(WAIT)
    assert gate.most == 2
    assert sorted(gate.finished) == list(range(6))
    pool.close()


def test_a_bigger_size_starts_waiting_tasks_at_once() -> None:
    pool = TaskPool("test", 1)
    gate = Gate()
    for number in range(4):
        pool.submit(gate.task(number))
    gate.wait_for_running(1)
    pool.resize(3)
    gate.wait_for_running(3)
    gate.open.set()
    assert pool.wait_until_idle(WAIT)
    pool.close()


def test_a_smaller_size_lets_running_tasks_finish_first() -> None:
    pool = TaskPool("test", 3)
    gate = Gate()
    for number in range(3):
        pool.submit(gate.task(number))
    gate.wait_for_running(3)
    pool.resize(1)
    gate.open.set()
    assert pool.wait_until_idle(WAIT)
    assert sorted(gate.finished) == [0, 1, 2]
    second = Gate()
    for number in range(3):
        pool.submit(second.task(number))
    second.wait_for_running(1)
    time.sleep(0.05)
    assert second.running == 1
    second.open.set()
    assert pool.wait_until_idle(WAIT)
    pool.close()


def test_closing_drops_the_tasks_still_waiting() -> None:
    pool = TaskPool("test", 1)
    gate = Gate()
    pool.submit(gate.task(0))
    pool.submit(gate.task(1))
    gate.wait_for_running(1)
    pool.close()
    pool.submit(gate.task(2))
    gate.open.set()
    assert pool.wait_until_idle(WAIT)
    assert gate.finished == [0]


def test_a_failing_task_is_logged_and_the_pool_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    pool = TaskPool("test", 1)
    done = threading.Event()

    def fail() -> None:
        raise RuntimeError("broken")

    with caplog.at_level(logging.ERROR, logger="scmp3.app.workers"):
        pool.submit(fail)
        pool.submit(done.set)
        assert done.wait(WAIT)
        assert pool.wait_until_idle(WAIT)
    assert "A task in the test pool failed" in caplog.text
    pool.close()


def test_waiting_for_idle_gives_up_after_the_timeout() -> None:
    pool = TaskPool("test", 1)
    gate = Gate()
    pool.submit(gate.task(0))
    assert pool.wait_until_idle(0.05) is False
    gate.open.set()
    assert pool.wait_until_idle(WAIT)
    pool.close()


def test_a_timer_calls_back_after_its_delay() -> None:
    fired = threading.Event()
    ThreadTimers().call_later(0.01, fired.set)
    assert fired.wait(WAIT)


def test_a_timer_called_off_never_fires() -> None:
    fired = threading.Event()
    ThreadTimers().call_later(0.05, fired.set).cancel()
    assert not fired.wait(0.2)
