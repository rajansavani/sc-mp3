"""Tests for the job manager's failures, retries by hand and by themselves, and duplicates."""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import HistoryEntry


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
TRACK = fakes.TRACK
Harness = Any


@pytest.fixture
def jobs(tmp_path: Path) -> Harness:
    return fakes.make_harness(tmp_path / "library")


@pytest.fixture
def no_review(tmp_path: Path) -> Harness:
    """A harness that saves every track without review."""
    return fakes.make_harness(tmp_path / "library", review_before_saving=False)


def saved_entry(url: str = TRACK) -> HistoryEntry:
    return HistoryEntry(
        source_id=fakes.source_for(url).id,
        url_norm=url,
        saved_path=Path("Kaia - Midnight Drive.mp3"),
        title="Midnight Drive",
        artist="Kaia, Lou V",
        album="Midnight Drive",
        format="mp3",
        saved_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


# Failing and retrying


def test_a_resolve_failure_puts_the_job_in_error(jobs: Harness) -> None:
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.unavailable, "404"))
    job_id = jobs.add_and_resolve()
    job = jobs.job(job_id)
    assert job["state"] == "error"
    assert job["error"] == {"kind": "unavailable", "detail": "404", "where": None, "reason": None}
    assert job["tags"] is None


@pytest.mark.parametrize("kind", [ErrorKind.drm, ErrorKind.unavailable, ErrorKind.offline])
def test_the_ui_hears_when_resolving_fails(jobs: Harness, kind: ErrorKind) -> None:
    # Without this event the queue kept saying "Fetching info…" (found in T-025's live run).
    jobs.steps.prepare_errors.append(ScmpError(kind, "x"))
    job_id = jobs.add_and_resolve()
    assert jobs.events.job_states(job_id) == ["queued", "resolving", "error"]
    assert jobs.events.last_job(job_id) == jobs.job(job_id)


def test_the_ui_hears_when_an_automatic_retry_fails_again(jobs: Harness) -> None:
    jobs.steps.prepare_errors.extend([ScmpError(ErrorKind.offline), ScmpError(ErrorKind.offline)])
    job_id = jobs.add_and_resolve()
    jobs.timers.fire_all()
    jobs.pools.run("resolve")
    assert jobs.events.job_states(job_id)[-3:] == ["queued", "resolving", "error"]
    assert jobs.events.last_job(job_id) == jobs.job(job_id)


@pytest.mark.parametrize("where", ["library", "data"])
def test_a_disk_error_says_which_folder_it_was_about(jobs: Harness, where: str) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk, "can't write", where=where))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    error = jobs.job(job_id)["error"]
    assert error == {"kind": "disk", "detail": "can't write", "where": where, "reason": None}


@pytest.mark.parametrize("reason", ["file_in_use", "path_too_long"])
def test_a_disk_error_says_what_went_wrong_when_it_knows(jobs: Harness, reason: str) -> None:
    jobs.steps.finalize_errors.append(
        ScmpError(ErrorKind.disk, "can't write", where="library", reason=reason)
    )
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["error"]["reason"] == reason


def test_retry_after_a_resolve_failure_resolves_again(jobs: Harness) -> None:
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.engine_outdated))
    job_id = jobs.add_and_resolve()
    jobs.manager.retry(job_id)
    assert jobs.job(job_id)["state"] == "queued"
    jobs.pools.run("resolve")
    assert jobs.job(job_id)["state"] == "needs_review"
    assert jobs.job(job_id)["error"] is None


def test_a_background_download_failure_shows_on_the_job_in_review(jobs: Harness) -> None:
    jobs.steps.fetch_errors.append(ScmpError(ErrorKind.service_down))
    job_id = jobs.add_and_resolve()
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "error"
    assert job["tags"] is not None


def test_retry_after_a_background_download_failure_goes_back_to_review(jobs: Harness) -> None:
    jobs.steps.fetch_errors.append(ScmpError(ErrorKind.service_down))
    job_id = jobs.add_and_resolve()
    jobs.pools.run_all()
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"] is not None


def test_retry_after_a_download_failure_while_saving_saves(jobs: Harness) -> None:
    jobs.steps.fetch_errors.append(ScmpError(ErrorKind.service_down))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "error"
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"


def test_retry_after_a_save_error_saves_again_with_the_same_choices(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk, "can't write"))
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"album": "Night Drives"})
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "error"
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert [call.overrides for call in jobs.steps.finalized] == [{"album": "Night Drives"}] * 2
    assert jobs.steps.steps_for(job_id).count("fetch") == 1


def test_retry_after_a_failed_conversion_downloads_again_then_saves(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.convert_failed))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    staged = Path(jobs.job(job_id)["staged_audio"])
    jobs.manager.retry(job_id)
    assert jobs.job(job_id)["staged_audio"] is None
    # The failed audio is gone before the new download writes its own.
    gone: list[bool] = []
    jobs.steps.during_fetch = lambda: gone.append(not staged.exists())
    jobs.pools.run_all()
    assert gone == [True]
    assert jobs.job(job_id)["state"] == "saved"
    assert jobs.steps.steps_for(job_id).count("fetch") == 2


def test_a_new_download_after_a_failed_conversion_keeps_its_own_file(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.convert_failed))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    jobs.manager.retry(job_id)
    # The download runs while the misc pool is still busy, as after a paste of many links.
    jobs.pools.run("download")
    staged = Path(jobs.job(job_id)["staged_audio"])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    assert Path(jobs.job(job_id)["output_path"]).read_bytes() == b"converted audio"
    assert not staged.exists()


def test_retry_on_a_job_without_an_error_does_nothing(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.manager.retry(job_id) == {}
    assert jobs.steps.steps_for(job_id) == ["prepare"]


@pytest.mark.parametrize(("kind", "delay"), [("offline", 30.0), ("rate_limited", 60.0)])
def test_offline_and_rate_limited_jobs_retry_by_themselves(
    jobs: Harness, kind: str, delay: float
) -> None:
    jobs.steps.prepare_errors.append(ScmpError(kind))
    job_id = jobs.add_and_resolve()
    (timer,) = jobs.timers.waiting()
    assert timer.delay == delay
    timer.callback()
    jobs.pools.run("resolve")
    assert jobs.job(job_id)["state"] == "needs_review"


def test_other_errors_wait_for_the_user(jobs: Harness) -> None:
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.drm))
    jobs.add_and_resolve()
    assert jobs.timers.started == []


def test_removing_a_job_stops_its_automatic_retry(jobs: Harness) -> None:
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.offline))
    job_id = jobs.add_and_resolve()
    jobs.manager.remove(job_id)
    assert jobs.timers.waiting() == []
    jobs.timers.fire_all()
    assert not jobs.pools["resolve"].tasks


def test_a_retry_by_hand_stops_the_automatic_one(jobs: Harness) -> None:
    jobs.steps.prepare_errors.extend([ScmpError(ErrorKind.offline), ScmpError(ErrorKind.offline)])
    job_id = jobs.add_and_resolve()
    jobs.manager.retry(job_id)
    jobs.pools.run("resolve")
    # The first timer fires late, after its failure was replaced by a new one.
    jobs.timers.started[0].callback()
    assert not jobs.pools["resolve"].tasks
    assert jobs.steps.steps_for(job_id) == ["prepare", "prepare"]


# Duplicates


def test_with_review_a_duplicate_is_a_warning_and_save_just_saves(jobs: Harness) -> None:
    jobs.steps.duplicates[TRACK] = saved_entry()
    job_id = jobs.add_and_resolve()
    job = jobs.job(job_id)
    assert job["state"] == "needs_review"
    assert job["duplicate"]["saved_at"].startswith("2026-10-03")
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"


def test_without_review_a_track_saves_by_itself(no_review: Harness) -> None:
    job_id = no_review.add_and_resolve()
    assert no_review.job(job_id)["state"] == "saving"
    no_review.pools.run_all()
    assert no_review.job(job_id)["state"] == "saved"


def test_without_review_a_duplicate_stops_with_an_error(no_review: Harness) -> None:
    no_review.steps.duplicates[TRACK] = saved_entry()
    job_id = no_review.add_and_resolve()
    no_review.pools.run_all()
    job = no_review.job(job_id)
    assert job["state"] == "error"
    assert job["error"]["kind"] == "duplicate"
    assert no_review.steps.finalized == []


@pytest.mark.parametrize("call", ["save", "retry"])
def test_save_or_retry_on_a_duplicate_error_saves_anyway(no_review: Harness, call: str) -> None:
    no_review.steps.duplicates[TRACK] = saved_entry()
    job_id = no_review.add_and_resolve()
    no_review.pools.run_all()
    if call == "save":
        no_review.manager.save([job_id])
    else:
        no_review.manager.retry(job_id)
    no_review.pools.run_all()
    assert no_review.job(job_id)["state"] == "saved"
