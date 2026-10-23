"""Tests for editing jobs in review: hand edits, cleanups, styles, covers and settings changes."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import job_cover_tasks
from scmp3.app.job_editing import rebuilt_suggestion
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import CropBox, SourceInfo, TagSuggestion


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
def no_background(tmp_path: Path) -> Harness:
    """A harness where nothing downloads until Save."""
    return fakes.make_harness(tmp_path / "library", download_while_reviewing=False)


def staging(job_id: str) -> Path:
    return paths.staging_dir() / job_id


# Editing


def test_hand_edits_are_kept_exactly_as_typed(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.manager.update_fields(job_id, {"title": " Midnight Drive ", "year": "2024"}) == {}
    job = jobs.job(job_id)
    assert job["overrides"] == {"title": " Midnight Drive ", "year": "2024"}
    assert job["tags"]["year"] == 2024


def test_an_edit_equal_to_the_suggestion_is_still_kept(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"title": "Midnight Drive"})
    assert jobs.job(job_id)["overrides"] == {"title": "Midnight Drive"}


@pytest.mark.parametrize(
    ("patch", "fields"),
    [
        ({"year": "20x4"}, {"year": {"reason": "NOT_A_YEAR"}}),
        ({"year": "0"}, {"year": {"reason": "NOT_A_YEAR"}}),
        ({"year": "10000"}, {"year": {"reason": "NOT_A_YEAR"}}),
        ({"track_no": "0"}, {"track_no": {"reason": "NOT_A_TRACK"}}),
        ({"track_no": "1000"}, {"track_no": {"reason": "NOT_A_TRACK"}}),
        ({"track_no": "three"}, {"track_no": {"reason": "NOT_A_TRACK"}}),
        ({"track_no": "5/3"}, {"track_no": {"reason": "TOTAL_BEFORE_TRACK"}}),
        (
            {"title": "Fine", "year": "x", "track_no": "y"},
            {"year": {"reason": "NOT_A_YEAR"}, "track_no": {"reason": "NOT_A_TRACK"}},
        ),
    ],
)
def test_a_patch_with_an_unusable_value_is_refused_whole(
    jobs: Harness, patch: dict[str, str], fields: dict[str, Any]
) -> None:
    job_id = jobs.add_and_resolve()
    before = len(jobs.events.events)
    assert jobs.manager.update_fields(job_id, patch) == {
        "error": "invalid_fields",
        "fields": fields,
    }
    assert jobs.job(job_id)["overrides"] == {}
    assert len(jobs.events.events) == before


def test_a_track_number_past_the_suggested_total_is_refused(jobs: Harness) -> None:
    jobs.steps.suggestion_extras = {"track_no": 2, "track_total": 5}
    job_id = jobs.add_and_resolve()
    answer = jobs.manager.update_fields(job_id, {"track_no": "7"})
    reason = {"reason": "TOTAL_BEFORE_TRACK"}
    assert answer == {"error": "invalid_fields", "fields": {"track_no": reason}}
    assert jobs.manager.update_fields(job_id, {"track_no": "7/9"}) == {}


def test_a_blank_number_clears_the_field(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.manager.update_fields(job_id, {"year": "  "}) == {}
    assert jobs.job(job_id)["tags"]["year"] is None


def test_resetting_a_field_follows_the_suggestion_again(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"title": "Other", "artist": "Mara"})
    jobs.manager.reset_field(job_id, "title")
    job = jobs.job(job_id)
    assert job["overrides"] == {"artist": "Mara"}
    assert job["tags"]["title"] == "Midnight Drive"


def test_toggling_a_cleanup_re_renders_the_fields_not_typed_by_hand(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"artist": "Kaia"})
    producer = next(
        change
        for change in jobs.job(job_id)["suggestion"]["changes"]
        if change["kind"] == "remove_producer"
    )
    assert jobs.manager.toggle_change(job_id, producer["id"], False) == {}
    job = jobs.job(job_id)
    assert job["tags"]["title"] == "Midnight Drive [prod. Hazy]"
    assert job["tags"]["artist"] == "Kaia"
    toggled = [change for change in job["suggestion"]["changes"] if change["id"] == producer["id"]]
    assert toggled[0]["enabled"] is False


def test_toggling_a_cleanup_the_job_doesnt_have_is_a_bug(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.manager.toggle_change(job_id, "nope", False) == {"error": "internal"}


def test_a_style_for_one_track_changes_its_tags(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_style(job_id, "feat_in_title")
    job = jobs.job(job_id)
    assert job["style"] == "feat_in_title"
    assert job["tags"]["title"] == "Midnight Drive (feat. Lou V)"
    assert job["tags"]["artist"] == "Kaia"


def queued_job(jobs: Harness) -> str:
    return jobs.add()


def saving_job(jobs: Harness) -> str:
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    return job_id


def saved_job(jobs: Harness) -> str:
    job_id = saving_job(jobs)
    jobs.pools.run_all()
    return job_id


def skipped_job(jobs: Harness) -> str:
    job_id = jobs.add_and_resolve()
    jobs.manager.skip(job_id)
    return job_id


@pytest.mark.parametrize("make_job", [queued_job, saving_job, saved_job, skipped_job])
def test_jobs_outside_review_cant_be_edited(jobs: Harness, make_job: Any) -> None:
    job_id = make_job(jobs)
    assert jobs.job(job_id)["state"] == make_job.__name__.removesuffix("_job")
    assert jobs.manager.update_fields(job_id, {"title": "x"}) == {"error": "not_editable"}
    assert jobs.manager.set_style(job_id, "custom") == {"error": "not_editable"}


def test_a_job_in_error_can_still_be_edited(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.manager.update_fields(job_id, {"title": "Fixed"}) == {}


# Covers


def test_a_cover_link_is_checked_before_the_job_switches_to_it(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    link = "https://example.com/cover.jpg"
    assert jobs.manager.set_artwork(job_id, "url", link, None) == {}
    assert jobs.job(job_id)["artwork"]["kind"] == "source"
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert job["artwork"] == {"kind": "url", "value": link, "crop": None}
    assert job["cover_url"] == link
    assert job["cover_size"] == {"width": 1200, "height": 800}


def test_a_cover_that_doesnt_work_leaves_the_cover_as_it_was(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("misc")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/broken.jpg", None)
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert job["artwork"]["kind"] == "source"
    assert job["cover_url"] == fakes.ARTWORK


def test_a_pasted_image_is_kept_as_a_file_and_sent_as_a_preview(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artwork(job_id, "data_url", "data:image/png;base64,iVBORw0KGgo=", None)
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert job["artwork"]["kind"] == "file"
    assert Path(job["artwork"]["value"]).parent == staging(job_id)
    assert job["cover_url"] == fakes.PREVIEW
    assert job["cover_size"] == {"width": 300, "height": 300}


def test_a_picked_image_file_is_sent_as_a_preview(jobs: Harness, tmp_path: Path) -> None:
    job_id = jobs.add_and_resolve()
    picked = str(tmp_path / "cover.png")
    jobs.manager.set_artwork(job_id, "file", picked, None)
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert job["artwork"]["value"] == picked
    assert job["cover_url"] == fakes.PREVIEW


def test_the_same_image_with_a_new_crop_only_changes_the_crop(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    link = "https://example.com/cover.jpg"
    jobs.manager.set_artwork(job_id, "url", link, None)
    jobs.pools.run("misc")
    crop = CropBox(left=0.1, top=0.0, width=0.6, height=0.9)
    jobs.manager.set_artwork(job_id, "url", link, crop)
    assert not jobs.pools["misc"].tasks
    job = jobs.job(job_id)
    assert job["artwork"]["crop"] == crop.to_dict()
    assert job["cover_size"] == {"width": 1200, "height": 800}


def test_source_goes_back_to_the_sources_own_cover(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("misc")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.pools.run("misc")
    jobs.manager.set_artwork(job_id, "source", None, None)
    job = jobs.job(job_id)
    assert job["artwork"] == {"kind": "source", "value": None, "crop": None}
    assert job["cover_url"] == fakes.ARTWORK
    assert job["cover_size"] == {"width": 500, "height": 500}


def test_going_back_to_the_source_calls_off_a_cover_being_checked(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    jobs.manager.set_artwork(job_id, "source", None, None)
    jobs.pools.run_all()
    assert jobs.job(job_id)["artwork"]["kind"] == "source"


@pytest.mark.parametrize(
    ("kind", "value", "problem"),
    [
        ("url", "https://example.com/broken.jpg", "unavailable"),
        ("url", "https://example.com/offline.jpg", "offline"),
        ("file", "C:\\Pictures\\damaged.png", "unreadable"),
    ],
)
def test_a_cover_that_cant_be_used_says_why_on_the_job(
    jobs: Harness, kind: str, value: str, problem: str
) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artwork(job_id, kind, value, None)
    jobs.pools.run("misc")
    assert jobs.job(job_id)["cover_problem"] == problem
    assert jobs.job(job_id)["artwork"]["kind"] == "source"


def test_a_cover_problem_goes_once_a_new_cover_is_chosen(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    assert jobs.job(job_id)["cover_problem"] is None
    jobs.manager.set_artwork(job_id, "url", "https://example.com/broken.jpg", None)
    jobs.pools.run("misc")
    assert jobs.job(job_id)["cover_problem"] == "unavailable"
    jobs.manager.set_artwork(job_id, "url", "https://example.com/cover.jpg", None)
    # The next cover clears the problem at once, and works.
    assert jobs.job(job_id)["cover_problem"] is None
    jobs.pools.run("misc")
    assert jobs.job(job_id)["cover_problem"] is None
    assert jobs.job(job_id)["artwork"]["kind"] == "url"


def test_a_cover_check_that_fails_unexpectedly_never_leaves_the_save_waiting(
    jobs: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.manager.set_artwork(job_id, "url", "https://example.com/bug.jpg", None)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert job["state"] == "saved"
    assert job["cover_problem"] == "internal"
    assert job["artwork"]["kind"] == "source"
    assert "Checking a new cover failed unexpectedly" in caplog.text


def test_the_sources_cover_is_read_once_and_the_save_reuses_it(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("misc")
    crop = CropBox(left=0.1, top=0.1, width=0.8, height=0.8)
    jobs.manager.set_artwork(job_id, "source", None, crop)
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.covers.source_reads == 1
    (call,) = jobs.steps.finalized
    assert call.cover.kind == "file"
    kept = Path(call.cover.value or "")
    assert kept.name.startswith("cover-source-")
    assert kept.suffix == ".jpg"
    assert call.cover.crop == crop
    # The job still says its cover is the source's own.
    assert jobs.job(job_id)["artwork"]["kind"] == "source"


def test_a_save_waits_for_the_sources_cover_and_leaves_no_staging_folder(
    jobs: Harness,
) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    jobs.manager.save([job_id])
    # The cover is still being read, so the save waits for it.
    jobs.pools.run("save")
    assert jobs.steps.finalized == []
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    (call,) = jobs.steps.finalized
    assert call.cover.kind == "file"
    assert jobs.covers.source_reads == 1
    assert not (paths.staging_dir() / job_id).exists()


def test_a_sources_cover_that_didnt_load_lets_finalize_try_again(jobs: Harness) -> None:
    jobs.covers.source_works = False
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    (call,) = jobs.steps.finalized
    assert call.cover.kind == "source"


def test_a_sources_cover_read_for_a_job_removed_meanwhile_is_never_written(
    jobs: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept: list[Any] = []
    monkeypatch.setattr(job_cover_tasks, "keep_source_cover", lambda *args: kept.append(args))
    job_id = jobs.add_and_resolve()
    jobs.covers.during_source = lambda: jobs.manager.remove(job_id)
    jobs.pools.run_all()
    assert kept == []
    assert not (paths.staging_dir() / job_id).exists()


# Settings


def test_a_new_title_style_re_renders_jobs_in_review(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.update_fields(job_id, {"album": "Night Drives"})
    jobs.change_settings(title_style="feat_in_title")
    jobs.pools.run("misc")
    job = jobs.events.last_job(job_id)
    assert job["tags"]["title"] == "Midnight Drive (feat. Lou V)"
    assert job["tags"]["album"] == "Night Drives"


def test_a_cleanup_setting_changes_only_the_cleanups_not_toggled_by_hand(jobs: Harness) -> None:
    title = "Night Bus (prod. Hazy) [prod. Mara]"
    jobs.steps.sources[TRACK] = fakes.source_for(TRACK, title)
    job_id = jobs.add_and_resolve()
    first, second = jobs.job(job_id)["suggestion"]["changes"]
    jobs.manager.toggle_change(job_id, first["id"], False)
    jobs.change_settings(remove_producer_credits=False)
    jobs.pools.run("misc")
    changes = {
        change["id"]: change["enabled"] for change in jobs.job(job_id)["suggestion"]["changes"]
    }
    assert changes == {first["id"]: False, second["id"]: False}
    jobs.manager.toggle_change(job_id, first["id"], True)
    changes = {
        change["id"]: change["enabled"] for change in jobs.job(job_id)["suggestion"]["changes"]
    }
    assert changes == {first["id"]: True, second["id"]: False}


def test_the_library_folder_setting_applies_to_the_next_save_at_once(jobs: Harness) -> None:
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk))
    job_id = jobs.add_and_resolve()
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    elsewhere = jobs.library.parent / "elsewhere"
    jobs.change_settings(library_dir=str(elsewhere))
    jobs.manager.retry(job_id)
    jobs.pools.run_all()
    assert Path(jobs.job(job_id)["output_path"]).parent == elsewhere / "Kaia"


def test_a_cleanup_setting_changed_while_resolving_reaches_the_job(jobs: Harness) -> None:
    def change_while_resolving() -> None:
        jobs.change_settings(remove_producer_credits=False)
        # The refresh after the change runs now, while the job isn't in review yet.
        jobs.pools.run("misc")

    jobs.steps.during_prepare = change_while_resolving
    job_id = jobs.add()
    jobs.pools.run("resolve")
    producer = [
        change
        for change in jobs.job(job_id)["suggestion"]["changes"]
        if change["kind"] == "remove_producer"
    ]
    assert [change["enabled"] for change in producer] == [False]
    assert jobs.job(job_id)["tags"]["title"] == "Midnight Drive [prod. Hazy]"


def test_a_settings_save_that_arrives_after_a_newer_one_is_ignored(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    older = jobs.store.update({"title_style": "feat_in_title"})
    newer = jobs.store.update({"title_style": "all_in_title"})
    jobs.manager.settings_changed(newer, 2)
    jobs.manager.settings_changed(older, 1)
    jobs.pools.run("misc")
    assert jobs.job(job_id)["tags"]["title"] == "Midnight Drive (Kaia, Lou V)"


def test_turning_on_downloads_while_reviewing_leaves_a_stopped_download_stopped(
    jobs: Harness,
) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.cancel(job_id)
    jobs.change_settings(download_while_reviewing=False)
    jobs.change_settings(download_while_reviewing=True)
    jobs.pools.run_all()
    assert jobs.job(job_id)["staged_audio"] is None
    assert "fetch" not in jobs.steps.steps_for(job_id)


def test_turning_on_downloads_while_reviewing_starts_them_for_jobs_in_review(
    no_background: Harness,
) -> None:
    job_id = no_background.add_and_resolve()
    assert not no_background.pools["download"].tasks
    no_background.change_settings(download_while_reviewing=True)
    no_background.pools.run("download")
    job = no_background.job(job_id)
    assert job["state"] == "needs_review"
    assert job["staged_audio"] is not None


def test_downloads_at_once_resizes_the_download_pool(jobs: Harness) -> None:
    assert jobs.pools["download"].size == 2
    jobs.change_settings(concurrent_downloads=4)
    assert jobs.pools["download"].size == 4


def test_a_new_audio_format_reaches_downloads_not_started_yet(no_background: Harness) -> None:
    job_id = no_background.add_and_resolve()
    no_background.change_settings(audio_format="m4a")
    assert no_background.job(job_id)["filename"].endswith(".m4a")
    no_background.manager.save([job_id])
    no_background.pools.run("download")
    (settings,) = no_background.steps.fetched_settings
    assert settings.audio_format == "m4a"


def test_a_download_keeps_the_format_it_started_with(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.steps.during_fetch = lambda: jobs.change_settings(audio_format="m4a")
    jobs.pools.run("download")
    (settings,) = jobs.steps.fetched_settings
    assert settings.audio_format == "mp3_320"
    jobs.pools.run_all()
    assert jobs.steps.steps_for(job_id).count("fetch") == 1


def test_a_progress_report_that_fails_never_reaches_the_download(
    jobs: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = jobs.add_and_resolve()
    record_event = jobs.events.emit

    def emit_all_but_progress(event: Any) -> None:
        if isinstance(event, dict) and event["type"] == "progress":
            raise RuntimeError("the bus broke")
        record_event(event)

    jobs.events.emit = emit_all_but_progress
    jobs.pools.run("download")
    assert jobs.job(job_id)["staged_audio"] is not None
    assert "Showing a download's progress failed" in caplog.text


def test_rebuilding_without_hand_toggles_gives_the_suggestion_back(jobs: Harness) -> None:
    job = jobs.job(jobs.add_and_resolve())
    suggestion = TagSuggestion.from_dict(job["suggestion"])
    source = SourceInfo.from_dict(job["source"])
    rebuilt = rebuilt_suggestion(suggestion, source, {}, jobs.store.load())
    assert rebuilt == suggestion
