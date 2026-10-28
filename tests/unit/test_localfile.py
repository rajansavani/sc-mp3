"""Tests for reading a file you already have as a source to re-tag."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Settings, Tags
from scmp3.core.sources import localfile
from tests.fixtures.local_files import OLD_TAGS, make_cover, tagged_copy


def test_a_file_is_read_as_a_source_of_kind_file(tiny_mp3: Path, tmp_path: Path) -> None:
    path = tagged_copy(tiny_mp3, tmp_path / "old favorite.mp3")
    local = localfile.read_file(path)
    source = local.source
    assert (source.kind, source.id, source.url) == ("file", str(path), str(path))
    assert source.title == "Sunday Mornings (Official Audio)"
    assert source.uploader == "Teo Marr"
    assert source.artists == ["Teo Marr"]
    assert source.album == "Sunday Mornings"
    assert source.release_date == date(2019, 1, 1)
    assert (source.genre, source.isrc, source.label) == ("Soul", "GBUM71029604", "Marr Records")
    assert source.description == OLD_TAGS.comment
    assert local.fmt == "mp3"
    assert local.duration is not None and local.duration > 0


def test_the_suggestion_cleans_the_title_and_keeps_what_the_cleanup_doesnt_touch(
    tiny_mp3: Path, tmp_path: Path
) -> None:
    local = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3"))
    suggestion = localfile.suggestion_for(local, Settings())
    assert suggestion.base_title == "Sunday Mornings"
    assert suggestion.main_artists == ["Teo Marr"]
    assert [change.span for change in suggestion.changes if change.enabled] == ["(Official Audio)"]
    assert (suggestion.album, suggestion.year, suggestion.genre) == (
        "Sunday Mornings",
        2019,
        "Soul",
    )
    assert suggestion.album_artist == "Teo Marr & Friends"
    assert (suggestion.track_no, suggestion.track_total) == (3, 12)


def test_an_artist_tag_that_lists_names_stays_as_written(tiny_mp3: Path, tmp_path: Path) -> None:
    # Joined again with another separator setting, the list would change for no reason.
    tags = Tags(title="Midnight Drive", artist="Kaia, Lou V", album="", album_artist="")
    local = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3", tags))
    suggestion = localfile.suggestion_for(local, Settings(artist_separator=" & "))
    assert (suggestion.main_artists, suggestion.featured) == (["Kaia, Lou V"], [])


def test_a_feature_in_the_title_is_kept_for_the_title_style(tiny_mp3: Path, tmp_path: Path) -> None:
    tags = Tags(title="Midnight Drive (feat. Lou V)", artist="Kaia", album="", album_artist="")
    local = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3", tags))
    suggestion = localfile.suggestion_for(local, Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("Midnight Drive", ["Kaia"])
    assert suggestion.featured == ["Lou V"]


def test_the_artist_in_the_title_goes_when_the_artist_tag_names_it(
    tiny_mp3: Path, tmp_path: Path
) -> None:
    tags = Tags(title="Teo Marr - Night Bus", artist="Teo Marr", album="", album_artist="")
    local = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3", tags))
    suggestion = localfile.suggestion_for(local, Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("Night Bus", ["Teo Marr"])


def test_a_file_without_tags_has_no_title_or_artist_to_suggest(
    tiny_m4a: Path, tmp_path: Path
) -> None:
    path = tmp_path / "untagged.m4a"
    shutil.copyfile(tiny_m4a, path)
    local = localfile.read_file(path)
    assert (local.source.title, local.source.uploader, local.source.artists) == ("", "", [])
    assert local.source.album is None
    assert local.fmt == "m4a"
    assert local.cover is None


def test_the_cover_in_the_file_is_read_with_its_tags(tiny_mp3: Path, tmp_path: Path) -> None:
    cover = make_cover()
    path = tagged_copy(tiny_mp3, tmp_path / "song.mp3", cover=cover)
    assert localfile.read_file(path).cover == cover.jpeg
    assert localfile.embedded_cover(path) == cover.jpeg


def test_a_file_that_isnt_there_is_called_missing(tmp_path: Path) -> None:
    with pytest.raises(ScmpError) as error_info:
        localfile.read_file(tmp_path / "gone.mp3")
    assert (error_info.value.kind, error_info.value.reason) == (ErrorKind.disk, "file_missing")


def test_an_m4a_named_mp3_is_refused_as_unreadable(tiny_m4a: Path, tmp_path: Path) -> None:
    path = tmp_path / "really an m4a.mp3"
    shutil.copyfile(tiny_m4a, path)
    with pytest.raises(ScmpError) as error_info:
        localfile.read_file(path)
    assert error_info.value.kind == ErrorKind.convert_failed
    assert error_info.value.reason == "unreadable_file"


def test_a_file_that_isnt_audio_is_refused_as_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "notes.mp3"
    path.write_text("not audio at all", encoding="utf-8")
    with pytest.raises(ScmpError) as error_info:
        localfile.read_file(path)
    assert error_info.value.reason == "unreadable_file"


@pytest.mark.parametrize(
    ("field", "value", "changes"),
    [
        ("title", "Sunday Mornings", True),
        ("artist", "Teo Marr, Kaia", True),
        ("album", "", True),
        ("year", 2020, False),
        ("genre", "Jazz", False),
        ("title", "Sunday Mornings (Official Audio)", False),
    ],
)
def test_only_a_new_title_artist_or_album_changes_what_playlists_know_the_song_by(
    tiny_mp3: Path, tmp_path: Path, field: str, value: object, changes: bool
) -> None:
    source = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3")).source
    current = Tags(
        title=source.title, artist=source.uploader, album=source.album or "", album_artist=""
    )
    new = Tags(**{**current.__dict__, field: value})
    assert localfile.changes_identity(source, new) is changes


def test_the_identity_is_the_files_title_artist_and_album_as_written(
    tiny_mp3: Path, tmp_path: Path
) -> None:
    source = localfile.read_file(tagged_copy(tiny_mp3, tmp_path / "song.mp3")).source
    assert localfile.identity(source) == {
        "title": "Sunday Mornings (Official Audio)",
        "artist": "Teo Marr",
        "album": "Sunday Mornings",
    }
