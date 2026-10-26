"""Tests for a set in queue.json: it comes back after a restart with its tracks, their edits and
their audio, and the dicts made from a snapshot never pick up a later change to a track."""

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import queue_file
from scmp3.app.events import Deferred, Event
from scmp3.app.queue_file import QueueFile


def load_set_job_fakes() -> ModuleType:
    """tests/fixtures/set_job_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_set_job_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "set_job_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


sets = load_set_job_fakes()
ALBUM = sets.ALBUM
Harness = Any


class Runs:
    """The job managers a test starts, so each one is closed at the end."""

    def __init__(self, library: Path) -> None:
        self.library = library
        self.started: list[Harness] = []

    def first(self, **settings: Any) -> Harness:
        queue = QueueFile(queue_file.default_path())
        harness = sets.make_set_harness(self.library, queue=queue, **settings)
        harness.manager.start_up()
        self.started.append(harness)
        return harness

    def restart(self, harness: Harness) -> Harness:
        again = sets.restarted(harness)
        self.started.append(again)
        return again


@pytest.fixture
def runs(tmp_path: Path) -> Iterator[Runs]:
    runs = Runs(tmp_path / "library")
    yield runs
    for harness in runs.started:
        harness.manager.shut_down()


def downloaded_set(jobs: Harness) -> str:
    """The album, with every track resolved and downloaded and the cover read."""
    set_id = jobs.add(ALBUM)
    jobs.pools.run("resolve")
    jobs.pools.run("resolve")
    jobs.pools.run("misc")
    jobs.pools.run("download")
    return set_id


def tracks(jobs: Harness, set_id: str) -> list[dict[str, Any]]:
    return jobs.job(set_id)["children"]


def kept_jobs() -> list[dict[str, Any]]:
    return json.loads(queue_file.default_path().read_text(encoding="utf-8"))["jobs"]


def test_a_set_comes_back_with_its_tracks_their_edits_and_their_audio(runs: Runs) -> None:
    jobs = runs.first()
    set_id = downloaded_set(jobs)
    first, second, third = (track["id"] for track in tracks(jobs, set_id)[:3])
    # Every edit is made after the set was added and its tracks resolved.
    jobs.manager.update_fields(set_id, {"album": "Night Drives (Deluxe)"})
    jobs.manager.update_fields(second, {"title": "Second Track"})
    jobs.manager.set_style(first, "feat_in_title")
    jobs.manager.set_included(set_id, third, False)
    before = tracks(jobs, set_id)
    again = runs.restart(jobs)
    view = again.job(set_id)
    assert view["state"] == "needs_review"
    assert view["overrides"] == {"album": "Night Drives (Deluxe)"}
    after = view["children"]
    assert [track["id"] for track in after] == [track["id"] for track in before]
    assert after[1]["tags"]["title"] == "Second Track"
    assert after[0]["style"] == "feat_in_title"
    assert [track["included"] for track in after] == [True, True, False, True, True]
    assert [track["tags"]["track_no"] for track in after if track["included"]] == [1, 2, 3, 4]
    assert [track["staged_audio"] for track in after] == [track["staged_audio"] for track in before]
    assert all(Path(track["staged_audio"]).is_file() for track in after)
    # Nothing was listed, resolved or downloaded again.
    assert {step for step, _ in again.steps.calls} <= {"restore", "duplicate_of"}


def test_a_sets_saved_tracks_come_back_saved_and_the_rest_keep_their_numbers(
    runs: Runs,
) -> None:
    jobs = runs.first()
    set_id = downloaded_set(jobs)
    first = tracks(jobs, set_id)[0]["id"]
    jobs.manager.save([first])
    jobs.pools.run_all()
    again = runs.restart(jobs)
    restored = tracks(again, set_id)
    assert [track["state"] for track in restored] == ["saved"] + ["needs_review"] * 4
    assert again.job(set_id)["album_settled"] is True
    again.manager.save([set_id])
    again.pools.run_all()
    assert [(call.tags.track_no, call.tags.track_total) for call in again.steps.set_saves] == [
        (n, 5) for n in range(2, 6)
    ]
    assert again.job(set_id)["state"] == "saved"


def test_a_set_still_being_listed_is_listed_again_after_a_restart(runs: Runs) -> None:
    jobs = runs.first()
    set_id = jobs.add(ALBUM)
    again = runs.restart(jobs)
    assert again.job(set_id)["state"] == "queued"
    again.pools.run("resolve")
    assert len(tracks(again, set_id)) == 5


def test_a_sets_finished_save_leaves_queue_json(runs: Runs) -> None:
    jobs = runs.first()
    set_id = downloaded_set(jobs)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert jobs.manager.keep_queue()
    assert kept_jobs() == []


def test_queue_json_keeps_a_tracks_edits_made_after_the_set_was_added(runs: Runs) -> None:
    jobs = runs.first()
    set_id = downloaded_set(jobs)
    second = tracks(jobs, set_id)[1]["id"]
    assert jobs.manager.keep_queue()
    jobs.manager.update_fields(second, {"artist": "Kaia, Lou V"})
    assert jobs.manager.keep_queue()
    (entry,) = kept_jobs()
    assert entry["children"][1]["job"]["overrides"] == {"artist": "Kaia, Lou V"}


class LaterBus(sets.fakes.RecordingBus):
    """A bus that builds each job event only when the test asks, as the real bus builds them
    later on its own thread."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting: list[Deferred] = []

    def emit(self, event: Event | Deferred) -> None:
        if isinstance(event, Deferred):
            self.waiting.append(event)
        else:
            super().emit(event)


def test_an_event_built_later_shows_a_track_as_it_was_when_it_changed(tmp_path: Path) -> None:
    jobs = sets.make_set_harness(tmp_path / "library")
    bus = LaterBus()
    jobs.manager._flow._events = bus
    try:
        set_id = jobs.add(ALBUM)
        jobs.pools.run("resolve")
        jobs.pools.run("resolve")
        first = jobs.job(set_id)["children"][0]["id"]
        jobs.manager.update_fields(first, {"title": "As It Was"})
        sent = bus.waiting[-1]
        jobs.manager.update_fields(first, {"title": "Changed Later"})
        jobs.manager.set_included(set_id, first, False)
        built = sent.build()
        (track, *_) = built["job"]["children"]
        assert track["tags"]["title"] == "As It Was"
        assert track["included"] is True
    finally:
        jobs.manager.shut_down()


def test_a_cleanup_setting_reaches_a_sets_tracks_and_queue_json(runs: Runs) -> None:
    jobs = runs.first()
    link = sets.track_link(ALBUM, 1)
    jobs.steps.sources[link] = sets.fakes.source_for(link, "Track 1 (prod. Hazy)")
    set_id = downloaded_set(jobs)
    assert tracks(jobs, set_id)[0]["tags"]["title"] == "Track 1"
    jobs.change_settings(remove_producer_credits=False)
    jobs.pools.run("misc")
    first = tracks(jobs, set_id)[0]
    assert first["tags"]["title"] == "Track 1 (prod. Hazy)"
    assert jobs.manager.keep_queue()
    (entry,) = kept_jobs()
    assert entry["children"][0]["job"]["suggestion"]["changes"][0]["enabled"] is False
    again = runs.restart(jobs)
    assert tracks(again, set_id)[0]["tags"]["title"] == "Track 1 (prod. Hazy)"


def test_a_settings_change_works_out_a_sets_places_only_for_a_name_question(
    runs: Runs, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scmp3.app import job_settings

    jobs = runs.first()
    downloaded_set(jobs)
    worked_out: list[int] = []
    real = job_settings.track_places

    def counted(set_job: Any, tracks: Any) -> Any:
        worked_out.append(len(tracks))
        return real(set_job, tracks)

    monkeypatch.setattr(job_settings, "track_places", counted)
    jobs.change_settings(remove_producer_credits=False)
    jobs.pools.run("misc")
    assert worked_out == []
