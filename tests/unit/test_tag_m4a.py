"""Tests for M4A tags: the MP4 atoms, and M4As that other tools tagged."""

import dataclasses
import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from mutagen.mp4 import MP4, AtomDataType, MP4Cover, MP4FreeForm
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

M4A_ATOM_KEYS = {
    "©nam",
    "©ART",
    "aART",
    "©alb",
    "©day",
    "©gen",
    "trkn",
    "covr",
    "©cmt",
    "----:com.scmp3:source_url",
    "----:com.scmp3:source_id",
    "----:com.apple.iTunes:ISRC",
    "----:com.apple.iTunes:LABEL",
}

GAPLESS_INFO = "----:com.apple.iTunes:iTunSMPB"


def make_cover(color: tuple[int, int, int] = (255, 0, 0), side: int = 600) -> Cover:
    """A real cover, made by artwork.process from a generated PNG."""
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), color).save(buffer, "PNG")
    return artwork.process(buffer.getvalue())


def atoms_in(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Each MP4 atom between `start` and `end`: its name, and where its body starts and ends."""
    position = start
    while position + 8 <= end:
        size = int.from_bytes(data[position : position + 4], "big")
        if size < 8:
            return
        yield data[position + 4 : position + 8], position + 8, position + size
        position += size


def ilst_children(path: Path) -> list[bytes]:
    """The names of the atoms in the file's ilst, read from its bytes rather than by mutagen."""
    data = path.read_bytes()
    start, end = 0, len(data)
    for name in (b"moov", b"udta", b"meta", b"ilst"):
        start, end = next((s, e) for n, s, e in atoms_in(data, start, end) if n == name)
        if name == b"meta":
            # meta holds a version and flags before its children.
            start += 4
    return [name for name, _, _ in atoms_in(data, start, end)]


@pytest.fixture
def m4a(tiny_m4a: Path, tmp_path: Path) -> Path:
    """A copy of the silent M4A that a test can change."""
    return Path(shutil.copy(tiny_m4a, tmp_path / "track.m4a"))


@pytest.fixture(scope="module")
def cover() -> Cover:
    return make_cover()


# The exact frames and atoms


def test_m4a_atoms_are_exactly_the_spec_list(m4a: Path, cover: Cover) -> None:
    tagger.write(m4a, FULL_TAGS, cover)
    atoms = MP4(m4a).tags
    assert set(atoms.keys()) == M4A_ATOM_KEYS
    assert atoms["trkn"] == [(3, 12)]
    assert atoms["©day"] == ["2024"]
    assert atoms["covr"][0].imageformat == MP4Cover.FORMAT_JPEG
    assert bytes(atoms["----:com.scmp3:source_id"][0]) == b"soundcloud:tracks:123456"


# Re-tagging


def test_retagging_an_m4a_leaves_exactly_one_of_each_atom(m4a: Path, cover: Cover) -> None:
    tagger.write(m4a, FULL_TAGS, cover)
    second = dataclasses.replace(FULL_TAGS, title="Second Title", year=2025, track_no=4)
    tagger.write(m4a, second, make_cover((0, 0, 255)))
    atoms = MP4(m4a).tags
    assert set(atoms.keys()) == M4A_ATOM_KEYS
    assert all(len(values) == 1 for values in atoms.values())
    assert tagger.read(m4a)[0] == second


def test_writing_an_m4a_removes_other_taggers_atoms_but_keeps_gapless_info(
    m4a: Path, cover: Cover
) -> None:
    gapless = MP4FreeForm(b" 00000000 00000840 000001C0")
    audio = MP4(m4a)
    audio.tags["©wrt"] = ["A Composer"]
    audio.tags["----:com.apple.iTunes:iTunNORM"] = [MP4FreeForm(b" 000001A0")]
    audio.tags[GAPLESS_INFO] = [gapless]
    audio.tags["©nam"] = ["Old Title", "Second Value"]
    audio.save()

    tagger.write(m4a, FULL_TAGS, cover)
    atoms = MP4(m4a).tags
    assert set(atoms.keys()) == M4A_ATOM_KEYS | {GAPLESS_INFO}
    assert bytes(atoms[GAPLESS_INFO][0]) == bytes(gapless)
    assert b"\xa9wrt" not in ilst_children(m4a)


def test_writing_an_m4a_drops_atoms_mutagen_couldnt_parse(m4a: Path) -> None:
    audio = MP4(m4a)
    audio.tags["©alb"] = ["Old Album"]
    audio.save()
    # Marking the album's data as UTF-16, which mutagen can't parse, so it keeps the raw atom.
    data = bytearray(m4a.read_bytes())
    data[data.find(b"\xa9alb") + 15] = 2
    m4a.write_bytes(bytes(data))
    assert "©alb" not in MP4(m4a).tags
    assert b"\xa9alb" in ilst_children(m4a)

    tagger.write(m4a, Tags(title="T", artist="A", album="", album_artist=""), None)
    assert b"\xa9alb" not in ilst_children(m4a)
    assert b"Old Album" not in m4a.read_bytes()


def test_reading_an_m4a_takes_the_genre_from_a_numeric_gnre_atom(m4a: Path) -> None:
    audio = MP4(m4a)
    audio.tags["©grp"] = ["XX"]
    audio.save()
    # Turning the two-byte grouping atom into a gnre atom for ID3v1 genre 17 plus 1, "Rock".
    data = bytearray(m4a.read_bytes())
    at = data.find(b"\xa9grp")
    data[at : at + 4] = b"gnre"
    data[at + 15] = 0
    data[at + 20 : at + 22] = b"\x00\x12"
    m4a.write_bytes(bytes(data))
    assert tagger.read(m4a)[0].genre == "Rock"

    tagger.write(m4a, FULL_TAGS, None)
    children = ilst_children(m4a)
    assert b"gnre" not in children
    assert b"\xa9gen" in children


def test_m4a_freeform_atoms_hold_utf8_text(m4a: Path) -> None:
    tagger.write(m4a, FULL_TAGS, None)
    atoms = MP4(m4a).tags
    for key in ("source_url", "source_id", "ISRC", "LABEL"):
        [value] = next(v for k, v in atoms.items() if k.endswith(f":{key}"))
        assert value.dataformat == AtomDataType.UTF8
    assert bytes(atoms["----:com.apple.iTunes:ISRC"][0]) == b"GBUM71029604"
    assert bytes(atoms["----:com.apple.iTunes:LABEL"][0]) == b"Kaia's Records"
    assert bytes(atoms["----:com.scmp3:source_url"][0]) == URL.encode()


# Reading files sc-mp3 didn't write


def test_reading_an_m4a_year_from_a_full_date(m4a: Path) -> None:
    audio = MP4(m4a)
    audio.tags["©day"] = ["2019-06-14T07:00:00Z"]
    audio.save()
    assert tagger.read(m4a)[0].year == 2019


# Errors


def test_writing_to_a_folder_named_like_an_m4a_is_a_disk_error(tmp_path: Path) -> None:
    folder = tmp_path / "folder.m4a"
    folder.mkdir()
    with pytest.raises(ScmpError) as caught:
        tagger.write(folder, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.disk


def test_an_m4a_that_isnt_one_fails_to_convert(tmp_path: Path) -> None:
    path = tmp_path / "broken.m4a"
    path.write_bytes(b"this is not an MP4 file" * 100)
    with pytest.raises(ScmpError) as write_error:
        tagger.write(path, FULL_TAGS, None)
    assert write_error.value.kind == ErrorKind.convert_failed
    with pytest.raises(ScmpError) as read_error:
        tagger.read(path)
    assert read_error.value.kind == ErrorKind.convert_failed
    assert tmp_path.name not in str(write_error.value)
    assert tmp_path.name not in str(read_error.value)


def test_mutagens_bare_value_error_on_a_damaged_m4a_fails_to_convert(
    m4a: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fuzzing found damaged M4A files where mutagen's save raises a plain ValueError.
    def damaged_save(self: MP4, *args: object, **kwargs: object) -> None:
        raise ValueError

    monkeypatch.setattr(MP4, "save", damaged_save)
    with pytest.raises(ScmpError) as caught:
        tagger.write(m4a, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.convert_failed
