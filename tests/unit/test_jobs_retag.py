"""Tests for re-tag jobs in the job manager: files you have, reviewed and written in place."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from scmp3.app.job_covers import CoverView
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import JobState
from tests.fixtures import job_fakes as fakes
from tests.fixtures.job_fakes import FILE_ALBUM, FILE_ARTIST, FILE_TITLE, Harness


@pytest.fixture
def jobs(tmp_path: Path) -> Harness:
    # The sample's suggestion renames the song, so the playlist question is off unless a test
    # is about it.
    return fakes.make_harness(tmp_path / "library", retag_playlist_warning=False)


@pytest.fixture
def asking(tmp_path: Path) -> Harness:
    return fakes.make_harness(tmp_path / "library")


@pytest.fixture
def song(tmp_path: Path) -> Path:
    path = tmp_path / "old songs" / "old favorite.mp3"
    path.parent.mkdir()
    path.write_bytes(b"audio")
    return path


def test_a_dropped_file_becomes_a_re_tag_job_named_by_its_path(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    job = jobs.job(job_id)
    assert (job["kind"], job["input"], job["state"]) == ("retag", str(song), "queued")


def test_a_file_is_read_then_waits_for_review_without_downloading(
    jobs: Harness, song: Path
) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "needs_review"
    assert jobs.steps.steps_for(job_id) == ["prepare"]
    assert not jobs.pools["download"].tasks


def test_a_file_waits_for_review_even_when_reviews_are_turned_off(
    tmp_path: Path, song: Path
) -> None:
    jobs = fakes.make_harness(tmp_path / "library", review_before_saving=False)
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "needs_review"
    assert "retag" not in jobs.steps.steps_for(job_id)


def test_the_same_file_twice_is_already_in_the_queue(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    answer = jobs.manager.add_inputs(f'"{song}"')
    assert answer["rejected"] == [
        {"input": f'"{song}"', "reason": "ALREADY_IN_QUEUE", "job_id": job_id}
    ]


def test_a_re_tag_job_shows_its_file_name_and_the_tags_in_the_file_now(
    jobs: Harness, song: Path
) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["filename"] == "old favorite.mp3"
    assert job["format"] is None
    assert job["file_tags"] == {"title": FILE_TITLE, "artist": FILE_ARTIST, "album": FILE_ALBUM}
    assert job["tags"]["title"] == "Sunday Mornings"


def test_saving_a_re_tag_writes_the_reviewed_tags_into_the_file(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.manager.update_fields(job_id, {"year": "2020"})
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert (job["state"], job["output_path"]) == ("saved", str(song))
    (call,) = jobs.steps.retagged
    assert (call.tags.title, call.tags.year) == ("Sunday Mornings", 2020)
    assert "finalize" not in jobs.steps.steps_for(job_id)
    assert "fetch" not in jobs.steps.steps_for(job_id)


def test_a_re_tag_keeps_the_files_own_cover_unless_another_is_chosen(
    jobs: Harness, song: Path
) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    (call,) = jobs.steps.retagged
    # The source's own, so the file's cover stays byte for byte, not the copy kept in staging.
    assert (call.cover.kind, call.cover.crop) == ("source", None)


def test_a_re_tag_that_fails_says_why_and_retries(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    missing = ScmpError(ErrorKind.disk, "gone", where="file", reason="file_missing")
    jobs.steps.retag_errors.append(missing)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    error = jobs.job(job_id)["error"]
    assert (error["kind"], error["reason"], error["where"]) == ("disk", "file_missing", "file")
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert jobs.steps.steps_for(job_id).count("retag") == 2


def test_a_file_that_cant_be_read_fails_before_review(jobs: Harness, song: Path) -> None:
    unreadable = ScmpError(ErrorKind.convert_failed, "not audio", reason="unreadable_file")
    jobs.steps.prepare_errors.append(unreadable)
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert (job["state"], job["error"]["reason"]) == ("error", "unreadable_file")
    assert job["tags"] is None


def test_turning_on_downloads_while_reviewing_leaves_re_tags_alone(
    tmp_path: Path, song: Path
) -> None:
    jobs = fakes.make_harness(tmp_path / "library", download_while_reviewing=False)
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.change_settings(download_while_reviewing=True)
    jobs.pools.run_all()
    assert "fetch" not in jobs.steps.steps_for(job_id)


def test_a_re_tag_in_review_comes_back_after_a_restart_without_downloading(
    tmp_path: Path, song: Path
) -> None:
    queue = fakes.QueueFile(tmp_path / "data" / "queue.json")
    jobs = fakes.make_harness(tmp_path / "library", queue=queue, retag_playlist_warning=False)
    jobs.manager.start_up()
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    again = fakes.restarted(jobs)
    again.pools.run_all()
    job = again.job(job_id)
    assert (job["kind"], job["state"], job["input"]) == ("retag", "needs_review", str(song))
    assert "fetch" not in again.steps.steps_for(job_id)
    again.manager.save([job_id])
    again.pools.run_all()
    assert again.job(job_id)["state"] == JobState.saved.value


def test_a_new_cover_for_a_re_tag_is_passed_as_chosen(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.pools.run_all()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    (call,) = jobs.steps.retagged
    assert (call.cover.kind, call.cover.value) == ("url", "https://example.com/cover.jpg")


def test_a_failed_re_tag_keeps_the_tags_it_was_asked_about(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.steps.retag_errors.append(ScmpError(ErrorKind.disk, "held", reason="file_in_use"))
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.manager.update_fields(job_id, {"title": "Another Title"}) == {
        "error": "not_editable"
    }
    assert jobs.manager.toggle_change(job_id, "remove_promo:(Official Audio)", False) == {
        "error": "not_editable"
    }
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    titles = [call.tags.title for call in jobs.steps.retagged]
    assert titles == ["Sunday Mornings", "Sunday Mornings"]


def test_retry_reads_a_file_changed_elsewhere_again_and_drops_the_edits(
    jobs: Harness, song: Path
) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.manager.update_fields(job_id, {"year": "2020"})
    changed = ScmpError(ErrorKind.disk, "changed", where="file", reason="file_changed")
    jobs.steps.retag_errors.append(changed)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["error"]["reason"] == "file_changed"
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert (job["state"], job["overrides"]) == ("needs_review", {})
    assert jobs.steps.steps_for(job_id).count("prepare") == 2


def test_a_re_tag_that_renames_the_song_waits_for_the_playlist_question(
    asking: Harness, song: Path
) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    assert asking.manager.save([job_id]) == {"error": "renames_song"}
    asking.pools.run_all()
    assert asking.job(job_id)["state"] == "needs_review"
    assert asking.steps.retagged == []


def test_a_re_tag_saves_once_the_playlist_question_is_answered(asking: Harness, song: Path) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    assert asking.manager.save([job_id], rename_confirmed=True) == {}
    asking.pools.run_all()
    assert asking.job(job_id)["state"] == "saved"


def test_a_re_tag_that_keeps_the_title_artist_and_album_saves_without_asking(
    asking: Harness, song: Path
) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    asking.manager.update_fields(job_id, {"title": FILE_TITLE, "year": "2020"})
    assert asking.manager.save([job_id]) == {}
    asking.pools.run_all()
    assert asking.job(job_id)["state"] == "saved"


def test_the_playlist_question_reads_the_artist_python_would_write(
    asking: Harness, song: Path
) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    assert asking.manager.update_fields(job_id, {"title": FILE_TITLE, "artist": "Mara Lune"}) == {}
    assert asking.manager.save([job_id]) == {"error": "renames_song"}


def test_a_saved_re_tag_lets_its_file_in_again_for_another_edit(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    answer = jobs.manager.add_inputs(f'"{song}"')
    assert answer["rejected"] == []
    (again,) = answer["added"]
    jobs.pools.run_all()
    assert jobs.job(again)["state"] == "needs_review"
    assert [job["id"] for job in jobs.manager.views()] == [again]


def test_a_re_tag_still_in_review_is_already_in_the_queue(jobs: Harness, song: Path) -> None:
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    answer = jobs.manager.add_inputs(f'"{song}"')
    assert answer["rejected"][0]["reason"] == "ALREADY_IN_QUEUE"
    assert jobs.job(job_id)["state"] == "needs_review"


@pytest.fixture
def featured_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture's file, with a featured artist in its title tag."""
    plain = fakes.file_source

    def featured(path: Path) -> Any:
        return dataclasses.replace(plain(path), title="Sunday Mornings (feat. Lou V)")

    monkeypatch.setattr(fakes, "file_source", featured)


def failed_save_in_feat_in_title(tmp_path: Path, song: Path) -> tuple[Harness, str]:
    """A re-tag whose tags match the file in "feat. in title", so Save didn't ask, and whose
    write then failed."""
    jobs = fakes.make_harness(tmp_path / "library", title_style="feat_in_title")
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    jobs.steps.retag_errors.append(ScmpError(ErrorKind.disk, "the file is in use"))
    assert jobs.manager.save([job_id]) == {}
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "error"
    return jobs, job_id


@pytest.mark.usefixtures("featured_file")
def test_retry_after_a_title_style_change_asks_about_playlists(tmp_path: Path, song: Path) -> None:
    jobs, job_id = failed_save_in_feat_in_title(tmp_path, song)
    jobs.change_settings(title_style="artist_field")
    assert jobs.manager.retry(job_id) == {"error": "renames_song"}
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "error"
    assert len(jobs.steps.retagged) == 1


@pytest.mark.usefixtures("featured_file")
def test_retry_saves_the_new_tags_once_the_question_is_answered(tmp_path: Path, song: Path) -> None:
    jobs, job_id = failed_save_in_feat_in_title(tmp_path, song)
    jobs.change_settings(title_style="artist_field")
    assert jobs.manager.retry(job_id, rename_confirmed=True) == {}
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    written = jobs.steps.retagged[-1].tags
    assert (written.title, written.artist) == ("Sunday Mornings", "Teo Marr, Lou V")


@pytest.mark.usefixtures("featured_file")
def test_retry_with_no_settings_change_saves_without_asking(tmp_path: Path, song: Path) -> None:
    jobs, job_id = failed_save_in_feat_in_title(tmp_path, song)
    assert jobs.manager.retry(job_id) == {}
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"


def test_retry_of_a_rename_already_confirmed_doesnt_ask_again(asking: Harness, song: Path) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    asking.steps.retag_errors.append(ScmpError(ErrorKind.disk, "the file is in use"))
    assert asking.manager.save([job_id], rename_confirmed=True) == {}
    asking.pools.run_all()
    assert asking.manager.retry(job_id) == {}
    asking.pools.run_all()
    assert asking.job(job_id)["state"] == "saved"


def test_retry_that_reads_a_changed_file_again_doesnt_ask(asking: Harness, song: Path) -> None:
    job_id = asking.add(str(song))
    asking.pools.run_all()
    changed = ScmpError(ErrorKind.disk, "changed", where="file", reason="file_changed")
    asking.steps.retag_errors.append(changed)
    asking.manager.save([job_id], rename_confirmed=True)
    asking.pools.run_all()
    assert asking.manager.retry(job_id) == {}
    asking.pools.run_all()
    assert asking.job(job_id)["state"] == "needs_review"


def test_a_files_own_cover_that_cant_be_previewed_is_marked_unseen_in_review(
    jobs: Harness, song: Path
) -> None:
    jobs.covers.source = lambda source: (CoverView(unseen=True), None)
    job_id = jobs.add(str(song))
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert (job["cover_unseen"], job["cover_missing"]) == (True, False)
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.pools.run_all()
    assert jobs.job(job_id)["cover_unseen"] is False
