"""Tests for the job manager with many jobs.

They cover pacing downloads, finding queued links, saving them all, and building the UI's dicts
outside the lock.
"""

import importlib.util
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import job_record
from scmp3.app.events import EventBus
from scmp3.app.job_downloads import DOWNLOAD_SPACING
from scmp3.app.job_flow import RESOLVE_SPACING
from scmp3.app.jobs import JobManager
from scmp3.core import paths


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
SHORT = "https://on.soundcloud.com/AbCdEfGh"
Harness = Any
# The longest a call may wait for the lock, as the bridge's calls may take.
CALL_BUDGET = 0.05


@pytest.fixture
def jobs(tmp_path: Path) -> Harness:
    return fakes.make_harness(tmp_path / "library")


def links(count: int) -> list[str]:
    return [f"https://soundcloud.com/kaia/track-{number}" for number in range(count)]


# Pacing


def test_youtube_downloads_start_at_least_5_seconds_apart() -> None:
    assert DOWNLOAD_SPACING == {"youtube": 5.0}


def test_the_download_pool_paces_each_download_by_its_source(jobs: Harness) -> None:
    pool = jobs.pools["download"]
    assert pool.spacing == DOWNLOAD_SPACING
    video = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    youtube = replace(fakes.source_for(OTHER, "Static Bloom"), kind="youtube", url=video)
    jobs.steps.sources[OTHER] = youtube
    jobs.add_and_resolve(TRACK)
    jobs.add_and_resolve(OTHER)
    assert pool.paces == ["soundcloud", "youtube"]


def test_youtube_links_resolve_one_at_a_time_2_seconds_apart(jobs: Harness) -> None:
    pool = jobs.pools["resolve"]
    assert (pool.spacing, pool.serial) == (RESOLVE_SPACING, {"youtube"})
    assert RESOLVE_SPACING == {"youtube": 2.0}
    jobs.add(TRACK)
    jobs.add("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert pool.paces == ["soundcloud", "youtube"]


def test_a_removed_jobs_resolve_gives_up_its_paced_turn(jobs: Harness) -> None:
    job_id = jobs.add("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    (wanted,) = jobs.pools["resolve"].wanted
    assert wanted() is True
    jobs.manager.remove(job_id)
    assert wanted() is False


def test_a_removed_jobs_download_gives_up_its_paced_turn(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(TRACK)
    (wanted,) = jobs.pools["download"].wanted
    assert wanted() is True
    jobs.manager.remove(job_id)
    assert wanted() is False


def test_a_cancelled_download_gives_up_its_paced_turn(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(TRACK)
    (wanted,) = jobs.pools["download"].wanted
    jobs.manager.cancel(job_id)
    assert wanted() is False


def test_the_pool_size_follows_the_downloads_at_once_setting(tmp_path: Path) -> None:
    jobs = fakes.make_harness(tmp_path / "library", concurrent_downloads=3)
    assert jobs.pools["download"].size == 3
    jobs.change_settings(concurrent_downloads=1)
    assert jobs.pools["download"].size == 1


# Finding queued links


def test_a_track_queued_through_its_short_link_counts_as_queued(jobs: Harness) -> None:
    jobs.steps.sources[SHORT] = fakes.source_for(TRACK)
    job_id = jobs.add_and_resolve(SHORT)
    assert jobs.manager.is_queued(SHORT)
    assert jobs.manager.is_queued(TRACK)
    answer = jobs.manager.add_inputs(TRACK)
    assert answer["rejected"] == [{"input": TRACK, "reason": "ALREADY_IN_QUEUE", "job_id": job_id}]


def test_a_removed_jobs_link_can_be_added_again(jobs: Harness) -> None:
    jobs.steps.sources[SHORT] = fakes.source_for(TRACK)
    job_id = jobs.add_and_resolve(SHORT)
    jobs.manager.remove(job_id)
    assert not jobs.manager.is_queued(SHORT)
    assert not jobs.manager.is_queued(TRACK)
    assert jobs.manager.add_inputs(TRACK)["rejected"] == []


def test_two_short_links_to_one_track_both_count_until_both_are_gone(jobs: Harness) -> None:
    second_short = "https://on.soundcloud.com/ZyXwVuTs"
    jobs.steps.sources[SHORT] = fakes.source_for(TRACK)
    jobs.steps.sources[second_short] = fakes.source_for(TRACK)
    first = jobs.add_and_resolve(SHORT)
    second = jobs.add_and_resolve(second_short)
    jobs.manager.remove(first)
    assert jobs.manager.is_queued(TRACK)
    jobs.manager.remove(second)
    assert not jobs.manager.is_queued(TRACK)


def test_a_link_is_found_at_once_with_2000_queued(jobs: Harness) -> None:
    jobs.manager.add_inputs("\n".join(links(2000)))
    start = time.perf_counter()
    for _ in range(100):
        assert not jobs.manager.is_queued("https://soundcloud.com/kaia/not-queued")
    assert (time.perf_counter() - start) / 100 < 0.001


# Saving them all


def test_saving_every_job_in_review_saves_each_with_its_own_tags(jobs: Harness) -> None:
    for number, link in enumerate(links(10)):
        jobs.steps.sources[link] = fakes.source_for(link, f"Track {number}")
    added = jobs.manager.add_inputs("\n".join(links(10)))["added"]
    jobs.pools.run("resolve")
    jobs.manager.update_fields(added[0], {"title": "Typed by hand"})
    assert jobs.manager.save(added) == {}
    jobs.pools.run_all()
    assert {jobs.job(job_id)["state"] for job_id in added} == {"saved"}
    titles = {call.tags.title for call in jobs.steps.finalized}
    assert "Typed by hand" in titles
    assert len(titles) == 10


def test_saving_all_leaves_out_jobs_that_arent_waiting_for_review(jobs: Harness) -> None:
    waiting = jobs.add_and_resolve(TRACK)
    fetching = jobs.add(OTHER)
    jobs.manager.save([waiting, fetching])
    assert jobs.job(waiting)["state"] == "saving"
    assert jobs.job(fetching)["state"] == "queued"


# The UI's dicts


def manager_on_a_real_bus(library: Path) -> tuple[JobManager, Any, EventBus]:
    """A job manager whose events wait in a real bus, so their dicts aren't built until sent."""
    store = fakes.SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(library)})
    pools = fakes.ManualPools()
    bus = EventBus()
    manager = JobManager(
        store, bus, steps=fakes.FakeSteps(), covers=fakes.FakeCovers(), make_pool=pools
    )
    return manager, pools, bus


def test_the_ui_dicts_are_built_outside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, pools, _ = manager_on_a_real_bus(tmp_path / "library")
    manager.add_inputs("\n".join(links(20)))
    pools.run("resolve")
    real_view = job_record.job_view

    def slow_view(record: Any, settings: Any) -> dict[str, Any]:
        time.sleep(0.01)
        return real_view(record, settings)

    monkeypatch.setattr(job_record, "job_view", slow_view)
    waits: list[float] = []

    def call_meanwhile() -> None:
        start = time.perf_counter()
        manager.is_queued(TRACK)
        waits.append(time.perf_counter() - start)

    reader = threading.Thread(target=call_meanwhile)
    building = threading.Thread(target=manager.views)
    building.start()
    time.sleep(0.02)
    reader.start()
    reader.join(5)
    building.join(5)
    assert waits and waits[0] < CALL_BUDGET


def test_a_job_dict_is_built_once_per_change(
    jobs: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = jobs.add_and_resolve()
    builds: list[str] = []
    real_view = job_record.job_view

    def counted(record: Any, settings: Any) -> dict[str, Any]:
        builds.append(record.id)
        return real_view(record, settings)

    monkeypatch.setattr(job_record, "job_view", counted)
    jobs.manager.update_fields(job_id, {"title": "New"})
    for _ in range(3):
        jobs.manager.views()
    # The event and every snapshot after it share one build.
    assert builds == [job_id]


def test_a_snapshot_shows_the_latest_download_progress(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.progress = (25.0, 60.0)
    seen: list[float] = []
    jobs.steps.during_fetch = lambda: seen.append(jobs.job(job_id)["progress"]["pct"])
    jobs.pools.run("download")
    assert seen == [60.0]


def test_a_burst_of_changes_builds_the_event_once_on_the_bus_thread(tmp_path: Path) -> None:
    manager, pools, bus = manager_on_a_real_bus(tmp_path / "library")
    (job_id,) = manager.add_inputs(TRACK)["added"]
    pools.run("resolve")
    for number in range(5):
        manager.update_fields(job_id, {"title": f"Draft {number}"})
    window = RecordingWindow()
    bus.attach(window)
    with window.changed:
        assert window.changed.wait_for(lambda: window.events, 5)
    bus.close()
    (event,) = window.events
    assert event["job"]["tags"]["title"] == "Draft 4"


class RecordingWindow:
    """A window that keeps the events the bus sends it."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.changed = threading.Condition()

    def evaluate_js(self, script: str) -> None:
        batch = json.loads(script.removeprefix("for (const event of ").split(") window")[0])
        with self.changed:
            self.events.extend(batch)
            self.changed.notify_all()
