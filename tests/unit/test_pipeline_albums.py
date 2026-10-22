"""Tests for the album mapping: shared album fields, numbers among included tracks, the cover."""

from datetime import date

import pytest

from scmp3.core.models import PlaylistInfo, Settings, SourceInfo, TagSuggestion
from scmp3.core.pipeline import (
    album_cover_source,
    album_fields,
    album_overrides,
    render_album_track,
    track_numbers,
)
from scmp3.core.suggest import InvalidOverride

LANTERN = PlaylistInfo(
    title="Lantern",
    owner="Night Records",
    set_type="album",
    is_album=True,
    release_date=date(2026, 2, 27),
    genre="#Ambient",
    artwork_url="https://i1.sndcdn.com/artworks-lantern-original.jpg",
)
BARE = PlaylistInfo(title="Lantern", owner="Night Records")
TRACK = SourceInfo(
    kind="soundcloud",
    id="soundcloud:tracks:1",
    url="https://soundcloud.com/mara-lune/lantern",
    title="Lantern",
    uploader="Night Records",
    artists=["Mara Lune"],
    artwork_url="https://i1.sndcdn.com/artworks-track-original.jpg",
)


def by(*artists: str, year: int | None = 2024, genre: str | None = "Folk") -> TagSuggestion:
    return TagSuggestion(base_title="Lantern", main_artists=list(artists), year=year, genre=genre)


MARA = by("Mara Lune")
SHARED = {"album": "Lantern", "album_artist": "Mara Lune"}


def test_track_numbers_count_only_the_included_tracks() -> None:
    assert track_numbers([True, False, True, True, False]) == [1, None, 2, 3, None]


def test_track_numbers_with_nothing_included_are_all_none() -> None:
    assert track_numbers([False, False]) == [None, None]


def test_the_album_artist_is_the_artist_every_track_shares() -> None:
    # A label's upload of one artist's album, with the spelling varying.
    fields = album_fields(LANTERN, [MARA, by("mara lune"), by("Mara Lune", "Teo Marr")])
    assert fields["album_artist"] == "Mara Lune"


def test_without_one_shared_artist_the_album_artist_is_the_sets_owner() -> None:
    assert album_fields(LANTERN, [MARA, by("Teo Marr")])["album_artist"] == "Night Records"
    assert album_fields(LANTERN)["album_artist"] == "Night Records"


def test_the_album_shares_the_sets_title_year_and_genre() -> None:
    assert album_fields(LANTERN, [MARA]) == {
        "album": "Lantern",
        "album_artist": "Mara Lune",
        "year": "2026",
        "genre": "Ambient",
    }


def test_without_a_year_or_genre_of_its_own_the_album_takes_its_first_tracks() -> None:
    fields = album_fields(BARE, [MARA, by("Mara Lune", year=2019, genre="Pop")])
    assert (fields["year"], fields["genre"]) == ("2024", "Folk")


def test_a_year_or_genre_nothing_gives_is_left_for_each_track() -> None:
    assert album_fields(BARE, [by("Mara Lune", year=None, genre=None)]) == {
        "album": "Lantern",
        "album_artist": "Mara Lune",
    }


def test_a_blank_set_title_falls_back_to_the_first_tracks_publisher_album() -> None:
    blank = PlaylistInfo(title=" ", owner="")
    first = TagSuggestion(base_title="Lantern", main_artists=["Mara Lune"], album="Lantern EP")
    assert album_fields(blank, [first])["album"] == "Lantern EP"
    assert album_fields(blank) == {"album": "", "album_artist": ""}


def test_a_tracks_own_edits_go_over_what_the_album_shares() -> None:
    edits = {"album_artist": "Mara", "title": "Lantern (Live)"}
    assert album_overrides(SHARED, edits) == {
        "album": "Lantern",
        "album_artist": "Mara",
        "title": "Lantern (Live)",
    }


def test_the_album_shares_the_sets_listed_cover_or_else_the_first_tracks() -> None:
    assert album_cover_source(LANTERN, TRACK) is LANTERN
    assert album_cover_source(BARE, TRACK) is TRACK


def render(**edits: str) -> tuple[str, str, int | None, int | None]:
    overrides = album_overrides(SHARED, edits)
    suggestion = TagSuggestion(base_title="Lantern", main_artists=["Mara Lune"], album="Lantern")
    tags = render_album_track(
        TRACK, suggestion, Settings(), track_no=2, track_total=9, overrides=overrides
    )
    return tags.album, tags.album_artist, tags.track_no, tags.track_total


def test_an_album_track_gets_the_shared_album_and_its_number() -> None:
    assert render() == ("Lantern", "Mara Lune", 2, 9)


def test_a_typed_artist_doesnt_change_the_album_artist() -> None:
    assert render(artist="Someone Else")[1] == "Mara Lune"


def test_the_tracks_own_edits_win_over_what_the_album_shares() -> None:
    assert render(album="Lantern (Live)", album_artist="Mara") == ("Lantern (Live)", "Mara", 2, 9)
    assert render(track_no="3/12")[2:] == (3, 12)


def test_a_typed_number_past_the_albums_total_is_refused() -> None:
    with pytest.raises(InvalidOverride):
        render(track_no="10")
