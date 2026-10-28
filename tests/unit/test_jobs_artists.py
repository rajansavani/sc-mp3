"""Tests for artist lists set by hand: main and featured names, which the tags and folder follow."""

import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app.job_artists import ArtistList, pasted_names, with_artists
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import TagSuggestion


def load_fixture(name: str, file_name: str) -> ModuleType:
    """A module from tests/fixtures/, which pytest's importlib mode can't import by name."""
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / file_name
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_fixture("scmp3_test_fixture_job_fakes", "job_fakes.py")
sets = load_fixture("scmp3_test_fixture_set_job_fakes", "set_job_fakes.py")
Harness = Any

# The track the user reported: SoundCloud lists all three names in the publisher field.
GG = "https://soundcloud.com/gunk/gg-fuckin-ez"
FACE = "gg fuckin ez ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧"
GG_SOURCE = replace(
    fakes.source_for(GG, "gg fuckin ez  ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧"),
    uploader="gunk",
    artists=["gunk, zayok, edyn "],
)
CYPHER_NAMES = [f"Guest {number:02d}" for number in range(1, 21)]


@pytest.fixture
def jobs(tmp_path: Path) -> Iterator[Harness]:
    harness = fakes.make_harness(tmp_path / "library")
    harness.steps.sources[GG] = GG_SOURCE
    yield harness
    harness.manager.shut_down()


def credit(job: dict[str, Any]) -> tuple[list[str], list[str]]:
    return job["suggestion"]["main_artists"], job["suggestion"]["featured"]


def saved_tags(jobs: Harness, job_id: str) -> Any:
    """The tags the job's file was written with."""
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "saved"
    return jobs.steps.finalized[-1].tags


# The reported track


def test_the_reported_track_starts_with_gunk_main_and_the_others_featured(jobs: Harness) -> None:
    job = jobs.job(jobs.add_and_resolve(GG))
    assert credit(job) == (["gunk"], ["zayok", "edyn"])
    assert job["artists_edited"] is False
    assert job["tags"]["artist"] == "gunk, zayok, edyn"


@pytest.mark.parametrize(
    ("style", "title", "artist"),
    [
        ("artist_field", FACE, "gunk, zayok, edyn"),
        ("feat_in_title", f"{FACE} (feat. edyn)", "gunk, zayok"),
        ("all_in_title", f"{FACE} (gunk, zayok, edyn)", "gunk, zayok"),
    ],
)
def test_moving_a_featured_name_to_main_changes_the_shown_and_saved_tags(
    jobs: Harness, style: str, title: str, artist: str
) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.manager.set_style(job_id, style)
    answer = jobs.manager.set_artists(job_id, ArtistList(("gunk", "zayok"), ("edyn",)))
    assert answer == {"artists": {"main": ["gunk", "zayok"], "featured": ["edyn"]}}
    job = jobs.job(job_id)
    assert credit(job) == (["gunk", "zayok"], ["edyn"])
    assert job["artists_edited"] is True
    assert (job["tags"]["title"], job["tags"]["artist"]) == (title, artist)
    jobs.pools.run("download")
    saved = saved_tags(jobs, job_id)
    assert (saved.title, saved.artist) == (title, artist)


@pytest.mark.parametrize(
    ("style", "title", "artist"),
    [
        ("artist_field", FACE, "gunk, zayok, edyn"),
        ("feat_in_title", FACE, "gunk, zayok, edyn"),
        ("all_in_title", f"{FACE} (gunk, zayok, edyn)", "gunk, zayok, edyn"),
    ],
)
def test_moving_every_name_to_main_leaves_no_feat_part(
    jobs: Harness, style: str, title: str, artist: str
) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.manager.set_style(job_id, style)
    jobs.manager.set_artists(job_id, ArtistList(("gunk", "zayok", "edyn"), ()))
    tags = jobs.job(job_id)["tags"]
    assert (tags["title"], tags["artist"]) == (title, artist)


def test_the_album_artist_and_folder_follow_the_first_main_artist(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.manager.set_artists(job_id, ArtistList(("zayok", "gunk"), ("edyn",)))
    job = jobs.job(job_id)
    assert job["tags"]["album_artist"] == "zayok"
    assert job["filename"].startswith("zayok/")


def test_a_typed_album_artist_keeps_the_folder_where_it_is(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.manager.update_fields(job_id, {"album_artist": "gunk"})
    jobs.manager.set_artists(job_id, ArtistList(("zayok",), ("gunk", "edyn")))
    job = jobs.job(job_id)
    assert job["tags"]["album_artist"] == "gunk"
    assert job["filename"].startswith("gunk/")


def test_twenty_featured_names_are_all_credited_and_saved(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_style(job_id, "feat_in_title")
    names = tuple(CYPHER_NAMES)
    assert "error" not in jobs.manager.set_artists(job_id, ArtistList(("Kaia",), names))
    tags = jobs.job(job_id)["tags"]
    assert tags["title"] == f"Midnight Drive (feat. {', '.join(names)})"
    assert tags["artist"] == "Kaia"
    jobs.pools.run("download")
    assert saved_tags(jobs, job_id).title == tags["title"]


# What an edited list survives


def test_a_cleanup_toggle_changes_the_title_but_not_an_edited_list(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artists(job_id, ArtistList(("Kaia", "Mara"), ()))
    changes = jobs.job(job_id)["suggestion"]["changes"]
    for change in changes:
        assert jobs.manager.toggle_change(job_id, change["id"], False) == {}
    job = jobs.job(job_id)
    # Without the cleanups, Lou V would be back in the title and out of the featured list.
    assert job["tags"]["title"] == "Midnight Drive (feat. Lou V) [prod. Hazy]"
    assert credit(job) == (["Kaia", "Mara"], [])
    assert job["tags"]["artist"] == "Kaia, Mara"


def test_a_settings_change_leaves_an_edited_list_alone(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artists(job_id, ArtistList(("Mara",), ("Kaia",)))
    jobs.change_settings(remove_producer_credits=False, title_style="feat_in_title")
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert credit(job) == (["Mara"], ["Kaia"])
    assert job["tags"]["title"] == "Midnight Drive [prod. Hazy] (feat. Kaia)"


def test_an_edit_made_while_a_settings_change_is_worked_out_keeps_both(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.change_settings(remove_producer_credits=False)
    # The refresh is waiting on the misc pool when the list changes.
    jobs.manager.set_artists(job_id, ArtistList(("Mara",), ()))
    jobs.pools.run("misc")
    job = jobs.job(job_id)
    assert credit(job) == (["Mara"], [])
    assert job["tags"]["title"] == "Midnight Drive [prod. Hazy]"


def test_resetting_the_list_brings_the_suggestion_back(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    before = jobs.job(job_id)
    jobs.manager.set_artists(job_id, ArtistList(("edyn",), ()))
    assert jobs.manager.set_artists(job_id, None) == {}
    job = jobs.job(job_id)
    assert credit(job) == (["gunk"], ["zayok", "edyn"])
    assert job["artists_edited"] is False
    assert job["tags"] == before["tags"]


def test_resetting_follows_the_cleanups_switched_meanwhile(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.manager.set_artists(job_id, ArtistList(("Mara",), ()))
    (feature,) = [
        change
        for change in jobs.job(job_id)["suggestion"]["changes"]
        if change["kind"] == "extract_feature"
    ]
    jobs.manager.toggle_change(job_id, feature["id"], False)
    jobs.manager.set_artists(job_id, None)
    assert credit(jobs.job(job_id)) == (["Kaia"], [])


def test_resetting_a_list_never_edited_sends_nothing(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    before = len(jobs.events.events)
    assert jobs.manager.set_artists(job_id, None) == {}
    assert len(jobs.events.events) == before


# Typing the credit


def test_a_typed_artist_wins_over_the_list_until_its_dropped(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.manager.set_artists(job_id, ArtistList(("gunk", "zayok"), ("edyn",)))
    jobs.manager.update_fields(job_id, {"artist": "gunk & friends"})
    assert jobs.job(job_id)["tags"]["artist"] == "gunk & friends"
    jobs.manager.reset_field(job_id, "artist")
    job = jobs.job(job_id)
    assert job["tags"]["artist"] == "gunk, zayok, edyn"
    assert credit(job) == (["gunk", "zayok"], ["edyn"])


# Featured artists with no main one


def test_featured_artists_with_no_main_one_hold_the_save(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.pools.run("download")
    jobs.manager.set_artists(job_id, ArtistList((), ("zayok", "edyn")))
    assert jobs.job(job_id)["needs_main_artist"] is True
    assert jobs.manager.save([job_id]) == {"error": "no_main_artist"}
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "needs_review"
    assert jobs.steps.finalized == []
    # Moving a name to the main row lets it save.
    jobs.manager.set_artists(job_id, ArtistList(("zayok",), ("edyn",)))
    assert jobs.job(job_id)["needs_main_artist"] is False
    assert saved_tags(jobs, job_id).artist == "zayok, edyn"


def test_save_all_saves_the_others_and_holds_the_one_with_no_main_artist(jobs: Harness) -> None:
    held, other = jobs.add_and_resolve(GG), jobs.add_and_resolve(fakes.OTHER)
    jobs.pools.run("download")
    jobs.manager.set_artists(held, ArtistList((), ("zayok",)))
    assert jobs.manager.save([held, other]) == {"error": "no_main_artist"}
    jobs.pools.run_all()
    assert [jobs.job(held)["state"], jobs.job(other)["state"]] == ["needs_review", "saved"]


def test_a_typed_artist_or_an_empty_list_saves(jobs: Harness) -> None:
    typed, empty = jobs.add_and_resolve(GG), jobs.add_and_resolve(fakes.OTHER)
    jobs.pools.run("download")
    jobs.manager.set_artists(typed, ArtistList((), ("zayok",)))
    jobs.manager.update_fields(typed, {"artist": "gunk"})
    jobs.manager.set_artists(empty, ArtistList((), ()))
    assert jobs.job(typed)["needs_main_artist"] is False
    assert jobs.manager.save([typed, empty]) == {}
    jobs.pools.run_all()
    assert [jobs.job(typed)["state"], jobs.job(empty)["state"]] == ["saved", "saved"]
    tags = {call.tags.artist for call in jobs.steps.finalized}
    assert tags == {"gunk", ""}


def test_a_retry_after_the_main_row_was_emptied_goes_back_to_review(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    jobs.pools.run("download")
    jobs.steps.finalize_errors.append(ScmpError(ErrorKind.disk, "full"))
    jobs.manager.save([job_id])
    jobs.pools.run_all()
    assert jobs.job(job_id)["state"] == "error"
    jobs.manager.set_artists(job_id, ArtistList((), ("gunk",)))
    tries = len(jobs.steps.finalized)
    assert jobs.manager.retry(job_id) == {}
    jobs.pools.run_all()
    job = jobs.job(job_id)
    assert (job["state"], job["error"]) == ("needs_review", None)
    assert len(jobs.steps.finalized) == tries


def test_a_set_saves_its_other_tracks_and_holds_the_one_with_no_main_artist(
    tmp_path: Path,
) -> None:
    harness = sets.make_set_harness(tmp_path / "library")
    try:
        set_id = harness.add(sets.ALBUM)
        harness.pools.run("resolve")
        harness.pools.run("resolve")
        harness.pools.run("misc")
        first = harness.job(set_id)["children"][0]["id"]
        harness.manager.set_artists(first, ArtistList((), ("Kaia",)))
        assert harness.manager.save([set_id]) == {"error": "no_main_artist"}
        harness.pools.run_all()
        states = [track["state"] for track in harness.job(set_id)["children"]]
        assert states == ["needs_review", "saved", "saved", "saved", "saved"]
        assert harness.steps.saves_for(first) == []
    finally:
        harness.manager.shut_down()


# Who can be edited


def test_a_saved_or_unknown_job_refuses_a_list(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve()
    jobs.pools.run("download")
    saved_tags(jobs, job_id)
    artists = ArtistList(("Mara",), ())
    assert jobs.manager.set_artists(job_id, artists) == {"error": "not_editable"}
    assert jobs.manager.set_artists("job-nope", artists) == {"error": "unknown_job"}


def test_a_set_track_takes_a_list_by_its_own_id(tmp_path: Path) -> None:
    harness = sets.make_set_harness(tmp_path / "library")
    try:
        set_id = harness.add(sets.ALBUM)
        harness.pools.run("resolve")
        harness.pools.run("resolve")
        harness.pools.run("misc")
        first = harness.job(set_id)["children"][0]["id"]
        assert "error" not in harness.manager.set_artists(first, ArtistList(("Kaia",), ("Lou V",)))
        track = harness.job(set_id)["children"][0]
        assert track["tags"]["artist"] == "Kaia, Lou V"
        assert track["artists_edited"] is True
        # The set's own id holds the album, not a credit.
        refused = harness.manager.set_artists(set_id, ArtistList(("Kaia",), ()))
        assert refused == {"error": "not_editable"}
        harness.manager.save([set_id])
        harness.pools.run_all()
        (call,) = harness.steps.saves_for(first)
        assert call.tags.artist == "Kaia, Lou V"
    finally:
        harness.manager.shut_down()


# The list itself


def test_a_list_keeps_names_without_the_spaces_around_them() -> None:
    artists = ArtistList.of([" gunk "], ["zayok", " edyn"])
    assert artists == ArtistList(("gunk",), ("zayok", "edyn"))
    assert ArtistList.from_dict(artists.to_dict()) == artists


def test_a_list_drops_invisible_edges_empty_names_and_repeats() -> None:
    artists = ArtistList.of(
        ["\u200egunk\u200b", "\u200b", "gunk\x1f"],
        ["edyn\ufeff", "EDYN", "  ", "Gunk", "zayok"],
    )
    assert artists == ArtistList(("gunk",), ("edyn", "zayok"))


def test_a_list_keeps_what_belongs_to_a_name() -> None:
    names = [
        "Axurya ✧",
        "Kaia 🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
        "Zoé",
    ]
    assert ArtistList.of(names, []).main == tuple(names)


@pytest.mark.parametrize(("main", "featured"), [("gunk", []), (["gunk"], None), (["gunk", 3], [])])
def test_a_list_that_isnt_names_is_refused(main: Any, featured: Any) -> None:
    with pytest.raises(TypeError):
        ArtistList.of(main, featured)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("gunk, zayok, edyn", ["gunk", "zayok", "edyn"]),
        ("Tyler, The Creator", ["Tyler, The Creator"]),
        ("Kaia, Tyler, The Creator & Lou V", ["Kaia", "Tyler, The Creator", "Lou V"]),
        ("Chase & Status", ["Chase & Status"]),
        ("gunk\nzayok\r\nedyn", ["gunk", "zayok", "edyn"]),
        ("\u200bedyn\u200b, edyn, ", ["edyn"]),
        ("", []),
    ],
)
def test_pasted_names_split_as_soundclouds_artist_field(text: str, names: list[str]) -> None:
    assert pasted_names(text) == names


def test_the_answer_and_the_tags_credit_the_cleaned_names(jobs: Harness) -> None:
    job_id = jobs.add_and_resolve(GG)
    answer = jobs.manager.set_artists(job_id, ArtistList.of(["gunk", "\u200b"], ["edyn\u200b"]))
    assert answer == {"artists": {"main": ["gunk"], "featured": ["edyn"]}}
    job = jobs.job(job_id)
    assert credit(job) == (["gunk"], ["edyn"])
    assert job["tags"]["artist"] == "gunk, edyn"
    assert job["filename"].startswith("gunk/")


def test_a_suggestion_without_a_list_set_by_hand_is_used_as_it_is() -> None:
    suggestion = TagSuggestion(base_title="Song", main_artists=["Kaia"], featured=["Lou V"])
    assert with_artists(suggestion, None) is suggestion
    credited = with_artists(suggestion, ArtistList(("Lou V",), ()))
    assert (credited.main_artists, credited.featured) == (["Lou V"], [])
    assert suggestion.main_artists == ["Kaia"]
