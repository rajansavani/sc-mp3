"""Tests for MP3 tags: the ID3v2.3 frames, and MP3s that other tools tagged."""

import dataclasses
import io
import shutil
from collections import Counter
from pathlib import Path

import pytest
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    PRIV,
    TCOM,
    TCON,
    TIT2,
    TXXX,
    Encoding,
    ID3v1SaveOptions,
    PictureType,
)
from mutagen.mp4 import MP4
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

# The frames TECH_SPEC §10 lists, as they're keyed in the saved v2.3 tag.
MP3_FRAME_KEYS = {
    "TIT2",
    "TPE1",
    "TPE2",
    "TALB",
    "TYER",
    "TCON",
    "TRCK",
    "APIC:",
    "COMM::eng",
    "WOAS",
    "TSRC",
    "TPUB",
    "TXXX:SCMP3_SOURCE_ID",
}


def make_cover(color: tuple[int, int, int] = (255, 0, 0), side: int = 600) -> Cover:
    """A real cover, made by artwork.process from a generated PNG."""
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), color).save(buffer, "PNG")
    return artwork.process(buffer.getvalue())


def id3_frame_keys(path: Path) -> set[str]:
    """The frame keys exactly as saved, without mutagen's v2.4 translation."""
    return set(ID3(path, translate=False).keys())


def synchsafe(size: int) -> bytes:
    """An ID3v2 size, stored as 7 bits a byte."""
    return bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])


def id3v2_tag(version: int, frames: dict[str, str], *, footer: bool = False) -> bytes:
    """A small ID3v2 tag built by hand, since mutagen writes neither v2.2 nor footers."""
    name_size = 3 if version == 2 else 4
    body = b""
    for name, text in frames.items():
        payload = b"\x00" + text.encode("latin-1")
        assert len(name) == name_size
        if version == 2:
            body += name.encode() + len(payload).to_bytes(3, "big") + payload
        else:
            body += name.encode() + synchsafe(len(payload)) + b"\x00\x00" + payload
    flags = 0x10 if footer else 0
    header = b"ID3" + bytes([version, 0, flags]) + synchsafe(len(body))
    tail = b"3DI" + bytes([version, 0, flags]) + synchsafe(len(body)) if footer else b""
    return header + body + tail


def audio_only(mp3: Path) -> bytes:
    """The MP3's bytes after its ID3v2 tag."""
    return mp3.read_bytes()[ID3(mp3).size :]


@pytest.fixture
def mp3(tiny_mp3: Path, tmp_path: Path) -> Path:
    """A copy of the silent MP3 that a test can change."""
    return Path(shutil.copy(tiny_mp3, tmp_path / "track.mp3"))


@pytest.fixture(scope="module")
def cover() -> Cover:
    return make_cover()


# The exact frames and atoms


def test_mp3_frames_are_exactly_the_spec_list(mp3: Path, cover: Cover) -> None:
    tagger.write(mp3, FULL_TAGS, cover)
    assert id3_frame_keys(mp3) == MP3_FRAME_KEYS
    id3 = ID3(mp3, translate=False)
    assert id3["TYER"].text == ["2024"]
    assert id3["TRCK"].text == ["3/12"]
    assert id3["WOAS"].url == URL
    picture = id3["APIC:"]
    assert (picture.type, picture.mime, picture.desc) == (PictureType.COVER_FRONT, "image/jpeg", "")
    comment = id3["COMM::eng"]
    assert (comment.lang, comment.desc, comment.text) == ("eng", "", [URL])


def test_mp3_tags_are_id3v2_3_with_no_id3v1_tag(mp3: Path) -> None:
    tagger.write(mp3, FULL_TAGS, None)
    assert ID3(mp3).version == (2, 3, 0)
    data = mp3.read_bytes()
    assert data[:4] == b"ID3\x03"
    assert data[-128:-125] != b"TAG"


def test_mp3_text_is_utf16_since_v2_3_has_no_utf8(mp3: Path) -> None:
    tagger.write(mp3, FULL_TAGS, None)
    assert ID3(mp3, translate=False)["TIT2"].encoding == Encoding.UTF16


def test_the_mp3_year_is_saved_as_the_v2_3_tyer_frame(mp3: Path) -> None:
    tagger.write(mp3, FULL_TAGS, None)
    keys = id3_frame_keys(mp3)
    assert "TYER" in keys
    assert "TDRC" not in keys


def test_a_link_with_non_ascii_characters_is_percent_encoded_for_woas(mp3: Path) -> None:
    written = dataclasses.replace(
        FULL_TAGS, source_url="https://soundcloud.com/kaïa/mïdnight drive"
    )
    tagger.write(mp3, written, None)
    tags, _ = tagger.read(mp3)
    assert tags.source_url == "https://soundcloud.com/ka%C3%AFa/m%C3%AFdnight%20drive"


# Re-tagging


def test_retagging_an_mp3_leaves_exactly_one_of_each_frame(mp3: Path, cover: Cover) -> None:
    tagger.write(mp3, FULL_TAGS, cover)
    second = dataclasses.replace(FULL_TAGS, title="Second Title", year=2025, track_no=4)
    tagger.write(mp3, second, make_cover((0, 0, 255)))
    counts = Counter(frame.FrameID for frame in ID3(mp3, translate=False).values())
    assert set(counts.values()) == {1}
    assert sum(counts.values()) == len(MP3_FRAME_KEYS)
    assert tagger.read(mp3)[0] == second


def test_writing_an_mp3_removes_other_taggers_frames_and_id3v1(mp3: Path, cover: Cover) -> None:
    old = ID3()
    old.add(TIT2(encoding=Encoding.UTF8, text=["Old Title"]))
    old.add(TCOM(encoding=Encoding.UTF8, text=["A Composer"]))
    old.add(TXXX(encoding=Encoding.UTF8, desc="replaygain_track_gain", text=["-6 dB"]))
    old.add(PRIV(owner="WM/MediaClassPrimaryID", data=b"\x00" * 16))
    old.add(COMM(encoding=Encoding.UTF8, lang="eng", desc="iTunNORM", text=["0000 0000"]))
    old.add(APIC(encoding=0, mime="image/png", type=PictureType.COVER_BACK, desc="b", data=b"x"))
    old.add(APIC(encoding=0, mime="image/png", type=PictureType.COVER_FRONT, desc="f", data=b"y"))
    old.save(mp3, v2_version=4, v1=ID3v1SaveOptions.CREATE)
    assert mp3.read_bytes()[-128:-125] == b"TAG"

    tagger.write(mp3, FULL_TAGS, cover)
    assert id3_frame_keys(mp3) == MP3_FRAME_KEYS
    assert mp3.read_bytes()[-128:-125] != b"TAG"
    assert tagger.read(mp3) == (FULL_TAGS, cover.jpeg)


# Reading files sc-mp3 didn't write


def test_reading_an_mp3_without_tags_gives_blank_tags(mp3: Path) -> None:
    ID3(mp3).delete(mp3)
    assert tagger.read(mp3) == (Tags(title="", artist="", album="", album_artist=""), None)


def test_reading_prefers_the_front_cover_and_the_plain_comment(mp3: Path) -> None:
    old = ID3()
    old.add(APIC(encoding=0, mime="image/png", type=PictureType.COVER_BACK, desc="b", data=b"back"))
    old.add(
        APIC(encoding=0, mime="image/png", type=PictureType.COVER_FRONT, desc="", data=b"front")
    )
    old.add(COMM(encoding=Encoding.UTF8, lang="eng", desc="iTunNORM", text=["0000 0000"]))
    old.add(COMM(encoding=Encoding.UTF8, lang="xxx", desc="", text=["A real comment"]))
    old.save(mp3, v2_version=4)
    tags, cover_bytes = tagger.read(mp3)
    assert cover_bytes == b"front"
    assert tags.comment == "A real comment"


def test_reading_takes_any_picture_when_none_is_the_front_cover(mp3: Path) -> None:
    old = ID3()
    old.add(APIC(encoding=0, mime="image/png", type=PictureType.OTHER, desc="", data=b"other"))
    old.save(mp3, v2_version=4)
    assert tagger.read(mp3)[1] == b"other"


def test_reading_turns_numeric_id3v1_genres_into_names(mp3: Path) -> None:
    old = ID3()
    old.add(TCON(encoding=Encoding.UTF8, text=["(17)"]))
    old.save(mp3, v2_version=3)
    assert tagger.read(mp3)[0].genre == "Rock"


def test_reading_joins_multiple_values_into_one_string(mp3: Path) -> None:
    old = ID3()
    old.add(TIT2(encoding=Encoding.UTF8, text=["Part One", "Part Two"]))
    old.save(mp3, v2_version=4)
    assert tagger.read(mp3)[0].title == "Part One, Part Two"


# Errors


def test_writing_to_a_missing_mp3_is_a_disk_error_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "missing.mp3"
    with pytest.raises(ScmpError) as caught:
        tagger.write(path, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.disk
    assert not path.exists()


def test_an_mp3_whose_tag_claims_to_run_past_the_end_fails_untouched(mp3: Path) -> None:
    tagger.write(mp3, FULL_TAGS, None)
    damaged = bytearray(mp3.read_bytes())
    # The ID3 header's size field, set to about 256 MB.
    damaged[6:10] = b"\x7f\x7f\x7f\x7f"
    mp3.write_bytes(bytes(damaged))
    with pytest.raises(ScmpError) as caught:
        tagger.write(mp3, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.convert_failed
    assert mp3.read_bytes() == bytes(damaged)


def test_a_truncated_mp3_tag_fails_to_convert_rather_than_blaming_the_disk(
    mp3: Path, cover: Cover
) -> None:
    tagger.write(mp3, FULL_TAGS, cover)
    mp3.write_bytes(mp3.read_bytes()[:3000])
    with pytest.raises(ScmpError) as caught:
        tagger.read(mp3)
    assert caught.value.kind == ErrorKind.convert_failed


# Genres


def test_a_number_as_the_genre_is_stored_as_typed(mp3: Path) -> None:
    tagger.write(mp3, dataclasses.replace(FULL_TAGS, genre="140"), None)
    assert ID3(mp3, translate=False)["TCON"].text == ["140"]


@pytest.mark.parametrize(
    ("stored", "genre"),
    [("(17)", "Rock"), ("(17)Hard Rock", "Hard Rock"), ("(RX)", "Remix"), ("140", "140")],
)
def test_reading_turns_only_bracketed_id3v1_references_into_names(
    mp3: Path, stored: str, genre: str
) -> None:
    old = ID3()
    old.add(TCON(encoding=Encoding.UTF8, text=[stored]))
    old.save(mp3, v2_version=3)
    assert tagger.read(mp3)[0].genre == genre


def test_reading_an_id3v1_only_file_names_its_genre_number(mp3: Path) -> None:
    old = ID3()
    old.add(TIT2(encoding=Encoding.LATIN1, text=["Old Title"]))
    old.add(TCON(encoding=Encoding.LATIN1, text=["Rock"]))
    old.save(mp3, v1=ID3v1SaveOptions.CREATE)
    ID3(mp3).delete(mp3, delete_v1=False, delete_v2=True)
    assert ID3(mp3).version < (2, 0, 0)
    tags, _ = tagger.read(mp3)
    assert (tags.title, tags.genre) == ("Old Title", "Rock")


def test_reading_an_id3v2_2_tag(mp3: Path) -> None:
    tag = id3v2_tag(2, {"TT2": "Old Title", "TP1": "Old Artist", "TCO": "(17)", "TYE": "1999"})
    mp3.write_bytes(tag + audio_only(mp3))
    tags, _ = tagger.read(mp3)
    assert (tags.title, tags.artist, tags.genre, tags.year) == (
        "Old Title",
        "Old Artist",
        "Rock",
        1999,
    )


# What else an MP3 can hold


@pytest.mark.parametrize("contents", ["an M4A", "a PNG", "nothing"], ids=["m4a", "png", "empty"])
def test_a_file_without_mpeg_audio_isnt_tagged_as_an_mp3(
    tiny_m4a: Path, tmp_path: Path, contents: str
) -> None:
    path = tmp_path / "not-audio.mp3"
    if contents == "an M4A":
        shutil.copy(tiny_m4a, path)
    elif contents == "a PNG":
        Image.new("RGB", (8, 8)).save(path, "PNG")
    else:
        path.write_bytes(b"")
    before = path.read_bytes()
    with pytest.raises(ScmpError) as caught:
        tagger.write(path, FULL_TAGS, None)
    assert caught.value.kind == ErrorKind.convert_failed
    assert path.read_bytes() == before


def test_an_m4a_temp_copy_named_as_an_mp3_by_fmt_is_refused(tiny_m4a: Path, tmp_path: Path) -> None:
    temp = Path(shutil.copy(tiny_m4a, tmp_path / ".track.retag.tmp"))
    with pytest.raises(ScmpError) as caught:
        tagger.write(temp, FULL_TAGS, None, fmt="mp3")
    assert caught.value.kind == ErrorKind.convert_failed
    assert MP4(temp).info.length > 0


def test_an_old_v2_4_footer_is_removed_with_its_tag(mp3: Path) -> None:
    audio = audio_only(mp3)
    mp3.write_bytes(id3v2_tag(4, {"TIT2": "Old Title"}, footer=True) + audio)
    tagger.write(mp3, FULL_TAGS, None)
    assert audio_only(mp3) == audio


def test_a_second_tag_stacked_after_the_first_is_removed(mp3: Path) -> None:
    audio = audio_only(mp3)
    first = id3v2_tag(3, {"TIT2": "First"})
    second = id3v2_tag(4, {"TIT2": "Second"}, footer=True)
    mp3.write_bytes(first + second + id3v2_tag(3, {"TIT2": "Third"}) + audio)
    tagger.write(mp3, FULL_TAGS, None)
    assert audio_only(mp3) == audio
    assert b"Second" not in mp3.read_bytes()
    assert tagger.read(mp3)[0] == FULL_TAGS


def test_a_bogus_tag_header_after_the_first_is_left_alone(mp3: Path) -> None:
    audio = audio_only(mp3)
    # It claims 10 MB, which runs past the end, so it isn't a tag and nothing after it goes.
    bogus = b"ID3\x03\x00\x00" + synchsafe(10_000_000)
    mp3.write_bytes(id3v2_tag(4, {"TIT2": "Old"}, footer=True) + bogus + audio)
    tagger.write(mp3, FULL_TAGS, None)
    assert audio_only(mp3) == bogus + audio
