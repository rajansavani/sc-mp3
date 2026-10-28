"""Tests for `scmp3 retag`, on real files in a temp folder and answers typed by the test."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from scmp3 import cli, cli_output
from scmp3.core import tagger
from scmp3.core.settings import SettingsStore
from scmp3.core.settings import default_path as default_settings_path
from tests.fixtures.local_files import OLD_TAGS, make_cover, tagged_copy


@pytest.fixture
def song(tiny_mp3: Path, tmp_path: Path) -> Path:
    return tagged_copy(tiny_mp3, tmp_path / "old songs" / "old favorite.mp3", cover=make_cover())


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answers for input(), in order. A test appends to it."""
    lines: list[str] = []
    replies: Iterator[str] = iter(lines)

    def fake_input() -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    return lines


def retag(*args: str) -> int:
    return cli.main(["retag", *args])


def test_yes_writes_the_suggested_tags_and_prints_only_the_path(
    song: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert retag(str(song), "--yes") == cli_output.EXIT_OK
    shown = capsys.readouterr()
    assert shown.out == f"{song}\n"
    assert tagger.read(song)[0].title == "Sunday Mornings"
    assert cli_output.PLAYLIST_NOTE in shown.err
    assert cli_output.RETAGGED_NOTE in shown.err


def test_the_review_shows_the_files_tags_and_where_they_go(
    song: Path, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    typed += ["q"]
    assert retag(str(song)) == cli_output.EXIT_OK
    err = capsys.readouterr().err
    assert f"Title in the file: {OLD_TAGS.title}" in err
    assert f"Artist in the file: {OLD_TAGS.artist}" in err
    assert "Cover: the one in the file" in err
    assert f"Writes the new tags into {song}" in err


def test_a_new_title_asks_about_playlists_and_no_leaves_the_file_alone(
    song: Path, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    before = song.read_bytes()
    typed += ["", "n"]
    assert retag(str(song)) == cli_output.EXIT_OK
    shown = capsys.readouterr()
    assert cli_output.PLAYLIST_QUESTION in shown.err
    assert cli_output.SKIPPED in shown.err
    assert shown.out == ""
    assert song.read_bytes() == before


def test_enter_at_the_playlist_question_writes_the_tags(
    song: Path, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    typed += ["", ""]
    assert retag(str(song)) == cli_output.EXIT_OK
    assert capsys.readouterr().out == f"{song}\n"
    assert tagger.read(song)[0].title == "Sunday Mornings"


def test_keeping_the_title_artist_and_album_asks_nothing(
    song: Path, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Turning the cleanup off keeps the title as it is in the file.
    typed += ["1", "year 2021", ""]
    assert retag(str(song)) == cli_output.EXIT_OK
    assert cli_output.PLAYLIST_QUESTION not in capsys.readouterr().err
    tags = tagger.read(song)[0]
    assert (tags.title, tags.year) == (OLD_TAGS.title, 2021)


def test_the_playlist_question_can_be_turned_off_in_the_settings(
    song: Path, typed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    SettingsStore(default_settings_path()).update({"retag_playlist_warning": False})
    typed += [""]
    assert retag(str(song)) == cli_output.EXIT_OK
    assert cli_output.PLAYLIST_QUESTION not in capsys.readouterr().err
    assert tagger.read(song)[0].title == "Sunday Mornings"


def test_style_picks_the_title_style(
    tiny_mp3: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tags = OLD_TAGS.__class__(title="Song (feat. Lou V)", artist="Kaia", album="", album_artist="")
    path = tagged_copy(tiny_mp3, tmp_path / "song.mp3", tags)
    assert retag(str(path), "--yes", "--style", "artist_field") == cli_output.EXIT_OK
    written = tagger.read(path)[0]
    assert (written.title, written.artist) == ("Song", "Kaia, Lou V")


def test_links_are_refused_with_the_command_that_saves_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    link = "https://soundcloud.com/kaia/midnight-drive"
    assert retag(link) == cli_output.EXIT_USAGE
    assert capsys.readouterr().err.strip() == f"{link}: {cli_output.LINKS_NEED_GET}"


def test_a_missing_file_is_refused_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = tmp_path / "gone.mp3"
    assert retag(str(gone)) == cli_output.EXIT_USAGE
    assert "File not found. Check the path." in capsys.readouterr().err


def test_a_file_that_isnt_audio_says_so_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "notes.mp3"
    notes.write_text("not audio", encoding="utf-8")
    assert retag(str(notes), "--yes") == cli_output.EXIT_ERROR
    assert cli_output.REASON_MESSAGES["unreadable_file"] in capsys.readouterr().err


def test_get_refuses_a_file_and_names_retag(
    song: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("scmp3.core.paths.ffmpeg_exe", lambda: Path("ffmpeg"))
    assert cli.main(["get", str(song)]) == cli_output.EXIT_USAGE
    assert capsys.readouterr().err.strip() == f"{song}: {cli_output.FILES_NEED_RETAG}"


def test_several_files_are_re_tagged_in_turn(
    song: Path, tiny_m4a: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tagged_copy(tiny_m4a, tmp_path / "Night Bus.m4a")
    assert retag(str(song), str(other), "--yes") == cli_output.EXIT_OK
    assert capsys.readouterr().out.splitlines() == [str(song), str(other)]
