"""Tests for the job manager: adding, downloading, saving, skipping, removing and cancelling.

Each runs step by step on the fakes, so the order of every transition in TECH_SPEC §3 is the test's.
"""

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.urls import SOUNDCLOUD_NOT_TRACK, UnsupportedInput


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


@pytest.fixture
def no_background(tmp_path: Path) -> Harness:
    """A harness where nothing downloads until Save."""
    return fakes.make_harness(tmp_path / "library", download_while_reviewing=False)


def staging(job_id: str) -> Path:
    return paths.staging_dir() / job_id


# Adding


def test_adding_a_link_queues_a_job_and_says_so(jobs: Harness) -> None:
    answer = jobs.manager.add_inputs(TRACK)
    (job_id,) = answer["added"]
    assert answer["rejected"] == []
    assert jobs.events.job_states(job_id) == ["queued"]
    assert jobs.job(job_id)["input"] == TRACK
    assert jobs.job(job_id)["kind"] == "track"


def test_resolving_goes_through_resolving_to_review(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.events.job_states(job_id) == ["queued", "resolving", "needs_review"]
    job = jobs.job(job_id)
    assert job["source"]["url"] == TRACK
    assert job["suggestion"]["base_title"] == "Midnight Drive"


def test_a_job_in_review_carries_what_the_ui_shows(jobs: Harness) -> None:
    job = jobs.job(jobs.add_and_resolve())
    assert job["tags"]["title"] == "Midnight Drive"
    assert job["tags"]["artist"] == "Kaia, Lou V"
    assert job["filename"] == "Kaia/Kaia, Lou V - Midnight Drive.mp3"
    assert job["format"]["format"]["format_id"] == "hls_aac_160k"
    assert job["cover_url"] == fakes.ARTWORK
    assert job["collision"] is None


def test_the_cover_size_arrives_once_the_cover_is_read(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.job(job_id)["cover_size"] is None
    jobs.pools.run("misc")
    assert jobs.job(job_id)["cover_size"] == {"width": 500, "height": 500}


def test_the_file_name_follows_the_audio_format_setting(tmp_path: Path) -> None:
    jobs = fakes.make_harness(tmp_path / "library", audio_format="m4a")
    assert jobs.job(jobs.add_and_resolve())["filename"].endswith(".m4a")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("https://soundcloud.com/kaia/sets/night-drives", "SETS_NOT_SUPPORTED"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YOUTUBE_NOT_SUPPORTED"),
        ("https://www.youtube.com/playlist?list=PL123", "YOUTUBE_PLAYLIST"),
        ("https://soundcloud.com/kaia", "SOUNDCLOUD_NOT_TRACK"),
        ("https://example.com/song", "NOT_SUPPORTED"),
        ("hello", "NOT_SUPPORTED"),
    ],
)
def test_inputs_that_cant_be_saved_are_rejected_with_a_code(
    jobs: Harness, text: str, reason: str
) -> None:
    answer = jobs.manager.add_inputs(text)
    assert answer == {"added": [], "rejected": [{"input": text, "reason": reason}]}
    assert jobs.events.events == []


def test_files_are_rejected_until_re_tagging_exists(jobs: Harness, tmp_path: Path) -> None:
    song = tmp_path / "old favorite.mp3"
    song.write_bytes(b"audio")
    answer = jobs.manager.add_inputs(str(song))
    assert answer["rejected"] == [{"input": str(song), "reason": "FILES_NOT_SUPPORTED"}]


def test_a_paste_that_is_too_long_says_so_without_an_input(jobs: Harness) -> None:
    text = " ".join(f"https://soundcloud.com/kaia/track-{n}" for n in range(2_001))
    answer = jobs.manager.add_inputs(text)
    assert len(answer["added"]) == 2_000
    assert answer["rejected"] == [{"input": "", "reason": "PASTE_TOO_LONG"}]


def test_a_link_already_in_the_queue_is_rejected_with_its_job(jobs: Harness) -> None:
    job_id = jobs.add()
    copy = "https://www.soundcloud.com/kaia/midnight-drive?si=abc"
    answer = jobs.manager.add_inputs(f"{copy}\n{OTHER}")
    assert answer["rejected"] == [{"input": copy, "reason": "ALREADY_IN_QUEUE", "job_id": job_id}]
    assert len(answer["added"]) == 1


def test_a_resolved_link_counts_as_queued_by_its_source_link(jobs: Harness) -> None:
    jobs.steps.sources["https://soundcloud.com/kaia/old-name"] = fakes.source_for(TRACK)
    job_id = jobs.add_and_resolve("https://soundcloud.com/kaia/old-name")
    answer = jobs.manager.add_inputs(TRACK)
    assert answer["rejected"][0]["job_id"] == job_id


def test_a_short_link_to_something_else_leaves_the_queue_with_its_code(jobs: Harness) -> None:
    short = "https://on.soundcloud.com/AbCdEfGh"
    jobs.steps.prepare_errors.append(UnsupportedInput(SOUNDCLOUD_NOT_TRACK))
    job_id = jobs.add_and_resolve(short)
    assert jobs.manager.views() == []
    assert jobs.events.events[-2:] == [
        {"type": "job_removed", "job_id": job_id},
        {"type": "rejected", "rejected": [{"input": short, "reason": "SOUNDCLOUD_NOT_TRACK"}]},
    ]


# Downloading in the background


def test_the_download_starts_right_after_resolving(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"] == str(staging(job_id) / "audio.mp3")
    assert job["progress"] is None


def test_download_progress_goes_out_as_progress_events(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    progress = [event for event in jobs.events.for_job(job_id) if event["type"] == "progress"]
    assert progress == [
        {"type": "progress", "job_id": job_id, "pct": 50.0, "speed": 1000.0, "eta": 3.0}
    ]


def test_a_running_download_shows_its_progress_in_the_job(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    seen: list[Any] = []
    jobs.steps.during_fetch = lambda: seen.append(jobs.job(job_id)["progress"]["pct"])
    jobs.pools.run("download")
    assert seen == [50.0]


def test_without_the_background_setting_nothing_downloads_before_save(
    no_background: Harness,
) -> None:
    job_id = no_background.add_and_resolve()
    assert not no_background.pools["download"].tasks
    assert no_background.steps.steps_for(job_id) == ["prepare"]


# Saving


def test_saving_downloaded_audio_saves_it_into_the_library(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run_all()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "saved"
    assert job["output_path"] == str(jobs.library / "Kaia" / "Kaia, Lou V - Midnight Drive.mp3")
    assert Path(job["output_path"]).read_bytes() == b"converted audio"
    assert job["staged_audio"] is None
    assert not staging(job_id).exists()
    assert jobs.events.job_states(job_id)[-2:] == ["saving", "saved"]
    assert job["cover_missing"] is False


def test_a_save_into_a_library_that_cant_be_listed_warns_once(
    jobs: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The name check and the move both look for folders to reuse (T-019).
    jobs.library.mkdir(parents=True)
    list_folder = os.scandir

    def refuse_the_library(path: Any) -> Any:
        # shutil.rmtree lists folders by file descriptor on macOS and Linux.
        if not isinstance(path, int) and Path(path) == jobs.library:
            raise PermissionError(13, "Permission denied")
        return list_folder(path)

    monkeypatch.setattr(os, "scandir", refuse_the_library)
    job_id = jobs.add_and_resolve()
    jobs.pools.run_all()
    with caplog.at_level(logging.WARNING, logger="scmp3.core.library"):
        jobs.manager.save([job_id])
        jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert [record.getMessage() for record in caplog.records] == [
        "Couldn't look for a folder to reuse: Permission denied"
    ]


def test_a_save_without_its_cover_says_so_on_the_saved_job(jobs: Harness) -> None:
    jobs.steps.cover_errors.append(ScmpError(ErrorKind.unavailable, "the cover answered 404"))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "saved"
    assert job["cover_missing"] is True


def test_saving_before_the_download_finishes_waits_in_saving(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    # The source's cover is read, so only the download is left to wait for.
    jobs.pools.run("misc")
    jobs.manager.save([job_id])
    job = jobs.job(job_id)
    assert job["state"] == "saving"
    assert job["staged_audio"] is None
    assert not jobs.pools["save"].tasks
    jobs.pools.run("download")
    assert jobs.pools["save"].tasks
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"


def test_saving_without_the_background_download_downloads_first(no_background: Harness) -> None:
    job_id = no_background.add_and_resolve()
    no_background.manager.save([job_id])
    no_background.pools.run_all()
    assert no_background.job(job_id)["state"] == "saved"
    assert no_background.steps.steps_for(job_id) == ["prepare", "fetch", "finalize"]


def test_a_save_uses_the_hand_edits_style_and_cover(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"title": "  Midnight Drive (Edit) "})
    jobs.manager.set_style(job_id, "feat_in_title")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    (call,) = jobs.steps.finalized
    assert call.overrides == {"title": "  Midnight Drive (Edit) "}
    assert call.tags.title == "  Midnight Drive (Edit) "
    assert call.style == "feat_in_title"
    assert call.cover.kind == "url"


def test_saving_a_job_that_isnt_in_review_does_nothing(jobs: Harness) -> None:
    job_id = jobs.add()
    assert jobs.manager.save([job_id, "gone"]) == {}
    assert jobs.job(job_id)["state"] == "queued"


def test_the_save_waits_for_a_new_cover_being_checked(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.manager.save([job_id])
    assert not jobs.pools["save"].tasks
    jobs.pools.run_all()
    assert jobs.steps.finalized[0].cover.value == "https://example.com/cover.jpg"


def test_a_name_policy_of_skip_leaves_the_job_skipped(tmp_path: Path) -> None:
    jobs = fakes.make_harness(tmp_path / "library", when_file_exists="skip", artist_folders=False)
    (jobs.library / "Kaia, Lou V - Midnight Drive.mp3").parent.mkdir(parents=True)
    (jobs.library / "Kaia, Lou V - Midnight Drive.mp3").write_bytes(b"older")
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "skipped"


# Taken file names under the "ask" policy


@pytest.fixture
def flat_jobs(tmp_path: Path) -> Harness:
    """A harness with artist folders off, so a taken name sits at the top of the library."""
    return fakes.make_harness(tmp_path / "library", artist_folders=False)


def taken(jobs: Harness) -> Path:
    existing = jobs.library / "Kaia, Lou V - Midnight Drive.mp3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"older")
    return existing


def test_a_taken_name_asks_the_user_without_blocking_the_save_worker(flat_jobs: Harness) -> None:
    existing = taken(flat_jobs)
    first = flat_jobs.add_and_resolve()
    second = flat_jobs.add_and_resolve(OTHER)
    flat_jobs.manager.save([first, second])
    flat_jobs.pools.run_all()
    job = flat_jobs.job(first)
    assert job["state"] == "needs_review"
    assert job["collision"] == {"path": str(existing)}
    assert flat_jobs.job(second)["state"] == "saved"
    assert existing.read_bytes() == b"older"


@pytest.mark.parametrize(
    ("choice", "state", "name"),
    [
        ("keep_both", "saved", "Kaia, Lou V - Midnight Drive (2).mp3"),
        ("replace", "saved", "Kaia, Lou V - Midnight Drive.mp3"),
        ("skip", "skipped", None),
    ],
)
def test_the_answer_to_a_taken_name_decides_the_save(
    flat_jobs: Harness, choice: str, state: str, name: str | None
) -> None:
    taken(flat_jobs)
    job_id = flat_jobs.add_and_resolve()
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    assert flat_jobs.manager.answer_collision(job_id, choice) == {}
    flat_jobs.pools.run_all()
    job = flat_jobs.job(job_id)
    assert job["state"] == state
    assert job["collision"] is None
    if name is not None:
        assert job["output_path"] == str(flat_jobs.library / name)


def test_an_edit_drops_the_question_about_the_old_name(flat_jobs: Harness) -> None:
    taken(flat_jobs)
    job_id = flat_jobs.add_and_resolve()
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    flat_jobs.manager.update_fields(job_id, {"title": "Midnight Drive II"})
    assert flat_jobs.job(job_id)["collision"] is None
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    assert flat_jobs.job(job_id)["output_path"].endswith("Midnight Drive II.mp3")


def other_track_file(jobs: Harness) -> Path:
    """Another artist's track with the name the job gets under a {title} file name template."""
    other = jobs.library / "Midnight Drive.mp3"
    other.write_bytes(b"another artist's track")
    return other


def test_an_answer_about_one_file_never_replaces_another(flat_jobs: Harness) -> None:
    existing = taken(flat_jobs)
    other = other_track_file(flat_jobs)
    job_id = flat_jobs.add_and_resolve()
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    assert flat_jobs.job(job_id)["collision"] == {"path": str(existing)}
    # The new template names the job's file after the other track, so the question is dropped.
    flat_jobs.change_settings(filename_template="{title}")
    flat_jobs.pools.run("misc")
    assert flat_jobs.job(job_id)["collision"] is None
    assert flat_jobs.manager.answer_collision(job_id, "replace") == {}
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    assert flat_jobs.job(job_id)["collision"] == {"path": str(other)}
    assert existing.read_bytes() == b"older"
    assert other.read_bytes() == b"another artist's track"


def test_an_answer_for_a_name_that_changed_before_the_save_asks_again(flat_jobs: Harness) -> None:
    existing = taken(flat_jobs)
    other = other_track_file(flat_jobs)
    job_id = flat_jobs.add_and_resolve()
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    flat_jobs.manager.answer_collision(job_id, "replace")
    # The name changes after the answer, while the save waits for the save worker.
    flat_jobs.change_settings(filename_template="{title}")
    flat_jobs.pools.run("save")
    assert flat_jobs.job(job_id)["collision"] == {"path": str(other)}
    assert existing.read_bytes() == b"older"
    assert other.read_bytes() == b"another artist's track"


def test_an_answer_for_a_name_that_changed_to_a_free_one_saves_without_asking(
    flat_jobs: Harness,
) -> None:
    existing = taken(flat_jobs)
    job_id = flat_jobs.add_and_resolve()
    flat_jobs.manager.save([job_id])
    flat_jobs.pools.run_all()
    flat_jobs.manager.answer_collision(job_id, "replace")
    flat_jobs.change_settings(filename_template="{title}")
    flat_jobs.pools.run_all()
    assert flat_jobs.job(job_id)["output_path"] == str(flat_jobs.library / "Midnight Drive.mp3")
    assert existing.read_bytes() == b"older"


def test_a_taken_name_in_a_folder_that_differs_by_case_is_asked_about(jobs: Harness) -> None:
    # The plan says "Kaia", and the save reuses "kaia", so the question is about that file.
    existing = jobs.library / "kaia" / "Kaia, Lou V - Midnight Drive.mp3"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"older")
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    # Paths are compared as text, since Windows paths are equal ignoring case.
    assert jobs.job(job_id)["collision"] == {"path": str(existing)}
    assert jobs.manager.answer_collision(job_id, "replace") == {}
    jobs.pools.run_all()
    assert jobs.job(job_id)["output_path"] == str(existing)
    assert existing.read_bytes() == b"converted audio"
    assert [path.name for path in jobs.library.iterdir()] == ["kaia"]


def test_an_answer_without_a_question_does_nothing(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.manager.answer_collision(job_id, "replace") == {}
    assert jobs.job(job_id)["state"] == "needs_review"


# Skipping and removing


def test_skipping_deletes_the_staged_audio(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    assert staging(job_id).is_dir()
    jobs.manager.skip(job_id)
    job = jobs.job(job_id)
    assert job["state"] == "skipped"
    assert job["staged_audio"] is None
    jobs.pools.run_all()
    assert not staging(job_id).exists()


def test_skipping_during_a_download_stops_it_and_cleans_up(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.during_fetch = lambda: jobs.manager.skip(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "skipped"
    assert jobs.job(job_id)["staged_audio"] is None
    assert not staging(job_id).exists()
    assert jobs.events.job_states(job_id)[-1] == "skipped"


def test_a_skipped_job_ignores_a_late_resolve(jobs: Harness) -> None:
    job_id = jobs.add()
    jobs.manager.skip(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "skipped"
    assert jobs.steps.calls == []


def test_skipping_a_saved_job_does_nothing(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    jobs.manager.skip(job_id)
    assert jobs.job(job_id)["state"] == "saved"


def test_removing_a_job_says_so_and_deletes_its_staged_audio(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    assert jobs.manager.remove(job_id) == {}
    assert jobs.manager.views() == []
    assert jobs.events.events[-1] == {"type": "job_removed", "job_id": job_id}
    jobs.pools.run_all()
    assert not staging(job_id).exists()


def test_no_event_follows_job_removed(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.during_fetch = lambda: jobs.manager.remove(job_id)
    jobs.pools.run_all()
    events = jobs.events.for_job(job_id)
    assert events[-1] == {"type": "job_removed", "job_id": job_id}
    assert not staging(job_id).exists()


def test_removing_a_job_while_its_file_is_placed_lets_the_save_finish(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    answers: list[Any] = []

    def remove_now() -> None:
        answers.append(jobs.manager.remove(job_id))
        answers.append([job["id"] for job in jobs.manager.views()])

    jobs.steps.during_finalize = remove_now
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert answers == [{}, [job_id]]
    assert (jobs.library / "Kaia" / "Kaia, Lou V - Midnight Drive.mp3").is_file()
    assert jobs.manager.views() == []
    events = jobs.events.for_job(job_id)
    assert events[-2]["job"]["state"] == "saved"
    assert events[-1] == {"type": "job_removed", "job_id": job_id}


def test_removing_a_job_while_it_resolves_drops_the_result(jobs: Harness) -> None:
    job_id = jobs.add()
    pool = jobs.pools["resolve"]
    task = pool.tasks.popleft()
    jobs.manager.remove(job_id)
    task()
    assert jobs.events.for_job(job_id)[-1]["type"] == "job_removed"


def test_calls_for_a_job_not_in_the_queue_say_so(jobs: Harness) -> None:
    for answer in (
        jobs.manager.skip("gone"),
        jobs.manager.retry("gone"),
        jobs.manager.cancel("gone"),
        jobs.manager.remove("gone"),
        jobs.manager.answer_collision("gone", "replace"),
        jobs.manager.update_fields("gone", {"title": "x"}),
        jobs.manager.reset_field("gone", "title"),
        jobs.manager.toggle_change("gone", "c1", False),
        jobs.manager.set_style("gone", "artist_field"),
        jobs.manager.set_artwork("gone", "source", None, None),
    ):
        assert answer == {"error": "unknown_job"}


# Cancelling


def test_cancel_puts_a_save_waiting_for_its_audio_back_in_review(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.manager.cancel(job_id)
    assert jobs.job(job_id)["state"] == "needs_review"
    # The download carries on, so the audio is ready for the next save.
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"] is not None
    assert "finalize" not in jobs.steps.steps_for(job_id)


def test_cancel_takes_back_a_save_still_waiting_for_the_save_worker(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.manager.save([job_id])
    jobs.manager.cancel(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "needs_review"
    assert jobs.steps.finalized == []


def test_cancel_stops_a_background_download(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.during_fetch = lambda: jobs.manager.cancel(job_id)
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert job["progress"] is None
    assert job["staged_audio"] is None


def test_a_new_download_waits_for_a_cancelled_one_to_stop(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    downloads_queued: list[int] = []

    def cancel_then_save() -> None:
        jobs.steps.during_fetch = None
        jobs.manager.cancel(job_id)
        jobs.manager.save([job_id])
        downloads_queued.append(len(jobs.pools["download"].tasks))

    jobs.steps.during_fetch = cancel_then_save
    jobs.pools.run("download")
    # The second download starts only once the first has stopped.
    assert downloads_queued == [0]
    assert len(jobs.pools["download"].tasks) == 1
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert jobs.steps.steps_for(job_id).count("fetch") == 2


def test_saving_after_a_cancelled_download_downloads_again(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.during_fetch = lambda: jobs.manager.cancel(job_id)
    jobs.pools.run_all()
    jobs.steps.during_fetch = None
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert jobs.steps.steps_for(job_id).count("fetch") == 2


def test_cancel_while_the_file_is_being_placed_changes_nothing(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.manager.save([job_id])
    answers: list[Any] = []
    original = jobs.steps.finalize

    def finalize_while_cancelled(*args: Any, **kwargs: Any) -> Any:
        answers.append(jobs.manager.cancel(job_id))
        return original(*args, **kwargs)

    jobs.steps.finalize = finalize_while_cancelled
    jobs.pools.run_all()
    assert answers == [{}]
    assert jobs.job(job_id)["state"] == "saved"


def test_jobs_still_on_their_way_to_a_save_count_as_unsaved(jobs: Harness) -> None:
    # Closing loses them, since nothing brings the queue back yet (T-050).
    first = jobs.add_and_resolve()
    second = jobs.add_and_resolve(OTHER)
    jobs.add("https://soundcloud.com/kaia/still-resolving")
    assert jobs.manager.unsaved_count() == 3
    jobs.manager.skip(second)
    jobs.manager.save([first])
    jobs.pools.run_all()
    assert jobs.job(first)["state"] == "saved"
    assert jobs.manager.unsaved_count() == 1


@pytest.mark.parametrize(
    "kind", [ErrorKind.drm, ErrorKind.preview_only, ErrorKind.unavailable, ErrorKind.geo_blocked]
)
def test_a_job_no_retry_can_save_doesnt_count_as_unsaved(jobs: Harness, kind: ErrorKind) -> None:
    jobs.steps.prepare_errors.append(ScmpError(kind, "x"))
    job_id = jobs.add_and_resolve()
    assert jobs.job(job_id)["state"] == "error"
    assert jobs.manager.unsaved_count() == 0


@pytest.mark.parametrize(
    "kind",
    [ErrorKind.offline, ErrorKind.service_down, ErrorKind.engine_outdated, ErrorKind.internal],
)
def test_a_job_retry_could_still_save_counts_as_unsaved(jobs: Harness, kind: ErrorKind) -> None:
    jobs.steps.prepare_errors.append(ScmpError(kind, "x"))
    job_id = jobs.add_and_resolve()
    assert jobs.job(job_id)["state"] == "error"
    assert jobs.manager.unsaved_count() == 1


def test_a_save_that_failed_on_the_disk_counts_as_unsaved(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["error"]["kind"] == "disk"
    assert jobs.manager.unsaved_count() == 1


def test_many_jobs_leave_in_one_call_and_unknown_ids_are_left_out(jobs: Harness) -> None:
    first = jobs.add_and_resolve(TRACK)
    second = jobs.add_and_resolve(OTHER)
    third = jobs.add("https://soundcloud.com/kaia/lantern")
    assert jobs.manager.remove_jobs([first, "gone", second]) == {}
    assert [job["id"] for job in jobs.manager.views()] == [third]
    removed = [event["job_id"] for event in jobs.events.events if event["type"] == "job_removed"]
    assert removed == [first, second]


def test_removing_many_lets_a_save_placing_its_file_finish_first(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.pools.run("misc")
    jobs.manager.save([job_id])
    jobs.steps.during_finalize = lambda: jobs.manager.remove_jobs([job_id])
    jobs.pools.run("save")
    assert jobs.manager.views() == []
    assert jobs.steps.steps_for(job_id)[-1] == "finalize"
    assert list(jobs.library.rglob("*.mp3"))
