"""Tests for keeping the queue in queue.json and bringing it back at the next start.

Each test closes the job manager and starts a new one on the same settings and queue.json, the
way quitting and opening the app does.
"""

import importlib.util
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import queue_file
from scmp3.app.job_artists import ArtistList
from scmp3.app.queue_file import QueueFile
from scmp3.core import paths
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


class Runs:
    """The job managers a test starts, so each one is closed at the end."""

    def __init__(self, library: Path) -> None:
        self.library = library
        self.started: list[Harness] = []

    def first(self, **settings: Any) -> Harness:
        queue = QueueFile(queue_file.default_path())
        harness = fakes.make_harness(self.library, queue=queue, **settings)
        harness.manager.start_up()
        self.started.append(harness)
        return harness

    def restart(self, harness: Harness) -> Harness:
        again = fakes.restarted(harness)
        self.started.append(again)
        return again

    def reopen(self, harness: Harness) -> Harness:
        """Start again after a test closed `harness` itself, to change what it left behind."""
        again = fakes.reopened(harness)
        self.started.append(again)
        return again


@pytest.fixture
def runs(tmp_path: Path) -> Iterator[Runs]:
    runs = Runs(tmp_path / "library")
    yield runs
    for harness in runs.started:
        harness.manager.shut_down()


def staged_job(jobs: Harness, url: str = TRACK) -> str:
    """A job in review whose audio is downloaded."""
    job_id = jobs.add_and_resolve(url)
    jobs.pools.run("download")
    assert jobs.job(job_id)["staged_audio"]
    return job_id


def kept_jobs() -> list[dict[str, Any]]:
    return json.loads(queue_file.default_path().read_text(encoding="utf-8"))["jobs"]


# Coming back


def test_a_job_in_review_comes_back_with_its_audio_without_downloading_again(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    audio = jobs.job(job_id)["staged_audio"]
    again = runs.restart(jobs)
    job = again.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"] == audio
    assert Path(audio).is_file()
    again.manager.save([job_id])
    again.pools.run_all()
    assert again.job(job_id)["state"] == "saved"
    assert again.steps.steps_for(job_id) == ["restore", "finalize"]


def test_edits_cleanups_style_and_cover_come_back(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    change = jobs.job(job_id)["suggestion"]["changes"][0]
    jobs.manager.update_fields(job_id, {"album": "Night Drives"})
    jobs.manager.toggle_change(job_id, change["id"], False)
    jobs.manager.set_style(job_id, "feat_in_title")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.pools.run("misc")
    before = jobs.job(job_id)
    again = runs.restart(jobs)
    after = again.job(job_id)
    for key in ("overrides", "style", "artwork", "suggestion", "tags", "filename", "cover_url"):
        assert after[key] == before[key], key
    # The cleanup switched by hand stays switched when the settings change.
    again.change_settings(remove_promo_text=False)
    again.pools.run("misc")
    (toggled,) = [
        item for item in again.job(job_id)["suggestion"]["changes"] if item["id"] == change["id"]
    ]
    assert toggled["enabled"] is False


def test_an_artist_list_comes_back_and_still_wins_over_the_cleanups(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    jobs.manager.set_artists(job_id, ArtistList(("Mara", "Kaia"), ("Lou V",)))
    before = jobs.job(job_id)
    assert jobs.manager.keep_queue() is True
    (entry,) = kept_jobs()
    assert entry["artists"] == {"main": ["Mara", "Kaia"], "featured": ["Lou V"]}
    again = runs.restart(jobs)
    after = again.job(job_id)
    for key in ("suggestion", "tags", "filename", "artists_edited"):
        assert after[key] == before[key], key
    # A settings change after the restart rebuilds the suggestion, and the list stays.
    again.change_settings(remove_producer_credits=False)
    again.pools.run("misc")
    job = again.job(job_id)
    assert job["suggestion"]["main_artists"] == ["Mara", "Kaia"]
    assert job["tags"]["title"] == "Midnight Drive [prod. Hazy]"
    # Reset still goes back to the suggestion.
    again.manager.set_artists(job_id, None)
    assert again.job(job_id)["suggestion"]["main_artists"] == ["Kaia"]


def test_a_job_with_an_unusable_artist_list_is_left_out(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add_and_resolve()
    jobs.manager.shut_down()
    path = queue_file.default_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jobs"][0]["artists"] = {"main": "Kaia", "featured": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    again = runs.reopen(jobs)
    assert job_id not in [job["id"] for job in again.manager.views()]
    assert len(list(path.parent.glob("queue.bad-*.json"))) == 1


def test_the_queue_comes_back_in_order_in_the_first_snapshot(runs: Runs) -> None:
    jobs = runs.first()
    first = jobs.add_and_resolve(TRACK)
    second = jobs.add(OTHER)
    again = runs.restart(jobs)
    assert [job["id"] for job in again.manager.views()] == [first, second]


def test_a_job_still_fetching_its_info_fetches_it_again(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add()
    again = runs.restart(jobs)
    assert again.job(job_id)["state"] == "queued"
    again.pools.run("resolve")
    assert again.job(job_id)["state"] == "needs_review"
    assert again.steps.steps_for(job_id) == ["prepare"]


def test_a_job_that_was_saving_comes_back_in_review(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    assert jobs.job(job_id)["state"] == "saving"
    again = runs.restart(jobs)
    assert again.job(job_id)["state"] == "needs_review"
    # Its download starts again, as for any job in review, but nothing saves without a Save.
    again.pools.run_all()
    job = again.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"]


def test_saved_skipped_and_removed_jobs_dont_come_back(runs: Runs) -> None:
    jobs = runs.first()
    saved = staged_job(jobs, TRACK)
    skipped = jobs.add_and_resolve(OTHER)
    removed = jobs.add("https://soundcloud.com/kaia/lantern")
    waiting = jobs.add("https://soundcloud.com/kaia/static-bloom")
    jobs.manager.save([saved])
    jobs.pools.run_all()
    jobs.manager.skip(skipped)
    jobs.manager.remove(removed)
    again = runs.restart(jobs)
    assert [job["id"] for job in again.manager.views()] == [waiting]


def test_a_failed_job_comes_back_failed_and_retry_carries_on(runs: Runs) -> None:
    jobs = runs.first()
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk, "the drive is full"))
    job_id = staged_job(jobs)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["error"]["kind"] == "disk"
    again = runs.restart(jobs)
    assert again.job(job_id)["state"] == "error"
    assert again.job(job_id)["error"]["kind"] == "disk"
    again.manager.retry(job_id)
    again.pools.run_all()
    assert again.job(job_id)["state"] == "saved"
    assert "fetch" not in again.steps.steps_for(job_id)


def test_a_job_that_failed_to_fetch_its_info_fetches_it_on_retry(runs: Runs) -> None:
    jobs = runs.first()
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.service_down, "503"))
    job_id = jobs.add_and_resolve()
    again = runs.restart(jobs)
    assert again.job(job_id)["error"]["kind"] == "service_down"
    again.manager.retry(job_id)
    again.pools.run("resolve")
    assert again.job(job_id)["state"] == "needs_review"


def test_an_offline_job_retries_by_itself_after_a_restart(runs: Runs) -> None:
    jobs = runs.first()
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.offline, "no network"))
    job_id = jobs.add_and_resolve()
    again = runs.restart(jobs)
    (timer,) = again.timers.waiting()
    assert timer.delay == 30.0
    timer.callback()
    again.pools.run("resolve")
    assert again.job(job_id)["state"] == "needs_review"


def test_staged_audio_that_has_gone_is_downloaded_again(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    jobs.manager.shut_down()
    Path(jobs.job(job_id)["staged_audio"]).unlink()
    again = runs.reopen(jobs)
    assert again.job(job_id)["staged_audio"] is None
    again.pools.run("download")
    assert again.job(job_id)["staged_audio"]
    assert again.steps.steps_for(job_id) == ["restore", "fetch"]


def test_a_download_stopped_by_hand_stays_stopped(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add_and_resolve()
    jobs.manager.cancel(job_id)
    again = runs.restart(jobs)
    again.pools.run_all()
    assert "fetch" not in again.steps.steps_for(job_id)


def test_the_duplicate_warning_comes_back(runs: Runs) -> None:
    jobs = runs.first()
    entry = fakes.HistoryEntry(
        source_id="soundcloud:tracks:1",
        url_norm=TRACK,
        saved_path=Path("Kaia - Midnight Drive.mp3"),
        title="Midnight Drive",
        artist="Kaia",
        album="Midnight Drive",
        format="mp3",
    )
    jobs.steps.duplicates[TRACK] = entry
    job_id = jobs.add_and_resolve()
    again = runs.restart(jobs)
    assert again.job(job_id)["duplicate"]["url_norm"] == TRACK


def test_a_link_that_came_back_counts_as_queued(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add_and_resolve()
    again = runs.restart(jobs)
    answer = again.manager.add_inputs(TRACK)
    assert answer["rejected"] == [{"input": TRACK, "reason": "ALREADY_IN_QUEUE", "job_id": job_id}]
    assert again.manager.is_queued(TRACK)


# The staging folders


def make_old(folder: Path) -> None:
    eight_days_ago = time.time() - 8 * 24 * 60 * 60
    os.utime(folder, (eight_days_ago, eight_days_ago))


def test_the_startup_purge_keeps_the_staging_folders_of_jobs_that_came_back(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    jobs.manager.shut_down()
    leftover = paths.staging_dir() / "leftover"
    leftover.mkdir()
    for folder in (paths.staging_dir() / job_id, leftover):
        make_old(folder)
    again = runs.reopen(jobs)
    again.pools.run("misc")
    assert (paths.staging_dir() / job_id).is_dir()
    assert not leftover.exists()


# The file


def test_queue_json_holds_the_jobs_not_saved_yet(runs: Runs) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    assert jobs.manager.keep_queue() is True
    (entry,) = kept_jobs()
    assert entry["job"]["id"] == job_id
    assert entry["staged"]["path"] == jobs.job(job_id)["staged_audio"]


def test_a_damaged_queue_json_is_moved_aside_and_the_queue_starts_empty(runs: Runs) -> None:
    path = queue_file.default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{ not json")
    jobs = runs.first()
    assert jobs.manager.views() == []
    (backup,) = path.parent.glob("queue.bad-*.json")
    assert backup.read_bytes() == b"{ not json"


def test_a_job_that_cant_be_read_is_left_out_and_the_file_kept_aside(runs: Runs) -> None:
    jobs = runs.first()
    job_id = jobs.add_and_resolve()
    jobs.manager.shut_down()
    path = queue_file.default_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    broken = {**data["jobs"][0], "job": {**data["jobs"][0]["job"], "id": "../outside"}}
    data["jobs"].append(broken)
    path.write_text(json.dumps(data), encoding="utf-8")
    again = runs.reopen(jobs)
    assert [job["id"] for job in again.manager.views()] == [job_id]
    assert len(list(path.parent.glob("queue.bad-*.json"))) == 1


def test_a_queue_json_that_cant_be_opened_is_never_written_over(runs: Runs) -> None:
    # A folder in its place can't be read, the way a file locked by another program can't.
    queue_file.default_path().mkdir(parents=True)
    jobs = runs.first()
    jobs.add()
    assert jobs.manager.keep_queue() is False
    assert queue_file.default_path().is_dir()


def test_a_track_saved_just_before_a_crash_comes_back_with_its_duplicate_warning(
    runs: Runs,
) -> None:
    jobs = runs.first()
    job_id = staged_job(jobs)
    jobs.manager.keep_queue()
    # The save lands in the history, and the app dies before queue.json hears of it.
    entry = fakes.HistoryEntry(
        source_id="soundcloud:tracks:1",
        url_norm=TRACK,
        saved_path=Path("Kaia - Midnight Drive.mp3"),
        title="Midnight Drive",
        artist="Kaia",
        album="Midnight Drive",
        format="mp3",
    )
    again = fakes.reopened(jobs)
    runs.started.append(again)
    assert again.job(job_id)["duplicate"] is None
    again.steps.duplicates[TRACK] = entry
    again.pools.run("misc")
    assert again.job(job_id)["duplicate"]["url_norm"] == TRACK
    assert again.events.last_job(job_id)["duplicate"]["url_norm"] == TRACK


def test_temp_files_a_crash_left_next_to_queue_json_are_deleted(runs: Runs) -> None:
    folder = queue_file.default_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    leftover = folder / ".queue.json.0123456789abcdef.tmp"
    leftover.write_text("half a queue", encoding="utf-8")
    other = folder / "history.db"
    other.write_bytes(b"")
    runs.first()
    assert not leftover.exists()
    assert other.exists()
