"""Tests for sets in the job manager: listing, numbering, the album's fields and cover, and saving
each track one at a time with its number."""

import importlib.util
import itertools
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core.errors import ErrorKind, ScmpError


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
ALBUM, PLAYLIST = sets.ALBUM, sets.PLAYLIST
Harness = Any


@pytest.fixture
def jobs(tmp_path: Path) -> Iterator[Harness]:
    harness = sets.make_set_harness(tmp_path / "library")
    yield harness
    harness.manager.shut_down()


def listed(jobs: Harness, url: str = ALBUM) -> str:
    """A set whose tracks are listed, none of them resolved yet."""
    set_id = jobs.add(url)
    jobs.pools.run("resolve")
    return set_id


def resolved(jobs: Harness, url: str = ALBUM) -> str:
    """A set whose tracks are all resolved, with the album's cover read."""
    set_id = listed(jobs, url)
    jobs.pools.run("resolve")
    jobs.pools.run("misc")
    return set_id


def tracks(jobs: Harness, set_id: str) -> list[dict[str, Any]]:
    return jobs.job(set_id)["children"]


def track_id(jobs: Harness, set_id: str, number: int) -> str:
    return tracks(jobs, set_id)[number - 1]["id"]


def numbers(jobs: Harness, set_id: str) -> list[tuple[int | None, int | None]]:
    """Each track's number and the album's total, as its tags have them."""
    return [
        (track["tags"]["track_no"], track["tags"]["track_total"]) if track["tags"] else (None, None)
        for track in tracks(jobs, set_id)
    ]


def saved_numbers(jobs: Harness) -> list[tuple[int, int]]:
    return [(call.tags.track_no, call.tags.track_total) for call in jobs.steps.set_saves]


# Listing


def test_a_set_link_is_one_queue_item_with_a_track_for_each_listed_one(jobs: Harness) -> None:
    set_id = listed(jobs)
    (job,) = jobs.manager.views()
    assert job["id"] == set_id
    assert job["kind"] == "set"
    assert job["state"] == "needs_review"
    assert job["source"]["playlist"]["title"] == "Night Drives"
    assert [track["input"] for track in job["children"]] == [
        sets.track_link(ALBUM, n) for n in range(1, 6)
    ]
    assert {track["state"] for track in job["children"]} == {"queued"}
    assert jobs.steps.steps_for(ALBUM) == ["prepare_set"]


def test_each_track_resolves_on_its_own_and_reaches_review(jobs: Harness) -> None:
    set_id = resolved(jobs)
    assert {track["state"] for track in tracks(jobs, set_id)} == {"needs_review"}
    assert [track["tags"]["title"] for track in tracks(jobs, set_id)] == [
        f"Track {n}" for n in range(1, 6)
    ]


def test_the_set_link_is_already_queued_but_its_tracks_links_are_not(jobs: Harness) -> None:
    resolved(jobs)
    again = jobs.manager.add_inputs(ALBUM)
    assert again["rejected"][0]["reason"] == "ALREADY_IN_QUEUE"
    assert jobs.manager.add_inputs(sets.track_link(ALBUM, 1))["rejected"] == []


def test_a_set_that_cant_be_listed_fails_and_retry_lists_it_again(jobs: Harness) -> None:
    jobs.steps.list_errors.append(ScmpError(ErrorKind.offline, "no answer"))
    set_id = listed(jobs)
    assert jobs.job(set_id)["state"] == "error"
    assert jobs.job(set_id)["error"]["kind"] == "offline"
    jobs.manager.retry(set_id)
    jobs.pools.run("resolve")
    assert jobs.job(set_id)["state"] == "needs_review"
    assert len(tracks(jobs, set_id)) == 5


# Numbers and the album's fields


def test_an_album_numbers_its_tracks_and_shares_the_sets_fields(jobs: Harness) -> None:
    set_id = resolved(jobs)
    view = jobs.job(set_id)
    assert view["default_mode"] == "album"
    assert view["tags"] == {
        "album": "Night Drives",
        "album_artist": "Kaia",
        "year": None,
        "genre": None,
    }
    assert numbers(jobs, set_id) == [(n, 5) for n in range(1, 6)]
    assert {track["tags"]["album"] for track in view["children"]} == {"Night Drives"}
    assert view["children"][0]["filename"] == "Kaia/Kaia - Track 1.mp3"


def test_leaving_a_track_out_renumbers_the_rest(jobs: Harness) -> None:
    set_id = resolved(jobs)
    answer = jobs.manager.set_included(set_id, track_id(jobs, set_id, 2), False)
    assert answer == {}
    assert numbers(jobs, set_id) == [(1, 4), (None, None), (2, 4), (3, 4), (4, 4)]
    jobs.manager.set_included(set_id, track_id(jobs, set_id, 2), True)
    assert numbers(jobs, set_id) == [(n, 5) for n in range(1, 6)]


def test_a_track_that_cant_be_saved_gets_no_number(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.drm, "DRM protected"))
    jobs.pools.run("resolve")
    assert tracks(jobs, set_id)[0]["error"]["kind"] == "drm"
    assert numbers(jobs, set_id) == [(None, None), (1, 4), (2, 4), (3, 4), (4, 4)]


def test_a_playlist_saves_as_singles_unless_it_is_made_an_album(jobs: Harness) -> None:
    set_id = resolved(jobs, PLAYLIST)
    assert jobs.job(set_id)["default_mode"] == "singles"
    assert numbers(jobs, set_id) == [(None, None)] * 3
    assert jobs.manager.set_album_mode(set_id, "album") == {}
    assert jobs.job(set_id)["album_mode"] == "album"
    assert numbers(jobs, set_id) == [(1, 3), (2, 3), (3, 3)]
    assert {track["tags"]["album"] for track in tracks(jobs, set_id)} == {"Late Mix"}


def test_the_sets_own_edits_change_every_tracks_album(jobs: Harness) -> None:
    set_id = resolved(jobs)
    answer = jobs.manager.update_fields(set_id, {"album": "Night Drives (Deluxe)", "year": "2024"})
    assert answer == {}
    view = jobs.job(set_id)
    assert view["tags"]["album"] == "Night Drives (Deluxe)"
    assert view["tags"]["year"] == 2024
    assert {track["tags"]["album"] for track in view["children"]} == {"Night Drives (Deluxe)"}
    assert {track["tags"]["year"] for track in view["children"]} == {2024}
    jobs.manager.reset_field(set_id, "album")
    assert jobs.job(set_id)["tags"]["album"] == "Night Drives"


def test_a_set_takes_only_the_albums_fields(jobs: Harness) -> None:
    set_id = resolved(jobs)
    assert jobs.manager.update_fields(set_id, {"title": "New"}) == {"error": "internal"}
    refused = jobs.manager.update_fields(set_id, {"year": "next year"})
    assert refused == {"error": "invalid_fields", "fields": {"year": {"reason": "NOT_A_YEAR"}}}
    assert jobs.manager.toggle_change(set_id, "x", False) == {"error": "not_editable"}


def test_a_tracks_typed_number_is_checked_against_the_albums_total(jobs: Harness) -> None:
    set_id = resolved(jobs)
    first = track_id(jobs, set_id, 1)
    refused = jobs.manager.update_fields(first, {"track_no": "7"})
    assert refused == {
        "error": "invalid_fields",
        "fields": {"track_no": {"reason": "TOTAL_BEFORE_TRACK"}},
    }
    assert jobs.manager.update_fields(first, {"track_no": "5"}) == {}
    assert numbers(jobs, set_id)[0] == (5, 5)


def test_edits_and_cleanups_stay_with_their_own_track(jobs: Harness) -> None:
    jobs.steps.sources[sets.track_link(ALBUM, 1)] = sets.fakes.source_for(
        sets.track_link(ALBUM, 1), "Track 1 [FREE DL]"
    )
    set_id = resolved(jobs)
    first, second = track_id(jobs, set_id, 1), track_id(jobs, set_id, 2)
    (change,) = tracks(jobs, set_id)[0]["suggestion"]["changes"]
    assert jobs.manager.toggle_change(first, change["id"], False) == {}
    assert jobs.manager.update_fields(second, {"artist": "Kaia, Lou V"}) == {}
    assert jobs.manager.update_fields(first, {"title": "Track One"}) == {}
    one, two = tracks(jobs, set_id)[:2]
    assert one["tags"]["title"] == "Track One"
    assert one["suggestion"]["changes"][0]["enabled"] is False
    assert one["tags"]["artist"] == "Kaia"
    assert two["tags"]["artist"] == "Kaia, Lou V"
    assert two["tags"]["title"] == "Track 2"
    # A typed artist stays in the album.
    assert two["tags"]["album_artist"] == "Kaia"
    assert two["tags"]["album"] == "Night Drives"


# Saving


def test_saving_an_album_saves_each_track_with_its_number_and_one_cover(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.manager.set_included(set_id, track_id(jobs, set_id, 3), False)
    assert jobs.manager.save([set_id]) == {}
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(1, 4), (2, 4), (3, 4), (4, 4)]
    # The cover is the set's own, kept when it was read, and loaded for the first track only.
    (loaded,) = jobs.steps.cover_loads
    assert loaded.kind == "file"
    assert [call.place.cover.cover for call in jobs.steps.set_saves] == [sets.LOADED_COVER] * 4
    view = jobs.job(set_id)
    assert view["state"] == "saved"
    assert [track["state"] for track in view["children"]] == [
        "saved",
        "saved",
        "needs_review",
        "saved",
        "saved",
    ]
    album = jobs.library / "Kaia"
    assert sorted(path.name for path in album.iterdir()) == [
        f"Kaia - Track {n}.mp3" for n in (1, 2, 4, 5)
    ]


def test_a_playlist_saves_each_track_as_a_single_with_its_own_cover(jobs: Harness) -> None:
    set_id = resolved(jobs, PLAYLIST)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert [call.place for call in jobs.steps.set_saves] == [None, None, None]
    assert {call.cover.kind for call in jobs.steps.set_saves} == {"source"}
    assert jobs.steps.cover_loads == []
    assert jobs.job(set_id)["state"] == "saved"


def test_an_album_waits_for_every_tracks_info_before_any_track_saves(jobs: Harness) -> None:
    set_id = listed(jobs)
    for _ in range(3):
        jobs.pools["resolve"].run_one()
    jobs.pools.run("misc")
    jobs.pools.run("download")
    jobs.manager.save([set_id])
    jobs.pools.run("save")
    assert jobs.steps.set_saves == []
    assert jobs.job(set_id)["state"] == "saving"
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(n, 5) for n in range(1, 6)]


def test_leaving_out_a_track_still_fetching_lets_the_album_save(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.offline, "no answer"))
    jobs.pools.run_all()
    assert tracks(jobs, set_id)[0]["error"]["kind"] == "offline"
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert jobs.steps.set_saves == []
    assert jobs.manager.set_included(set_id, track_id(jobs, set_id, 1), False) == {}
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(n, 4) for n in range(1, 5)]


def test_a_track_that_fails_keeps_its_number_and_retry_saves_it_with_it(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    third = track_id(jobs, set_id, 3)
    jobs.steps.set_save_errors[third] = ScmpError(ErrorKind.disk, "full")
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert tracks(jobs, set_id)[2]["state"] == "error"
    assert jobs.job(set_id)["state"] == "needs_review"
    assert jobs.manager.retry(third) == {}
    jobs.pools.run_all()
    assert jobs.steps.saves_for(third)[-1].tags.track_no == 3
    assert jobs.steps.steps_for(third).count("fetch") == 1
    assert jobs.job(set_id)["state"] == "saved"


def test_the_album_is_fixed_once_a_track_saves(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    jobs.manager.save([track_id(jobs, set_id, 1)])
    jobs.pools.run_all()
    assert jobs.job(set_id)["album_settled"] is True
    second = track_id(jobs, set_id, 2)
    assert jobs.manager.set_album_mode(set_id, "singles") == {"error": "not_editable"}
    assert jobs.manager.set_included(set_id, second, False) == {"error": "not_editable"}
    assert jobs.manager.update_fields(set_id, {"album": "Other"}) == {"error": "not_editable"}
    link = {"kind": "url", "value": "https://example.com/cover.jpg"}
    assert jobs.manager.set_artwork(set_id, link["kind"], link["value"], None) == {
        "error": "not_editable"
    }
    # The tracks not saved yet can still be fixed.
    assert jobs.manager.update_fields(second, {"title": "Fixed"}) == {}


def test_a_taken_name_asks_about_that_track_and_the_answer_saves_it(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    taken = jobs.library / "Kaia" / "Kaia - Track 2.mp3"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"older file")
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    second = tracks(jobs, set_id)[1]
    assert second["state"] == "needs_review"
    assert second["collision"] == {"path": str(taken)}
    assert jobs.manager.answer_collision(second["id"], "replace") == {}
    jobs.pools.run_all()
    assert tracks(jobs, set_id)[1]["state"] == "saved"
    assert taken.read_bytes() == b"converted audio"
    assert jobs.job(set_id)["state"] == "saved"


def test_the_queues_save_all_saves_a_sets_tracks_and_other_tracks(jobs: Harness) -> None:
    single = jobs.add_and_resolve(sets.fakes.TRACK)
    set_id = resolved(jobs)
    jobs.manager.save([single, set_id])
    jobs.pools.run_all()
    assert jobs.job(single)["state"] == "saved"
    assert len(jobs.steps.set_saves) == 5


def test_cancel_puts_a_sets_waiting_saves_back_in_review(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.manager.save([set_id])
    jobs.manager.cancel(set_id)
    jobs.pools.run_all()
    assert jobs.steps.set_saves == []
    assert jobs.job(set_id)["state"] == "needs_review"


def test_a_track_left_out_isnt_downloaded(jobs: Harness) -> None:
    set_id = listed(jobs)
    first = track_id(jobs, set_id, 1)
    jobs.manager.set_included(set_id, first, False)
    jobs.pools.run_all()
    assert "fetch" not in jobs.steps.steps_for(first)
    jobs.manager.set_included(set_id, first, True)
    jobs.pools.run_all()
    assert jobs.steps.steps_for(first).count("fetch") == 1


# Skipping and removing


def test_skipping_a_set_skips_its_tracks_and_deletes_their_audio(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    staged = [Path(track["staged_audio"]) for track in tracks(jobs, set_id)]
    assert jobs.manager.skip(set_id) == {}
    jobs.pools.run_all()
    assert jobs.job(set_id)["state"] == "skipped"
    assert {track["state"] for track in tracks(jobs, set_id)} == {"skipped"}
    assert not any(path.exists() for path in staged)


def test_removing_a_set_takes_its_tracks_and_folders_with_it(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    first = track_id(jobs, set_id, 1)
    staged = Path(tracks(jobs, set_id)[0]["staged_audio"])
    assert jobs.manager.remove(set_id) == {}
    jobs.pools.run_all()
    assert jobs.manager.views() == []
    assert not staged.exists()
    assert jobs.manager.update_fields(first, {"title": "Gone"}) == {"error": "unknown_job"}
    assert {"type": "job_removed", "job_id": set_id} in jobs.events.events


def test_a_tracks_progress_reaches_the_ui_with_its_own_id(jobs: Harness) -> None:
    set_id = resolved(jobs)
    first = track_id(jobs, set_id, 1)
    jobs.pools.run("download")
    progress = [event for event in jobs.events.events if event["type"] == "progress"]
    assert first in {event["job_id"] for event in progress}
    assert not any(
        event["job"]["id"] != set_id for event in jobs.events.events if event["type"] == "job"
    )


def test_closing_counts_a_sets_tracks_that_could_still_save(jobs: Harness) -> None:
    set_id = resolved(jobs)
    assert jobs.manager.unsaved_count() == 1
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert jobs.manager.unsaved_count() == 0


# Pacing


def test_a_long_set_is_paced_by_the_rate_limit_without_losing_tracks_or_numbers(
    jobs: Harness,
) -> None:
    long_set = "https://soundcloud.com/kaia/sets/everything"
    sets.add_set(jobs.steps, long_set, sets.playlist_for(long_set, 150, title="Everything"))
    # About 100 tracks fit SoundCloud's limit, so the rest are answered 429 until it clears.
    jobs.steps.resolve_budget = 100
    set_id = listed(jobs, long_set)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    waits: list[float] = []
    for _ in range(4):
        limited = [track for track in tracks(jobs, set_id) if track["error"]]
        assert len(limited) == 50
        assert {track["error"]["kind"] for track in limited} == {"rate_limited"}
        assert jobs.steps.set_saves == []
        waits.append(_fire_waiting_retries(jobs))
        jobs.pools.run_all()
    assert waits == [60.0, 120.0, 300.0, 600.0]
    # The limit clears, and the next retries go through.
    jobs.steps.resolve_budget = None
    assert _fire_waiting_retries(jobs) == 1800.0
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(n, 150) for n in range(1, 151)]
    assert [call.tags.title for call in jobs.steps.set_saves] == [
        f"Track {n}" for n in range(1, 151)
    ]
    assert jobs.job(set_id)["state"] == "saved"


def _fire_waiting_retries(jobs: Harness) -> float:
    """Fire the automatic retries waiting now, and return how long each one waited."""
    waiting = [
        timer
        for timer in jobs.timers.started
        if not timer.cancelled and not getattr(timer, "fired", False)
    ]
    for timer in waiting:
        timer.fired = True
        timer.callback()
    (delay,) = {timer.delay for timer in waiting}
    return delay


# What can still change once tracks are saved


def test_a_track_put_back_in_while_saves_wait_holds_the_whole_album_back(jobs: Harness) -> None:
    set_id = listed(jobs)
    fifth = track_id(jobs, set_id, 5)
    jobs.manager.set_included(set_id, fifth, False)
    for _ in range(4):
        jobs.pools["resolve"].run_one()
    jobs.pools.run("misc")
    jobs.pools.run("download")
    jobs.manager.save([set_id])
    assert len(jobs.pools["save"].tasks) == 4
    # Track 5 still has no info, so the saves already queued wait for it.
    assert jobs.manager.set_included(set_id, fifth, True) == {}
    jobs.pools.run("save")
    assert jobs.steps.set_saves == []
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(n, 5) for n in range(1, 6)]


def test_the_sets_retry_leaves_out_tracks_that_can_never_save(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.steps.prepare_errors.extend(
        [ScmpError(ErrorKind.drm, "DRM protected"), ScmpError(ErrorKind.offline, "no answer")]
    )
    jobs.pools.run_all()
    first, second = track_id(jobs, set_id, 1), track_id(jobs, set_id, 2)
    jobs.manager.retry(set_id)
    jobs.pools.run_all()
    assert jobs.steps.steps_for(first).count("prepare") == 1
    assert jobs.steps.steps_for(second).count("prepare") == 2


def test_a_track_that_can_never_save_stays_out_of_an_album_with_saved_tracks(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.unavailable, "404"))
    jobs.pools.run_all()
    first = track_id(jobs, set_id, 1)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert saved_numbers(jobs) == [(n, 4) for n in range(1, 5)]
    # Getting its info now would move the numbers, so it isn't tried again.
    assert jobs.manager.retry(first) == {"error": "not_editable"}
    jobs.pools.run_all()
    assert jobs.steps.steps_for(first).count("prepare") == 1


def test_singles_can_still_be_put_back_in_after_some_are_saved(jobs: Harness) -> None:
    set_id = resolved(jobs, PLAYLIST)
    third = track_id(jobs, set_id, 3)
    jobs.manager.set_included(set_id, third, False)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert jobs.job(set_id)["state"] == "saved"
    assert jobs.manager.set_included(set_id, third, True) == {}
    assert jobs.job(set_id)["state"] == "needs_review"
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert len(jobs.steps.set_saves) == 3
    assert jobs.job(set_id)["state"] == "saved"


def test_skipping_a_set_with_every_track_left_out_skips_it(jobs: Harness) -> None:
    set_id = resolved(jobs, PLAYLIST)
    for number in (1, 2, 3):
        jobs.manager.set_included(set_id, track_id(jobs, set_id, number), False)
    jobs.manager.skip(set_id)
    assert jobs.job(set_id)["state"] == "skipped"


def test_skipping_a_set_with_saved_tracks_counts_it_as_saved(jobs: Harness) -> None:
    set_id = resolved(jobs, PLAYLIST)
    jobs.manager.save([track_id(jobs, set_id, 1)])
    jobs.pools.run_all()
    jobs.manager.skip(set_id)
    assert jobs.job(set_id)["state"] == "saved"
    assert [track["state"] for track in tracks(jobs, set_id)] == ["saved", "skipped", "skipped"]


def test_an_album_without_a_cover_shares_its_first_included_tracks(jobs: Harness) -> None:
    bare = "https://soundcloud.com/kaia/sets/bare"
    sets.add_set(jobs.steps, bare, sets.playlist_for(bare, 3, cover=None))
    set_id = resolved(jobs, bare)
    jobs.manager.set_included(set_id, track_id(jobs, set_id, 1), False)
    second = tracks(jobs, set_id)[1]
    assert jobs.job(set_id)["cover_url"] == second["source"]["artwork_url"]
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert jobs.steps.cover_owners == [sets.track_link(bare, 2)]


# Taken names


def _taken(jobs: Harness, *numbers: int) -> None:
    for number in numbers:
        path = jobs.library / "Kaia" / f"Kaia - Track {number}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"older file")


def test_one_answer_can_go_for_every_taken_name_in_the_set(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    _taken(jobs, 1, 2, 3, 4)
    jobs.manager.save([set_id])
    jobs.pools["save"].run_one()
    jobs.pools["save"].run_one()
    asked = [track for track in tracks(jobs, set_id) if track["collision"]]
    assert len(asked) == 2
    # Tracks 1 and 2 are asked now, and tracks 3 and 4 will be once their saves run.
    assert jobs.manager.answer_collision(asked[0]["id"], "replace", True) == {}
    jobs.pools.run_all()
    assert [track["state"] for track in tracks(jobs, set_id)] == ["saved"] * 5
    assert sorted(path.name for path in (jobs.library / "Kaia").iterdir()) == [
        f"Kaia - Track {n}.mp3" for n in range(1, 6)
    ]
    assert all(call.path.read_bytes() == b"converted audio" for call in jobs.steps.set_saves)


def test_an_answer_for_one_track_goes_only_for_that_track(jobs: Harness) -> None:
    set_id = resolved(jobs)
    jobs.pools.run("download")
    _taken(jobs, 1, 2)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    first, second = (track["id"] for track in tracks(jobs, set_id)[:2])
    jobs.manager.answer_collision(first, "skip")
    jobs.pools.run_all()
    states = [track["state"] for track in tracks(jobs, set_id)]
    assert states == ["skipped", "needs_review", "saved", "saved", "saved"]
    assert tracks(jobs, set_id)[1]["collision"] is not None
    assert jobs.manager.answer_collision(second, "keep_both") == {}


# What the UI gets


def test_numbers_stay_put_while_the_tracks_come_in(jobs: Harness) -> None:
    set_id = listed(jobs)
    seen: list[list[tuple[int | None, int | None]]] = []
    for _ in range(5):
        jobs.pools["resolve"].run_one()
        seen.append(numbers(jobs, set_id))
    # Each track shows the number it saves with as soon as its info is in.
    assert seen[0] == [(1, 5)] + [(None, None)] * 4
    assert seen[-1] == [(n, 5) for n in range(1, 6)]
    for before, after in itertools.pairwise(seen):
        assert all(old in (new, (None, None)) for old, new in zip(before, after, strict=True))


def test_a_change_to_one_track_sends_the_set_with_only_that_track(jobs: Harness) -> None:
    set_id = resolved(jobs)
    second = track_id(jobs, set_id, 2)
    jobs.events.events.clear()
    jobs.manager.update_fields(second, {"title": "Second"})
    (event,) = jobs.events.events
    assert event["type"] == "set_tracks"
    assert event["job"]["id"] == set_id
    assert "children" not in event["job"]
    assert event["count"] == 5
    assert [(index, track["tags"]["title"]) for index, track in event["tracks"]] == [(1, "Second")]


def test_a_sets_first_event_and_a_change_in_its_length_send_every_track(jobs: Harness) -> None:
    set_id = jobs.add(ALBUM)
    jobs.pools.run("resolve")
    listed_events = [event for event in jobs.events.for_job(set_id) if event["type"] == "job"]
    assert len(listed_events[-1]["job"]["children"]) == 5


def test_a_track_waiting_to_retry_its_info_after_save_says_it_saves_once_in(jobs: Harness) -> None:
    set_id = listed(jobs)
    jobs.steps.prepare_errors.append(ScmpError(ErrorKind.rate_limited, "429"))
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    first = tracks(jobs, set_id)[0]
    assert first["error"]["kind"] == "rate_limited"
    assert first["saves_once_in"] is True
    assert jobs.job(set_id)["state"] == "saving"
    jobs.timers.fire_all()
    jobs.pools.run_all()
    # Track 1 saves last, once its info is in, with its own number.
    assert sorted(saved_numbers(jobs)) == [(n, 5) for n in range(1, 6)]
    assert jobs.steps.set_saves[-1].tags.track_no == 1


def test_an_answer_for_the_rest_of_the_set_covers_only_the_save_it_was_given_in(
    jobs: Harness,
) -> None:
    set_id = resolved(jobs, PLAYLIST)
    jobs.pools.run("download")
    third = track_id(jobs, set_id, 3)
    jobs.manager.set_included(set_id, third, False)
    for number in (1, 2, 3):
        path = jobs.library / "Kaia" / f"Kaia - Track {number}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"older file")
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    jobs.manager.answer_collision(track_id(jobs, set_id, 1), "replace", True)
    jobs.pools.run_all()
    assert [track["state"] for track in tracks(jobs, set_id)[:2]] == ["saved", "saved"]
    # Put back in and saved later, track 3 is asked about its own file.
    jobs.manager.set_included(set_id, third, True)
    jobs.manager.save([set_id])
    jobs.pools.run_all()
    assert tracks(jobs, set_id)[2]["collision"] is not None
    assert (jobs.library / "Kaia" / "Kaia - Track 3.mp3").read_bytes() == b"older file"
