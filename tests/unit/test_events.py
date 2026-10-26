"""Tests for the event bus: merging, the rate cap, waiting for the window, and thread safety."""

import itertools
import json
import logging
import re
import threading
import time
from typing import Any

import pytest

from scmp3.app.events import (
    Deferred,
    EventBus,
    PendingEvents,
    as_json,
    batch_script,
    in_chunks,
)

# Generous, so a slow CI machine doesn't fail a test that's only waiting for a send.
WAIT = 5.0


class FakeWindow:
    """Records each script the bus sends and decodes the events in it."""

    def __init__(self, fail_first: int = 0) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.sent_at: list[float] = []
        self._fail_first = fail_first
        self._changed = threading.Condition()

    def evaluate_js(self, script: str) -> None:
        with self._changed:
            self.sent_at.append(time.perf_counter())
            # Waiters wake once the lock is released, so this also covers a failed send.
            self._changed.notify_all()
            if self._fail_first:
                self._fail_first -= 1
                raise RuntimeError("the window went away")
            self.batches.append(decode(script))

    @property
    def events(self) -> list[dict[str, Any]]:
        return [event for batch in self.batches for event in batch]

    def wait_for(self, done: Any, timeout: float = WAIT) -> None:
        """Wait until `done(window)` is true, or fail the test."""
        with self._changed:
            assert self._changed.wait_for(lambda: done(self), timeout), "the events never arrived"


def decode(script: str) -> list[dict[str, Any]]:
    """The events in a script from batch_script."""
    match = re.fullmatch(r"for \(const event of (\[.*\])\) window\.scmp3\.emit\(event\);", script)
    assert match, script
    return json.loads(match.group(1))


def job_event(job_id: str, version: int) -> dict[str, Any]:
    return {"type": "job", "job": {"id": job_id, "version": version}}


def progress_event(job_id: str, pct: float) -> dict[str, Any]:
    return {"type": "progress", "job_id": job_id, "pct": pct, "speed": None, "eta": None}


def toast_event(text: str) -> dict[str, Any]:
    return {"type": "toast", "level": "info", "text": text}


@pytest.fixture
def bus() -> Any:
    event_bus = EventBus()
    yield event_bus
    event_bus.close()


def test_a_newer_job_event_replaces_the_waiting_one() -> None:
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(job_event("a", 2))
    assert [json.loads(text) for text in pending.take()] == [job_event("a", 2)]


def test_a_newer_progress_event_replaces_the_waiting_one() -> None:
    pending = PendingEvents()
    pending.add(progress_event("a", 10))
    pending.add(progress_event("a", 20))
    assert [json.loads(text) for text in pending.take()] == [progress_event("a", 20)]


def test_a_job_event_drops_waiting_progress_for_that_job() -> None:
    pending = PendingEvents()
    pending.add(progress_event("a", 10))
    pending.add(job_event("a", 1))
    assert [json.loads(text) for text in pending.take()] == [job_event("a", 1)]


def test_progress_after_a_job_event_is_kept_and_sent_after_it() -> None:
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(progress_event("a", 30))
    assert [json.loads(text) for text in pending.take()] == [
        job_event("a", 1),
        progress_event("a", 30),
    ]


def test_a_replaced_event_moves_behind_older_events_of_its_job() -> None:
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(progress_event("a", 30))
    pending.add(job_event("a", 2))
    assert [json.loads(text) for text in pending.take()] == [job_event("a", 2)]


def test_a_removed_job_drops_its_waiting_job_and_progress_events() -> None:
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(progress_event("a", 30))
    pending.add(job_event("b", 1))
    pending.add({"type": "job_removed", "job_id": "a"})
    assert [json.loads(text) for text in pending.take()] == [
        job_event("b", 1),
        {"type": "job_removed", "job_id": "a"},
    ]


def test_events_for_different_jobs_are_not_merged() -> None:
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(job_event("b", 1))
    pending.add(progress_event("a", 5))
    pending.add(progress_event("b", 5))
    assert len(pending.take()) == 4


@pytest.mark.parametrize("event_type", ["account", "components"])
def test_only_the_newest_whole_object_event_is_sent(event_type: str) -> None:
    pending = PendingEvents()
    pending.add({"type": event_type, event_type: {"version": 1}})
    pending.add(toast_event("between"))
    pending.add({"type": event_type, event_type: {"version": 2}})
    assert [json.loads(text) for text in pending.take()] == [
        toast_event("between"),
        {"type": event_type, event_type: {"version": 2}},
    ]


def test_account_and_components_events_dont_replace_each_other() -> None:
    pending = PendingEvents()
    pending.add({"type": "account", "account": None})
    pending.add({"type": "components", "components": {}})
    assert len(pending.take()) == 2


def test_toasts_are_never_merged() -> None:
    pending = PendingEvents()
    pending.add(toast_event("one"))
    pending.add(toast_event("one"))
    assert len(pending.take()) == 2


def test_take_empties_the_pending_events() -> None:
    pending = PendingEvents()
    pending.add(toast_event("one"))
    pending.take()
    assert not pending
    assert pending.take() == []


def test_an_event_that_isnt_json_fails_in_the_caller() -> None:
    pending = PendingEvents()
    with pytest.raises(TypeError):
        pending.add({"type": "toast", "text": object()})


def test_the_script_hands_each_event_to_the_ui_in_order() -> None:
    script = batch_script([json.dumps(toast_event("a")), json.dumps(toast_event("b"))])
    assert decode(script) == [toast_event("a"), toast_event("b")]


def test_events_that_waited_go_out_merged_once_the_window_is_ready(bus: EventBus) -> None:
    window = FakeWindow()
    for version in range(5):
        bus.emit(job_event("a", version))
        bus.emit(progress_event("a", version * 10))
    bus.emit(toast_event("hello"))

    bus.attach(window)
    window.wait_for(lambda w: len(w.batches) == 1)
    assert window.batches[0] == [job_event("a", 4), progress_event("a", 40), toast_event("hello")]


def test_batches_are_at_least_one_interval_apart() -> None:
    interval = 0.1
    bus = EventBus(interval=interval, clock=time.perf_counter)
    window = FakeWindow()
    bus.attach(window)
    try:
        deadline = time.perf_counter() + 0.6
        count = 0
        while time.perf_counter() < deadline:
            bus.emit(toast_event(str(count)))
            count += 1
            time.sleep(0.002)
        window.wait_for(lambda w: len(w.events) == count)
    finally:
        bus.close()

    gaps = [later - earlier for earlier, later in itertools.pairwise(window.sent_at)]
    # Without the cap there would be a batch per event, hundreds of them.
    assert len(window.batches) <= 9
    # A little slack, since a batch is timed just before it's handed to the window.
    assert min(gaps) >= interval * 0.8


def test_the_first_event_after_a_quiet_spell_goes_out_at_once(bus: EventBus) -> None:
    window = FakeWindow()
    bus.attach(window)
    started = time.perf_counter()
    bus.emit(toast_event("now"))
    window.wait_for(lambda w: len(w.batches) == 1)
    assert window.sent_at[0] - started < 0.05


def test_many_threads_can_emit_at_once_and_every_job_ends_current(bus: EventBus) -> None:
    window = FakeWindow()
    bus.attach(window)
    threads_count = 8
    versions = 200

    def worker(number: int) -> None:
        for version in range(versions):
            bus.emit(job_event(f"job-{number}", version))
            bus.emit(progress_event(f"job-{number}", version))
        bus.emit(toast_event(f"done {number}"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    window.wait_for(lambda w: len([e for e in w.events if e["type"] == "toast"]) == threads_count)
    received = window.events
    for number in range(threads_count):
        job_id = f"job-{number}"
        job_versions = [
            e["job"]["version"] for e in received if e.get("job", {}).get("id") == job_id
        ]
        # Each job's snapshots arrive in the order they were made, ending with the newest.
        assert job_versions == sorted(job_versions)
        assert job_versions[-1] == versions - 1
    toasts = sorted(e["text"] for e in received if e["type"] == "toast")
    assert toasts == sorted(f"done {n}" for n in range(threads_count))


def test_a_failed_send_is_logged_and_later_events_still_go_out(
    bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    window = FakeWindow(fail_first=1)
    bus.attach(window)
    with caplog.at_level(logging.WARNING, logger="scmp3.app.events"):
        bus.emit(toast_event("lost"))
        window.wait_for(lambda w: len(w.sent_at) == 1)
        bus.emit(toast_event("delivered"))
        window.wait_for(lambda w: len(w.batches) == 1)
    assert window.events == [toast_event("delivered")]
    assert "Sending 1 events to the window failed" in caplog.text


def test_nothing_is_sent_after_close() -> None:
    bus = EventBus()
    window = FakeWindow()
    bus.attach(window)
    bus.close()
    bus.emit(toast_event("too late"))
    time.sleep(0.3)
    assert window.batches == []


def test_attaching_after_close_starts_nothing() -> None:
    bus = EventBus()
    bus.close()
    window = FakeWindow()
    bus.emit(toast_event("too late"))
    bus.attach(window)
    time.sleep(0.3)
    assert window.batches == []


def test_close_stops_the_sending_thread() -> None:
    before = set(threading.enumerate())
    bus = EventBus()
    bus.attach(FakeWindow())
    started = set(threading.enumerate()) - before
    bus.close()
    assert [thread.name for thread in started] == ["scmp3-events"]
    assert not any(thread.is_alive() for thread in started)


class Builds:
    """Deferred job events that count their builds and note the thread each ran on."""

    def __init__(self) -> None:
        self.built: list[tuple[str, int]] = []
        self.threads: set[str] = set()

    def job(self, job_id: str, version: int) -> Deferred:
        def build() -> dict[str, Any]:
            self.built.append((job_id, version))
            self.threads.add(threading.current_thread().name)
            return job_event(job_id, version)

        return Deferred("job", job_id, build)


def test_a_deferred_job_event_is_built_only_if_nothing_replaced_it() -> None:
    builds = Builds()
    pending = PendingEvents()
    for version in range(3):
        pending.add(builds.job("a", version))
    assert builds.built == []
    assert list(as_json(pending.take())) == [json.dumps(job_event("a", 2))]
    assert builds.built == [("a", 2)]


def test_deferred_and_ready_events_replace_each_other_by_job() -> None:
    builds = Builds()
    pending = PendingEvents()
    pending.add(job_event("a", 1))
    pending.add(builds.job("a", 2))
    pending.add(progress_event("a", 40))
    pending.add(builds.job("b", 1))
    pending.add({"type": "job_removed", "job_id": "b"})
    sent = [json.loads(text) for text in as_json(pending.take())]
    assert sent == [
        job_event("a", 2),
        progress_event("a", 40),
        {"type": "job_removed", "job_id": "b"},
    ]
    assert builds.built == [("a", 2)]


def test_a_deferred_event_is_built_on_the_bus_thread(bus: EventBus) -> None:
    builds = Builds()
    window = FakeWindow()
    bus.emit(builds.job("a", 1))
    assert builds.built == []
    bus.attach(window)
    window.wait_for(lambda w: len(w.events) == 1)
    assert window.events == [job_event("a", 1)]
    assert builds.threads == {"scmp3-events"}


def test_a_deferred_event_that_fails_to_build_is_logged_and_the_rest_go_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> dict[str, Any]:
        raise RuntimeError("a bug in the job view")

    pending = PendingEvents()
    pending.add(Deferred("job", "a", broken))
    pending.add(toast_event("still sent"))
    with caplog.at_level(logging.ERROR, logger="scmp3.app.events"):
        sent = list(as_json(pending.take()))
    assert sent == [json.dumps(toast_event("still sent"))]
    assert "A job event couldn't be built" in caplog.text


def test_events_are_sent_in_parts_of_a_limited_size() -> None:
    events = ["a" * 40, "b" * 40, "c" * 40, "d" * 150, "e" * 10]
    assert list(in_chunks(events, limit=100)) == [
        ["a" * 40, "b" * 40],
        ["c" * 40],
        ["d" * 150],
        ["e" * 10],
    ]


def test_a_big_batch_reaches_the_window_in_order_in_several_scripts() -> None:
    bus = EventBus(script_limit=200)
    window = FakeWindow()
    for number in range(20):
        bus.emit(toast_event(f"toast {number}"))
    bus.attach(window)
    window.wait_for(lambda w: len(w.events) == 20)
    bus.close()
    assert [event["text"] for event in window.events] == [f"toast {n}" for n in range(20)]
    assert len(window.batches) > 1


def toast_texts(batch: list[Any]) -> list[str]:
    return [json.loads(text)["text"] for text in batch]


def test_a_batch_takes_the_oldest_events_up_to_its_limit() -> None:
    pending = PendingEvents()
    for number in range(5):
        pending.add(toast_event(f"toast {number}"))
    assert toast_texts(pending.take(2)) == ["toast 0", "toast 1"]
    assert toast_texts(pending.take()) == ["toast 2", "toast 3", "toast 4"]
    assert not pending


def test_a_burst_bigger_than_a_batch_goes_out_over_several_batches_in_order() -> None:
    bus = EventBus(interval=0.01, batch_limit=3)
    window = FakeWindow()
    for number in range(10):
        bus.emit(job_event(f"job {number}", 1))
    bus.attach(window)
    window.wait_for(lambda w: len(w.events) == 10)
    bus.close()
    assert [len(batch) for batch in window.batches] == [3, 3, 3, 1]
    assert [event["job"]["id"] for event in window.events] == [f"job {n}" for n in range(10)]


def test_a_built_event_lost_in_a_failed_send_says_so(bus: EventBus) -> None:
    window = FakeWindow(fail_first=1)
    lost: list[str] = []
    bus.emit(Deferred("job", "a", lambda: job_event("a", 1), on_lost=lambda: lost.append("a")))
    bus.attach(window)
    window.wait_for(lambda w: len(w.sent_at) == 1)
    bus.emit(Deferred("job", "a", lambda: job_event("a", 2), on_lost=lambda: lost.append("again")))
    window.wait_for(lambda w: len(w.events) == 1)
    assert window.events == [job_event("a", 2)]
    assert lost == ["a"]


def test_a_deferred_event_that_fails_to_build_counts_as_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> dict[str, Any]:
        raise RuntimeError("a bug in the job view")

    lost: list[str] = []
    pending = PendingEvents()
    pending.add(Deferred("job", "a", broken, on_lost=lambda: lost.append("a")))
    with caplog.at_level(logging.ERROR, logger="scmp3.app.events"):
        assert list(as_json(pending.take())) == []
    assert lost == ["a"]
