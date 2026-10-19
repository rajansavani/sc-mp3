"""Tests for the terminal review of `scmp3 get` and the questions it asks."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from scmp3 import cli_review
from scmp3.cli_review import (
    FIELD_PROBLEMS,
    REVIEW_HELP,
    InputEnded,
    TrackReview,
    ask_about_collision,
    audio_text,
    cover_text,
    read_answer,
    review_text,
    run_review,
)
from scmp3.core import pipeline
from scmp3.core.convert import AudioInfo
from scmp3.core.models import FormatOption, Settings, SourceInfo
from scmp3.core.suggest import suggest

# The design's sample title, which has a feature, a producer credit and promo text.
SOURCE = SourceInfo(
    kind="soundcloud",
    id="soundcloud:tracks:1",
    url="https://soundcloud.com/kaia/midnight-drive",
    title="Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
    uploader="Kaia",
    artwork_url="https://i1.sndcdn.com/artworks-000000000001-abcdef-original.jpg",
)
AAC_160 = FormatOption("hls_aac_160k", "m4a", codec="aac", bitrate=160.0)


def staged(downloaded: FormatOption = AAC_160) -> pipeline.Staged:
    audio = AudioInfo(duration=200.0, bitrate=320, codec="mp3", sample_rate=44100)
    return pipeline.Staged(Path("audio.mp3"), "mp3", audio, downloaded)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(library_dir=tmp_path / "library")


@pytest.fixture
def review(settings: Settings) -> TrackReview:
    return TrackReview(SOURCE, suggest(SOURCE, settings), settings, None)


def answers(*lines: str) -> Iterator[str]:
    return iter(lines)


def scripted(*lines: str) -> cli_review.Reader:
    """A reader that gives these answers in turn."""
    remaining = answers(*lines)

    def read(prompt: str) -> str:
        return next(remaining)

    return read


def change_number(review: TrackReview, kind: str) -> int:
    kinds = [change.kind for change in review.suggestion.changes]
    return kinds.index(kind) + 1


def test_the_review_starts_from_the_suggestion(review: TrackReview) -> None:
    tags = review.tags()
    assert (tags.title, tags.artist, tags.album) == (
        "Midnight Drive",
        "Kaia, Lou V",
        "Midnight Drive",
    )
    assert tags.source_url == SOURCE.url


def test_turning_a_change_off_puts_its_text_back(review: TrackReview) -> None:
    assert review.answer(str(change_number(review, "remove_producer"))) is None
    assert review.tags().title == "Midnight Drive [prod. Hazy]"
    # Turning it on again removes the text again.
    review.answer(str(change_number(review, "remove_producer")))
    assert review.tags().title == "Midnight Drive"


def test_a_number_without_a_change_says_so(review: TrackReview) -> None:
    assert review.answer("9") == "There's no change 9."
    assert review.answer("0") == "There's no change 0."


def test_a_field_name_and_value_set_the_field(review: TrackReview) -> None:
    assert review.answer("Title  Midnight Drive (VIP)") is None
    assert review.answer("artist Kaia") is None
    assert review.answer("track 3/12") is None
    tags = review.tags()
    assert (tags.title, tags.artist) == ("Midnight Drive (VIP)", "Kaia")
    assert (tags.track_no, tags.track_total) == (3, 12)


def test_a_field_name_alone_clears_the_field(review: TrackReview) -> None:
    review.answer("genre House")
    assert review.answer("genre") is None
    assert review.answer("album") is None
    tags = review.tags()
    assert (tags.genre, tags.album) == (None, "")


@pytest.mark.parametrize("name", ["title", "artist"])
def test_the_title_and_artist_cant_be_cleared(review: TrackReview, name: str) -> None:
    assert review.answer(name) == f"The {name} can't be empty."
    assert review.overrides == {}


@pytest.mark.parametrize(
    ("answer", "field"),
    [
        ("year soon", "year"),
        ("year 0", "year"),
        ("track three", "track_no"),
        ("track 5/3", "track_total"),
    ],
)
def test_a_number_no_tag_can_hold_is_refused_and_forgotten(
    review: TrackReview, answer: str, field: str
) -> None:
    review.answer("year 2024")
    assert review.answer(answer) == FIELD_PROBLEMS[field]
    assert review.overrides == {"year": "2024"}


def test_anything_else_gets_the_help(review: TrackReview) -> None:
    assert review.answer("louder") == REVIEW_HELP


def test_enter_saves_the_tags_as_the_review_left_them(
    review: TrackReview, capsys: pytest.CaptureFixture[str]
) -> None:
    tags = run_review(review, staged(), scripted("year 2024", "bogus", ""))
    assert tags is not None
    assert tags.year == 2024
    shown = capsys.readouterr()
    assert shown.out == ""
    assert REVIEW_HELP in shown.err


@pytest.mark.parametrize("answer", ["q", "skip", " Q "])
def test_q_skips_the_track(review: TrackReview, answer: str) -> None:
    assert run_review(review, staged(), scripted(answer)) is None


def test_the_review_shows_the_original_the_fields_the_changes_and_the_file(
    review: TrackReview, settings: Settings
) -> None:
    text = review_text(review, staged())
    assert "Original title: Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL" in text
    assert "Uploaded by Kaia" in text
    assert "  Title   Midnight Drive" in text
    assert "  Artist  Kaia, Lou V" in text
    assert "  Genre   (empty)" in text
    assert "1. on   " in text
    assert "Lou V credited as featured artist" in text
    assert "Cover: the track's artwork" in text
    assert "Audio: AAC 160 kbps stream → MP3 320 kbps" in text
    assert f"Saves as Kaia, Lou V - Midnight Drive.mp3 in {settings.library_dir}" in text


def test_saves_as_follows_a_typed_album_into_its_album_folder(settings: Settings) -> None:
    subfolders = replace(settings, album_subfolders=True)
    review = TrackReview(SOURCE, suggest(SOURCE, subfolders), subfolders, None)
    # A single's album comes from the setting, so it stays at the top of the library.
    flat = review_text(review, staged())
    assert flat.endswith(f"Midnight Drive.mp3 in {subfolders.library_dir}")
    review.answer("album Night Drives EP")
    album_folder = subfolders.library_dir / "Kaia - Night Drives EP"
    assert review_text(review, staged()).endswith(f"Midnight Drive.mp3 in {album_folder}")


def test_a_change_that_is_off_shows_as_off(review: TrackReview) -> None:
    review.answer("1")
    assert "1. off  " in review_text(review, staged())


def test_the_review_uses_the_tracks_title_style(review: TrackReview) -> None:
    review.style = "feat_in_title"
    text = review_text(review, staged())
    assert "  Title   Midnight Drive (feat. Lou V)" in text
    assert "Saves as Kaia - Midnight Drive (feat. Lou V).mp3" in text


@pytest.mark.parametrize(
    ("artwork_url", "is_avatar", "shown"),
    [
        (None, False, "none, since the track has no artwork"),
        ("https://i1.sndcdn.com/avatars-1-original.jpg", True, "the uploader's profile picture"),
        ("https://i1.sndcdn.com/artworks-1-original.jpg", False, "the track's artwork"),
    ],
)
def test_the_cover_line_flags_a_profile_picture(
    artwork_url: str | None, is_avatar: bool, shown: str
) -> None:
    source = SourceInfo(
        "soundcloud", "x", "u", "t", "a", artwork_url=artwork_url, artwork_is_avatar=is_avatar
    )
    assert cover_text(source).startswith(shown)


@pytest.mark.parametrize(
    ("downloaded", "line"),
    [
        (AAC_160, "AAC 160 kbps stream → MP3 320 kbps"),
        (FormatOption("download", "wav", is_original=True), "Original WAV → MP3 320 kbps"),
        (FormatOption("http_mp3_0_0", "mp3", codec="mp3"), "MP3 stream → MP3 320 kbps"),
    ],
)
def test_the_audio_line_says_what_was_downloaded_and_what_is_saved(
    downloaded: FormatOption, line: str
) -> None:
    assert audio_text(staged(downloaded)) == line


def test_a_taken_name_is_asked_about_until_the_answer_is_one_it_knows(tmp_path: Path) -> None:
    prompts: list[str] = []
    replies = answers("maybe", " K ")

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    assert ask_about_collision(read)(tmp_path / "Song.mp3") == "keep_both"
    assert len(prompts) == 2
    assert prompts[0].startswith(f"Song.mp3 already exists in {tmp_path}.")


def test_questions_go_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert read_answer("Save it again? ") == "yes"
    shown = capsys.readouterr()
    assert (shown.out, shown.err) == ("", "Save it again? ")


def test_a_byte_order_mark_from_a_powershell_pipe_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda: "﻿artist Kaia")
    assert read_answer("> ") == "artist Kaia"


def test_closed_input_raises_input_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    def closed() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    with pytest.raises(InputEnded):
        read_answer("> ")
