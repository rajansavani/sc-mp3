"""Tests for the job manager on real threads, and what it does at startup and shutdown."""

import importlib.util
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app.events import EventBus
from scmp3.app.jobs import JobManager
from scmp3.app.workers import TaskPool
from scmp3.core import download, paths
from scmp3.core.errors import ErrorKind, ScmpError


def load_job_fakes() -> ModuleType:
    """tests/fixtures/job_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_job_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "job_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_job_fakes()
TRACK, OTHER = fakes.TRACK, fakes.OTHER
Harness = Any


@pytest.fixture
def jobs(tmp_path: Path) -> Harness:
    return fakes.make_harness(tmp_path / "library")


# Starting and stopping


def test_starting_up_clears_old_staging_folders_and_leftover_temp_files(jobs: Harness) -> None:
    old = download.new_staging("old")
    stamp = datetime(2026, 1, 1).timestamp()
    os.utime(old, (stamp, stamp))
    leftover = jobs.library / ".Kaia - Song.mp3.partial"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"half")
    os.utime(leftover, (stamp, stamp))
    jobs.manager.start_up()
    jobs.pools.run_all()
    assert not old.exists()
    assert not leftover.exists()


def test_shutting_down_stops_downloads_and_timers(jobs: Harness) -> None:
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.offline))
    jobs.add_and_resolve()
    job_id = jobs.add_and_resolve(OTHER)
    jobs.steps.during_fetch = jobs.manager.shut_down
    jobs.pools.run("download")
    assert jobs.timers.waiting() == []
    assert all(pool.closed for pool in jobs.pools.by_name.values())
    assert jobs.job(job_id)["staged_audio"] is None


def test_starting_up_logs_why_a_cleanup_failed_without_the_folder(
    jobs: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    folder = str(Path.home() / "AppData" / "Local" / "sc-mp3" / "staging")

    def refused(**kwargs: Any) -> list[str]:
        raise PermissionError(13, "Access is denied", folder)

    monkeypatch.setattr(download, "purge_old", refused)
    with caplog.at_level(logging.INFO):
        jobs.manager.start_up()
        jobs.pools.run_all()
    (warning,) = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warning.getMessage().endswith("Access is denied")
    assert folder not in warning.getMessage()


def test_with_review_off_and_slow_covers_every_save_reuses_its_cover(tmp_path: Path) -> None:
    store = fakes.SettingsStore(paths.config_dir() / "settings.json")
    library = tmp_path / "library"
    store.update(
        {
            "library_dir": str(library),
            "review_before_saving": False,
            "when_file_exists": "keep_both",
        }
    )
    steps = fakes.FakeSteps()
    covers = fakes.FakeCovers(source_delay=0.05)
    events = fakes.RecordingBus()
    manager = JobManager(store, events, steps=steps, covers=covers, make_pool=TaskPool)
    links = [f"https://soundcloud.com/kaia/track-{n}" for n in range(20)]
    added = manager.add_inputs("\n".join(links))["added"]
    assert manager.wait_until_idle(20)
    manager.shut_down()
    assert {job["state"] for job in manager.views()} == {"saved"}
    assert [call.cover.kind for call in steps.finalized] == ["file"] * 20
    assert covers.source_reads == 20
    assert [job_id for job_id in added if (paths.staging_dir() / job_id).exists()] == []


def test_shutting_down_waits_for_a_save_that_is_placing_its_file(tmp_path: Path) -> None:
    steps = fakes.FakeSteps()
    manager, _ = threaded_manager(tmp_path / "library", steps)
    placing = threading.Event()

    def slow_finalize() -> None:
        placing.set()
        time.sleep(0.3)

    steps.during_finalize = slow_finalize
    (job_id,) = manager.add_inputs(TRACK)["added"]
    assert manager.wait_until_idle(10)
    manager.save([job_id])
    assert placing.wait(10)
    manager.shut_down()
    (job,) = manager.views()
    assert job["state"] == "saved"
    assert Path(job["output_path"]).is_file()


def test_an_unexpected_failure_names_only_the_exceptions_type(
    jobs: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    secret = str(Path.home() / "Music" / "private folder" / "song.mp3")
    jobs.steps.prepare_errors.append(FileNotFoundError(2, "No such file or directory", secret))
    with caplog.at_level(logging.INFO):
        job_id = jobs.add_and_resolve()
    error = jobs.job(job_id)["error"]
    assert error["kind"] == "internal"
    assert error["detail"] == "resolving failed with FileNotFoundError"
    above_debug = [record for record in caplog.records if record.levelno >= logging.INFO]
    assert all(secret not in record.getMessage() for record in above_debug)


# Threads


def threaded_manager(library: Path, steps: Any, **settings: Any) -> tuple[JobManager, Any]:
    store = fakes.SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(library), **settings})
    events = fakes.RecordingBus()
    manager = JobManager(store, events, steps=steps, covers=fakes.FakeCovers(), make_pool=TaskPool)
    return manager, events


def test_many_jobs_save_on_real_threads(tmp_path: Path) -> None:
    steps = fakes.FakeSteps(delay=0.01)
    manager, _ = threaded_manager(tmp_path / "library", steps)
    links = [f"https://soundcloud.com/kaia/track-{n}" for n in range(12)]
    for number, link in enumerate(links):
        steps.sources[link] = fakes.source_for(link, f"Track {number}")
    added = manager.add_inputs("\n".join(links))["added"]
    assert manager.wait_until_idle(10)
    manager.save(added)
    assert manager.wait_until_idle(10)
    assert {job["state"] for job in manager.views()} == {"saved"}
    names = {Path(job["output_path"]).name for job in manager.views()}
    assert len(names) == 12
    manager.shut_down()


def test_two_saves_with_the_same_name_never_overwrite_each_other(tmp_path: Path) -> None:
    steps = fakes.FakeSteps(delay=0.01)
    manager, _ = threaded_manager(tmp_path / "library", steps, when_file_exists="keep_both")
    links = [f"https://soundcloud.com/kaia/copy-{n}" for n in range(4)]
    for link in links:
        steps.sources[link] = fakes.source_for(link)
    added = manager.add_inputs("\n".join(links))["added"]
    assert manager.wait_until_idle(10)
    manager.save(added)
    assert manager.wait_until_idle(10)
    paths_saved = sorted(Path(job["output_path"]).name for job in manager.views())
    assert paths_saved == [
        "Kaia, Lou V - Midnight Drive (2).mp3",
        "Kaia, Lou V - Midnight Drive (3).mp3",
        "Kaia, Lou V - Midnight Drive (4).mp3",
        "Kaia, Lou V - Midnight Drive.mp3",
    ]
    manager.shut_down()


def test_downloads_never_run_more_at_once_than_the_setting(tmp_path: Path) -> None:
    running = 0
    most = 0
    lock = threading.Lock()

    class CountingSteps(fakes.FakeSteps):
        def fetch(self, prepared: Any, **kwargs: Any) -> Any:
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
            try:
                return super().fetch(prepared, **kwargs)
            finally:
                with lock:
                    running -= 1

    steps = CountingSteps(delay=0.02)
    manager, _ = threaded_manager(tmp_path / "library", steps)
    links = [f"https://soundcloud.com/kaia/track-{n}" for n in range(8)]
    manager.add_inputs("\n".join(links))
    assert manager.wait_until_idle(10)
    assert most == 2
    manager.shut_down()


def test_a_closed_event_bus_still_lets_the_manager_work(tmp_path: Path) -> None:
    store = fakes.SettingsStore(paths.config_dir() / "settings.json")
    events = EventBus()
    events.close()
    manager = JobManager(
        store, events, steps=fakes.FakeSteps(), covers=fakes.FakeCovers(), make_pool=TaskPool
    )
    manager.add_inputs(TRACK)
    assert manager.wait_until_idle(10)
    assert manager.views()[0]["state"] == "needs_review"
    manager.shut_down()


class RecordingWindow:
    """A window that keeps the events the bus sends it, in the order they arrive."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.changed = threading.Condition()

    def evaluate_js(self, script: str) -> None:
        batch = json.loads(script.removeprefix("for (const event of ").split(") window")[0])
        with self.changed:
            self.events.extend(batch)
            self.changed.notify_all()


# Where each state comes in the flow of a job that's saved, which never goes back.
STATE_ORDER = ["queued", "resolving", "needs_review", "saving", "saved"]


def check_job_order(events: list[dict[str, Any]]) -> None:
    """Check a job's events: states move forward, progress rises, and nothing follows removal."""
    rank = -1
    pct = -1.0
    for number, event in enumerate(events):
        if event["type"] == "job_removed":
            assert number == len(events) - 1, events[number:]
        elif event["type"] == "progress":
            assert STATE_ORDER[rank] in ("needs_review", "saving"), events
            assert event["pct"] >= pct, events
            pct = event["pct"]
        else:
            state_rank = STATE_ORDER.index(event["job"]["state"])
            assert state_rank >= rank, events
            rank = state_rank


def test_each_jobs_events_arrive_in_the_order_its_changes_happened(tmp_path: Path) -> None:
    store = fakes.SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(tmp_path / "library")})
    steps = fakes.FakeSteps(delay=0.005, progress=(10.0, 40.0, 70.0, 100.0))
    # The download's last report goes out before the job event that ends it would replace it.
    steps.during_fetch = lambda: time.sleep(0.05)
    bus = EventBus(interval=0.01)
    window = RecordingWindow()
    bus.attach(window)
    manager = JobManager(store, bus, steps=steps, covers=fakes.FakeCovers(), make_pool=TaskPool)
    links = [f"https://soundcloud.com/kaia/track-{n}" for n in range(10)]
    for number, link in enumerate(links):
        steps.sources[link] = fakes.source_for(link, f"Track {number}")
    added = manager.add_inputs("\n".join(links))["added"]
    # Each job is saved as soon as it's in review, while its download runs.
    asked: set[str] = set()
    deadline = time.monotonic() + 10
    while len(asked) < len(added):
        assert time.monotonic() < deadline
        for job in manager.views():
            if job["state"] == "needs_review" and job["id"] not in asked:
                manager.save([job["id"]])
                asked.add(job["id"])
        time.sleep(0.002)
    # Some leave the queue, wherever they've got to.
    for job_id in added[::3]:
        manager.remove(job_id)
    assert manager.wait_until_idle(10)
    removed = set(added[::3])

    def finished() -> bool:
        last = {fakes.event_job_id(event): event for event in window.events}
        return all(
            job_id in last
            and (last[job_id]["type"] == "job_removed") == (job_id in removed)
            and (job_id in removed or last[job_id]["job"]["state"] == "saved")
            for job_id in added
        )

    with window.changed:
        assert window.changed.wait_for(finished, 10), window.events[-5:]
    bus.close()
    manager.shut_down()
    assert any(event["type"] == "progress" for event in window.events)
    for job_id in added:
        check_job_order([event for event in window.events if fakes.event_job_id(event) == job_id])
