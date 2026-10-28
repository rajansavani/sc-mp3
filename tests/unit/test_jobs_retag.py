"""Tests for re-tag jobs in the job manager: files you have, reviewed and written in place."""

from pathlib import Path

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import JobState
from tests.fixtures import job_fakes as fakes
from tests.fixtures.job_fakes import FILE_ALBUM, FILE_ARTIST, FILE_TITLE, Harness


@pytest.fixture
def jobs(tmp_path: Path) -> Harness:
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
    jobs = fakes.make_harness(tmp_path / "library", queue=queue)
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
