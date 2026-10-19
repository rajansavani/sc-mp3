"""Tests for writing and reading tags through `tagger`, on one-second MP3 and M4A files."""

import dataclasses
import io
import shutil
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from PIL import Image

from scmp3.core import artwork, tagger
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Tags

URL = "https://soundcloud.com/kaia/midnight-drive"

FULL_TAGS = Tags(
    title="Midnight Drive (feat. Lou V) [VIP Mix]",
    artist="Kaia, Lou V",
    album="Midnight Drive",
    album_artist="Kaia",
    year=2024,
    genre="Drum & Bass",
    track_no=3,
    track_total=12,
    comment=URL,
    source_url=URL,
    source_id="soundcloud:tracks:123456",
    isrc="GBUM71029604",
    label="Kaia's Records",
)

# \u2019 is a curly apostrophe and \u2013 an en dash, both common in real titles.
TRICKY_TITLES = {
    "apostrophes": "Don't Stop 'Til It's Done \u2019n\u2019 Rock",
    "accents and scripts": "Beyoncé \u2013 Ελληνικά \u2013 日本語 \u2013 Ünïcödé",
    "emoji": "Night 🌙 Drive 🎧",
    "slash": "AC/DC Tribute / Live",
    "300 characters": "Long title " * 27 + "end",
}


def make_cover(color: tuple[int, int, int] = (255, 0, 0), side: int = 600) -> Cover:
    """A real cover, made by artwork.process from a generated PNG."""
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), color).save(buffer, "PNG")
    return artwork.process(buffer.getvalue())


@pytest.fixture(params=[".mp3", ".m4a"])
def audio(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """A copy of the silent MP3, then of the silent M4A."""
    source = request.getfixturevalue("tiny_mp3" if request.param == ".mp3" else "tiny_m4a")
    return Path(shutil.copy(source, tmp_path / f"track{request.param}"))


@pytest.fixture(scope="module")
def cover() -> Cover:
    return make_cover()


# Round trips


def test_every_field_and_the_cover_read_back_identically(audio: Path, cover: Cover) -> None:
    tagger.write(audio, FULL_TAGS, cover)
    tags, cover_bytes = tagger.read(audio)
    assert tags == FULL_TAGS
    assert cover_bytes == cover.jpeg


@pytest.mark.parametrize("title", TRICKY_TITLES.values(), ids=TRICKY_TITLES.keys())
def test_tricky_text_reads_back_identically(audio: Path, cover: Cover, title: str) -> None:
    written = dataclasses.replace(FULL_TAGS, title=title, artist=title, album=title, label=title)
    tagger.write(audio, written, cover)
    tags, _ = tagger.read(audio)
    assert tags == written


def test_a_300_character_title_is_kept_whole(audio: Path) -> None:
    title = "x" * 300
    tagger.write(audio, dataclasses.replace(FULL_TAGS, title=title), None)
    assert tagger.read(audio)[0].title == title


def test_empty_fields_are_left_out_and_read_back_empty(audio: Path) -> None:
    sparse = Tags(title="Midnight Drive", artist="Kaia", album="", album_artist="")
    tagger.write(audio, sparse, None)
    assert tagger.read(audio) == (sparse, None)


def test_a_track_number_without_a_total_reads_back_alone(audio: Path) -> None:
    written = dataclasses.replace(FULL_TAGS, track_no=5, track_total=None)
    tagger.write(audio, written, None)
    tags, _ = tagger.read(audio)
    assert (tags.track_no, tags.track_total) == (5, None)


def test_the_extension_is_matched_without_regard_to_case(tiny_mp3: Path, tmp_path: Path) -> None:
    path = Path(shutil.copy(tiny_mp3, tmp_path / "TRACK.MP3"))
    tagger.write(path, FULL_TAGS, None)
    assert tagger.read(path)[0] == FULL_TAGS


# Re-tagging


def test_writing_without_a_cover_removes_the_old_one(audio: Path, cover: Cover) -> None:
    tagger.write(audio, FULL_TAGS, cover)
    tagger.write(audio, FULL_TAGS, None)
    assert tagger.read(audio)[1] is None


# Choosing the format


def test_fmt_names_the_format_of_a_temp_copy(tiny_mp3: Path, tmp_path: Path) -> None:
    temp = Path(shutil.copy(tiny_mp3, tmp_path / ".track.mp3.retag.tmp"))
    tagger.write(temp, FULL_TAGS, None, fmt="mp3")
    final = temp.rename(tmp_path / "track.mp3")
    assert ID3(final).version == (2, 3, 0)
    assert tagger.read(final)[0] == FULL_TAGS


def test_fmt_works_for_an_m4a_temp_copy(tiny_m4a: Path, tmp_path: Path) -> None:
    temp = Path(shutil.copy(tiny_m4a, tmp_path / ".track.m4a.retag.tmp"))
    tagger.write(temp, FULL_TAGS, None, fmt="m4a")
    final = temp.rename(tmp_path / "track.m4a")
    assert tagger.read(final)[0] == FULL_TAGS


def test_fmt_overrides_the_extension(tiny_mp3: Path, tmp_path: Path) -> None:
    # An MP3 misnamed as .m4a would fail as an M4A, so success shows fmt chose the MP3 writer.
    misnamed = Path(shutil.copy(tiny_mp3, tmp_path / "track.m4a"))
    tagger.write(misnamed, FULL_TAGS, None, fmt="mp3")
    assert ID3(misnamed).version == (2, 3, 0)


@pytest.mark.parametrize("name", ["track.tmp", "track.flac", "track"])
def test_an_unknown_extension_without_fmt_is_refused(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"audio")
    with pytest.raises(ScmpError) as caught:
        tagger.write(path, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.internal
    assert "pass fmt" in str(caught.value)
    assert path.read_bytes() == b"audio"
    with pytest.raises(ScmpError):
        tagger.read(path)


# Reading files sc-mp3 didn't write


def test_reading_ffmpegs_files_gives_blank_tags(tiny_mp3: Path, tiny_m4a: Path) -> None:
    blank = Tags(title="", artist="", album="", album_artist="")
    assert tagger.read(tiny_mp3) == (blank, None)
    assert tagger.read(tiny_m4a) == (blank, None)


# Errors


@pytest.mark.parametrize("suffix", [".mp3", ".m4a"])
def test_reading_a_missing_file_is_a_disk_error(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ScmpError) as caught:
        tagger.read(tmp_path / f"missing{suffix}")
    assert caught.value.kind == ErrorKind.disk
    # Full paths go in debug logs only, so the detail leaves the folder out.
    assert tmp_path.name not in str(caught.value)


# Genres


@pytest.mark.parametrize(
    "genre", ["140", "2", "174", "RX", "CR", "(17)Rock", "((Live))", "(Hip Hop)", "Rock/Pop"]
)
def test_genres_read_back_as_typed(audio: Path, genre: str) -> None:
    tagger.write(audio, dataclasses.replace(FULL_TAGS, genre=genre), None)
    assert tagger.read(audio)[0].genre == genre


# Values no tag should hold


@pytest.mark.parametrize(
    "change",
    [
        {"year": 0},
        {"year": 10_000},
        {"year": -1},
        {"track_no": 0},
        {"track_no": 1000},
        {"track_total": 0},
        {"track_total": 65_536},
        {"title": "Night\ud800Drive"},
        {"source_url": "https://soundcloud.com/a\ud800"},
        {"source_id": "soundcloud:tracks:\ud800"},
    ],
    ids=lambda change: next(iter(change)) + "=" + repr(next(iter(change.values()))),
)
def test_values_no_tag_should_hold_are_refused_before_writing(
    audio: Path, change: dict[str, object]
) -> None:
    before = audio.read_bytes()
    with pytest.raises(ScmpError) as caught:
        tagger.write(audio, dataclasses.replace(FULL_TAGS, **change), None)
    assert caught.value.kind == ErrorKind.internal
    assert audio.read_bytes() == before


def test_the_edges_of_the_allowed_ranges_are_written(audio: Path) -> None:
    for year, track_no, track_total in ((1, 1, 1), (9999, 999, 999)):
        written = dataclasses.replace(
            FULL_TAGS, year=year, track_no=track_no, track_total=track_total
        )
        tagger.write(audio, written, None)
        assert tagger.read(audio)[0] == written
