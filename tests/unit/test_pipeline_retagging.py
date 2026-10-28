"""Tests for re-tagging a file you already have: reading it, writing into it, never losing it."""

import io
import os
import shutil
import stat
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    PRIV,
    SYLT,
    TBPM,
    TCON,
    TDRC,
    TPE1,
    TXXX,
    USLT,
    Encoding,
    ID3v1SaveOptions,
)
from mutagen.id3 import delete as id3_delete
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from PIL import Image

from scmp3.core import pipeline, placing, tagger
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import ArtworkChoice, CropBox, HistoryEntry, Settings, Tags
from scmp3.core.pipeline import Prepared, Tools
from scmp3.core.pipeline.retagging import changed_fields
from tests.fixtures.local_files import (
    OLD_TAGS,
    close_handle,
    exclusive_handle,
    make_cover,
    tagged_copy,
)


class NoYdl:
    """Stands in for a YoutubeDL as a context manager, for covers that never go online."""

    def __enter__(self) -> "NoYdl":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def urlopen(self, url: str) -> Any:
        raise AssertionError("a re-tag never downloads")


def no_ydl(*args: Any, **kwargs: Any) -> NoYdl:
    return NoYdl()


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "data" / "history.db")


@pytest.fixture
def tools(history: History) -> Tools:
    return Tools(make_ydl=no_ydl, history=history)


@pytest.fixture
def song(tiny_mp3: Path, tmp_path: Path) -> Path:
    return tagged_copy(tiny_mp3, tmp_path / "old songs" / "old favorite.mp3", cover=make_cover())


def prepared_for(path: Path, tools: Tools, settings: Settings | None = None) -> Prepared:
    return pipeline.prepare(classify(str(path)), settings=settings or Settings(), tools=tools)


def new_tags(**fields: Any) -> Tags:
    base = {"title": "Sunday Mornings", "artist": "Teo Marr", "album": "Sunday Mornings"}
    return Tags(**{"album_artist": "Teo Marr", **base, **fields})


def test_prepare_reads_a_local_file_without_going_online(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    assert prepared.source.kind == "file"
    assert prepared.suggestion.base_title == "Sunday Mornings"
    assert prepared.duplicate is None
    assert prepared.info == {}


def test_a_local_file_is_one_prepare_takes(song: Path) -> None:
    assert pipeline.unsupported_reason(classify(str(song))) is None


def test_retag_writes_the_reviewed_tags_into_the_file(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    saved = pipeline.retag(prepared, new_tags(year=2020), ArtworkChoice(), Settings())
    assert saved.path == song
    tags, _cover = tagger.read(song)
    assert (tags.title, tags.artist, tags.album, tags.year) == (
        "Sunday Mornings",
        "Teo Marr",
        "Sunday Mornings",
        2020,
    )


def test_retag_keeps_the_link_comment_and_codes_the_editor_doesnt_show(
    song: Path, tools: Tools
) -> None:
    prepared = prepared_for(song, tools)
    pipeline.retag(
        prepared, new_tags(comment=str(song), source_id="x"), ArtworkChoice(), Settings()
    )
    tags, _cover = tagger.read(song)
    assert tags.source_url == OLD_TAGS.source_url
    assert tags.source_id == OLD_TAGS.source_id
    assert tags.comment == OLD_TAGS.comment
    assert (tags.isrc, tags.label) == (OLD_TAGS.isrc, OLD_TAGS.label)


def test_the_render_never_puts_the_files_path_in_its_tags(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    tags = pipeline.render_tags(prepared.source, prepared.suggestion, Settings())
    assert str(song) not in {tags.comment, tags.source_url, tags.source_id}


def png_bytes(width: int = 1200, height: int = 800) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 140, 90)).save(buffer, "PNG")
    return buffer.getvalue()


def add_mp3_extras(path: Path, picture: bytes) -> None:
    """Frames sc-mp3 doesn't manage, as other taggers leave them, and a PNG cover of any shape."""
    id3 = ID3(path)
    id3.add(USLT(encoding=Encoding.UTF16, lang="eng", desc="", text="la la la"))
    id3.add(SYLT(encoding=Encoding.UTF16, lang="eng", format=2, type=1, text=[("la", 1000)]))
    id3.add(TXXX(encoding=Encoding.UTF16, desc="REPLAYGAIN_TRACK_GAIN", text=["-6.50 dB"]))
    id3.add(TXXX(encoding=Encoding.UTF16, desc="MusicBrainz Track Id", text=["abc-123"]))
    id3.add(TBPM(encoding=Encoding.UTF16, text=["128"]))
    id3.add(COMM(encoding=Encoding.UTF16, lang="deu", desc="Notiz", text=["Hallo"]))
    id3.add(PRIV(owner="WM/MediaClassPrimaryID", data=b"\x01\x02\x03"))
    id3.delall("APIC")
    id3.add(APIC(encoding=Encoding.LATIN1, mime="image/png", type=3, desc="", data=picture))
    id3.update_to_v23()
    id3.save(path, v2_version=3)


def test_a_re_tag_keeps_every_frame_it_doesnt_manage(song: Path, tools: Tools) -> None:
    add_mp3_extras(song, png_bytes())
    pipeline.retag(prepared_for(song, tools), new_tags(genre="Jazz"), ArtworkChoice(), Settings())
    id3 = ID3(song)
    assert id3.getall("USLT")[0].text == "la la la"
    assert id3.getall("SYLT")[0].text == [("la", 1000)]
    assert id3["TXXX:REPLAYGAIN_TRACK_GAIN"].text == ["-6.50 dB"]
    assert id3["TXXX:MusicBrainz Track Id"].text == ["abc-123"]
    assert id3["TBPM"].text == ["128"]
    assert id3["COMM:Notiz:deu"].text == ["Hallo"]
    assert id3.getall("PRIV")[0].data == b"\x01\x02\x03"
    assert (id3["TIT2"].text, id3["TCON"].text) == (["Sunday Mornings"], ["Jazz"])


def test_an_untouched_cover_comes_back_byte_for_byte(song: Path, tools: Tools) -> None:
    picture = png_bytes()
    add_mp3_extras(song, picture)
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    (kept,) = ID3(song).getall("APIC")
    assert (kept.data, kept.mime) == (picture, "image/png")


def test_an_m4a_keeps_its_other_atoms_and_its_cover_byte_for_byte(
    tiny_m4a: Path, tools: Tools, tmp_path: Path
) -> None:
    path = tagged_copy(tiny_m4a, tmp_path / "Night Bus.m4a")
    picture = png_bytes()
    audio = MP4(path)
    assert audio.tags is not None
    audio.tags["©lyr"] = ["la la la"]
    audio.tags["tmpo"] = [128]
    audio.tags["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(b"-6.50 dB")]
    audio.tags["covr"] = [MP4Cover(picture, imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()
    pipeline.retag(
        prepared_for(path, tools), new_tags(title="Night Bus"), ArtworkChoice(), Settings()
    )
    atoms = MP4(path).tags
    assert atoms is not None
    assert (atoms["©nam"], atoms["©lyr"], atoms["tmpo"]) == (["Night Bus"], ["la la la"], [128])
    assert bytes(atoms["----:com.apple.iTunes:replaygain_track_gain"][0]) == b"-6.50 dB"
    assert bytes(atoms["covr"][0]) == picture


def test_only_the_fields_that_changed_are_rewritten(song: Path, tools: Tools) -> None:
    id3 = ID3(song)
    # A full date the editor shows as a year only.
    id3.setall("TDRC", [TDRC(encoding=Encoding.UTF16, text=["2019-05-03"])])
    id3.save(song, v2_version=4)
    current = tagger.read(song)[0]
    reviewed = replace(current, title="Sunday Mornings")
    assert changed_fields(reviewed, current) == ["title"]
    pipeline.retag(prepared_for(song, tools), reviewed, ArtworkChoice(), Settings())
    after = ID3(song, translate=False)
    assert after.version == (2, 4, 0)
    assert str(after["TDRC"].text[0]) == "2019-05-03"
    assert after["TIT2"].text == ["Sunday Mornings"]


def test_a_new_year_replaces_a_v2_3_files_date_frames(song: Path, tools: Tools) -> None:
    pipeline.retag(prepared_for(song, tools), new_tags(year=2021), ArtworkChoice(), Settings())
    after = ID3(song, translate=False)
    assert after.version == (2, 3, 0)
    assert after["TYER"].text == ["2021"]
    assert tagger.read(song)[0].year == 2021


def test_an_id3v1_tag_follows_the_new_title(song: Path, tools: Tools) -> None:
    ID3(song).save(song, v2_version=3, v1=ID3v1SaveOptions.CREATE)
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    with song.open("rb") as file:
        file.seek(-128, os.SEEK_END)
        v1 = file.read(128)
    assert v1[:3] == b"TAG"
    assert v1[3:33].rstrip(b"\0") == b"Sunday Mornings"


def test_a_crop_of_the_files_own_cover_is_processed(song: Path, tools: Tools) -> None:
    choice = ArtworkChoice("source", crop=CropBox(left=0.0, top=0.0, width=0.5, height=0.5))
    pipeline.retag(prepared_for(song, tools), new_tags(), choice, Settings())
    cover = tagger.read(song)[1]
    assert cover is not None
    with Image.open(io.BytesIO(cover)) as image:
        assert (image.format, image.size) == ("JPEG", (200, 200))


def test_a_new_cover_that_cant_be_read_leaves_the_old_one(
    song: Path, tools: Tools, tmp_path: Path
) -> None:
    before = tagger.read(song)[1]
    choice = ArtworkChoice("file", str(tmp_path / "gone.png"))
    saved = pipeline.retag(prepared_for(song, tools), new_tags(), choice, Settings())
    assert saved.cover_error is not None
    assert tagger.read(song)[1] == before
    assert tagger.read(song)[0].title == "Sunday Mornings"


def test_a_re_tag_only_changes_the_fields_the_editor_shows(song: Path, tools: Tools) -> None:
    with pytest.raises(ScmpError) as error_info:
        tagger.update(song, new_tags(), fields=["comment"], cover=None, replace_cover=False)
    assert error_info.value.kind == ErrorKind.internal


def test_a_new_cover_replaces_the_one_in_the_file(song: Path, tools: Tools, tmp_path: Path) -> None:
    picked = tmp_path / "new cover.png"
    Image.new("RGB", (320, 320), (200, 30, 30)).save(picked)
    choice = ArtworkChoice("file", str(picked))
    pipeline.retag(prepared_for(song, tools), new_tags(), choice, Settings())
    cover = tagger.read(song)[1]
    assert cover is not None
    with Image.open(io.BytesIO(cover)) as image:
        assert image.size == (320, 320)


def test_no_cover_takes_the_cover_out(song: Path, tools: Tools) -> None:
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice("none"), Settings())
    assert tagger.read(song)[1] is None


def test_a_re_tag_updates_the_history_of_a_file_sc_mp3_saved(
    song: Path, tools: Tools, history: History
) -> None:
    entry = HistoryEntry(
        source_id=OLD_TAGS.source_id,
        url_norm=OLD_TAGS.source_url,
        saved_path=song,
        title=OLD_TAGS.title,
        artist=OLD_TAGS.artist,
        album=OLD_TAGS.album,
        format="mp3",
    )
    history.add(entry)
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    (only,) = history.recent()
    assert (only.title, only.saved_path, only.source_id) == (
        "Sunday Mornings",
        song,
        OLD_TAGS.source_id,
    )


def test_a_re_tag_adds_a_history_entry_for_a_file_sc_mp3_didnt_save(
    song: Path, tools: Tools, history: History
) -> None:
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    (only,) = history.recent()
    assert (only.saved_path, only.title, only.format) == (song, "Sunday Mornings", "mp3")
    # The file's own link, so saving it from SoundCloud again warns that it's already here.
    assert history.find_by_url(OLD_TAGS.source_url or "") == only


def test_an_m4a_is_re_tagged_as_an_m4a(tiny_m4a: Path, tools: Tools, tmp_path: Path) -> None:
    path = tagged_copy(tiny_m4a, tmp_path / "Night Bus.m4a")
    pipeline.retag(
        prepared_for(path, tools), new_tags(title="Night Bus"), ArtworkChoice(), Settings()
    )
    assert tagger.read(path)[0].title == "Night Bus"


# Never losing the file


def leftovers(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir() if path.name.endswith(".tmp"))


def test_a_write_that_fails_halfway_leaves_the_file_untouched(
    song: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepared_for(song, tools)
    original = song.read_bytes()

    def write_half_then_fail(path: Path, *args: Any, **kwargs: Any) -> None:
        with path.open("r+b") as file:
            file.write(b"\0" * 4096)
        raise ScmpError(ErrorKind.convert_failed, "the tags couldn't be written")

    monkeypatch.setattr(tagger, "update", write_half_then_fail)
    with pytest.raises(ScmpError):
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert song.read_bytes() == original
    assert leftovers(song.parent) == []


def test_a_crash_while_writing_leaves_the_file_untouched(
    song: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepared_for(song, tools)
    original = song.read_bytes()

    def crash(path: Path, *args: Any, **kwargs: Any) -> None:
        path.write_bytes(b"half")
        raise RuntimeError("a bug")

    monkeypatch.setattr(tagger, "update", crash)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.kind == ErrorKind.internal
    assert song.read_bytes() == original
    assert leftovers(song.parent) == []


@pytest.fixture
def quick_lock_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    # The swap is tried again for about 2 seconds while a file is locked; tests needn't wait.
    monkeypatch.setattr(placing, "LOCK_RETRY_DELAYS", (0.0,))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace a read-only file")
@pytest.mark.usefixtures("quick_lock_retries")
def test_a_read_only_file_is_called_in_use_and_left_untouched(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    original = song.read_bytes()
    os.chmod(song, stat.S_IREAD)
    try:
        with pytest.raises(ScmpError) as error_info:
            pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
        assert error_info.value.reason == "file_in_use"
        assert song.read_bytes() == original
        assert leftovers(song.parent) == []
    finally:
        os.chmod(song, stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix replaces a read-only file in a writable folder"
)
def test_a_read_only_file_stays_read_only_with_its_new_tags(song: Path, tools: Tools) -> None:
    os.chmod(song, stat.S_IRUSR)
    pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    assert stat.S_IMODE(song.stat().st_mode) == stat.S_IRUSR
    assert tagger.read(song)[0].title == "Sunday Mornings"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks a file another program has open")
@pytest.mark.usefixtures("quick_lock_retries")
def test_a_file_open_in_another_program_is_called_in_use_and_left_untouched(
    song: Path, tools: Tools
) -> None:
    prepared = prepared_for(song, tools)
    original = song.read_bytes()
    with song.open("rb"), pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.reason == "file_in_use"
    assert song.read_bytes() == original
    assert leftovers(song.parent) == []


def test_a_file_that_went_before_the_save_is_called_missing(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    shutil.rmtree(song.parent)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert (error_info.value.kind, error_info.value.reason) == (ErrorKind.disk, "file_missing")


def test_a_folder_that_goes_while_it_writes_is_called_missing(
    song: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepared_for(song, tools)
    update = tagger.update

    def write_then_unplug(path: Path, *args: Any, **kwargs: Any) -> None:
        update(path, *args, **kwargs)
        # As a drive that's unplugged: the folder and both files go.
        shutil.rmtree(song.parent)

    monkeypatch.setattr(tagger, "update", write_then_unplug)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.reason == "file_missing"


def test_a_failed_re_tag_leaves_the_history_alone(
    song: Path, tools: Tools, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepared_for(song, tools)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ScmpError(ErrorKind.disk, "the disk is full")

    monkeypatch.setattr(tagger, "update", fail)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.where == "file"
    assert history.recent() == []


def test_a_broken_history_only_warns_after_a_re_tag(
    song: Path, tools: Tools, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> int:
        raise ScmpError(ErrorKind.disk, "history database error")

    monkeypatch.setattr(history, "update_tags", broken)
    saved = pipeline.retag(prepared_for(song, tools), new_tags(), ArtworkChoice(), Settings())
    assert saved.path == song
    assert tagger.read(song)[0].title == "Sunday Mornings"


# A v2.3 tag's frames with several values, which mutagen joins with "/" unless told otherwise


def raw_v23_frames(path: Path) -> dict[str, list[bytes]]:
    """The raw bytes of each frame of the file's ID3v2.3 tag, by frame id."""
    data = path.read_bytes()
    assert data[:3] == b"ID3" and data[3] == 3
    size = int.from_bytes(bytes(byte & 0x7F for byte in data[6:10]), "big")
    frames: dict[str, list[bytes]] = {}
    offset = 10
    while offset + 10 <= 10 + size and data[offset : offset + 4].strip(b"\0"):
        frame_id = data[offset : offset + 4].decode("latin-1")
        frame_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        frames.setdefault(frame_id, []).append(data[offset + 10 : offset + 10 + frame_size])
        offset += 10 + frame_size
    return frames


@pytest.fixture
def several_values(song: Path) -> Path:
    """The song with null-separated v2.3 artist, genre and TXXX frames, as other taggers write."""
    id3 = ID3(song)
    id3.setall("TPE1", [TPE1(encoding=Encoding.UTF16, text=["Teo Marr", "Kaia"])])
    id3.setall("TCON", [TCON(encoding=Encoding.UTF16, text=["Soul", "Jazz"])])
    id3.add(TXXX(encoding=Encoding.UTF16, desc="ARTISTS", text=["Teo Marr", "Kaia"]))
    id3.update_to_v23()
    id3.save(song, v2_version=3, v23_sep=None)
    return song


# The TXXX frames include the null-separated ARTISTS one.
MULTI_VALUE_FRAMES = ("TPE1", "TCON", "TXXX")


def test_a_new_title_leaves_a_v2_3_files_multi_value_frames_byte_for_byte(
    several_values: Path, tools: Tools
) -> None:
    before = raw_v23_frames(several_values)
    current = tagger.read(several_values)[0]
    reviewed = replace(current, title="Sunday Mornings")
    pipeline.retag(prepared_for(several_values, tools), reviewed, ArtworkChoice(), Settings())
    after = raw_v23_frames(several_values)
    assert {key: after[key] for key in MULTI_VALUE_FRAMES} == {
        key: before[key] for key in MULTI_VALUE_FRAMES
    }
    assert tagger.read(several_values)[0].title == "Sunday Mornings"


def test_a_save_with_no_changes_leaves_the_file_byte_for_byte(
    several_values: Path, tools: Tools
) -> None:
    before = several_values.read_bytes()
    modified = several_values.stat().st_mtime_ns
    current = tagger.read(several_values)[0]
    pipeline.retag(prepared_for(several_values, tools), current, ArtworkChoice(), Settings())
    assert several_values.read_bytes() == before
    assert several_values.stat().st_mtime_ns == modified
    assert leftovers(several_values.parent) == []


# Errors with their own advice, and a file another program changed meanwhile


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks a file another program has open")
def test_a_file_another_program_holds_is_called_locked_when_its_read(
    song: Path, tools: Tools
) -> None:
    handle = exclusive_handle(song)
    try:
        with pytest.raises(ScmpError) as error_info:
            prepared_for(song, tools)
    finally:
        close_handle(handle)
    assert (error_info.value.kind, error_info.value.reason) == (ErrorKind.disk, "file_locked")


def test_tags_that_cant_be_written_say_so_rather_than_blame_a_conversion(
    song: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepared_for(song, tools)
    before = song.read_bytes()

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ScmpError(ErrorKind.convert_failed, "couldn't update the MP3 tags: error")

    monkeypatch.setattr(tagger, "update", refuse)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.reason == "tag_write_failed"
    assert song.read_bytes() == before


def test_a_file_changed_elsewhere_since_it_was_read_isnt_overwritten(
    song: Path, tools: Tools
) -> None:
    prepared = prepared_for(song, tools)
    retitled = replace(tagger.read(song)[0], title="Retitled Elsewhere")
    tagger.update(song, retitled, fields=["title"], cover=None, replace_cover=False)
    with pytest.raises(ScmpError) as error_info:
        pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert error_info.value.reason == "file_changed"
    assert tagger.read(song)[0].title == "Retitled Elsewhere"


def test_a_source_kept_from_before_stamps_isnt_refused(song: Path, tools: Tools) -> None:
    prepared = prepared_for(song, tools)
    prepared.source = replace(prepared.source, file_stamp=None)
    pipeline.retag(prepared, new_tags(), ArtworkChoice(), Settings())
    assert tagger.read(song)[0].title == "Sunday Mornings"


# Files with old, odd or no tags


def untagged_copy(audio: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio, dest)
    id3_delete(dest)
    return dest


def v22_frame(frame_id: bytes, body: bytes) -> bytes:
    return frame_id + len(body).to_bytes(3, "big") + body


def synchsafe(size: int) -> bytes:
    return bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))


def test_a_v2_2_tag_moves_to_v2_3_with_its_lyrics_and_year(
    tiny_mp3: Path, tools: Tools, tmp_path: Path
) -> None:
    path = untagged_copy(tiny_mp3, tmp_path / "old.mp3")
    frames = (
        v22_frame(b"TT2", b"\x00Old Song (Official Audio)")
        + v22_frame(b"TP1", b"\x00Teo Marr")
        + v22_frame(b"TYE", b"\x002019")
        + v22_frame(b"ULT", b"\x00eng\x00la la la")
    )
    path.write_bytes(b"ID3\x02\x00\x00" + synchsafe(len(frames)) + frames + path.read_bytes())
    prepared = prepared_for(path, tools)
    assert prepared.suggestion.base_title == "Old Song"
    pipeline.retag(prepared, new_tags(title="Old Song", year=2019), ArtworkChoice(), Settings())
    after = ID3(path, translate=False)
    assert after.version == (2, 3, 0)
    assert after["TIT2"].text == ["Old Song"]
    assert after.getall("USLT")[0].text == "la la la"
    assert tagger.read(path)[0].year == 2019


def test_an_id3v1_only_file_gets_a_v2_3_tag_and_its_v1_follows(
    tiny_mp3: Path, tools: Tools, tmp_path: Path
) -> None:
    path = untagged_copy(tiny_mp3, tmp_path / "old.mp3")
    v1 = b"TAG" + b"Old Title".ljust(30, b"\0") + b"Teo Marr".ljust(30, b"\0")
    v1 += b"".ljust(30, b"\0") + b"2019" + b"".ljust(30, b"\0") + b"\xff"
    path.write_bytes(path.read_bytes() + v1)
    pipeline.retag(prepared_for(path, tools), new_tags(), ArtworkChoice(), Settings())
    assert ID3(path, translate=False).version == (2, 3, 0)
    assert tagger.read(path)[0].title == "Sunday Mornings"
    tail = path.read_bytes()[-128:]
    assert tail[:3] == b"TAG" and tail[3:33].rstrip(b"\0") == b"Sunday Mornings"


@pytest.mark.parametrize("fixture", ["tiny_mp3", "tiny_m4a"])
def test_an_untagged_file_gets_the_typed_tags(
    fixture: str, request: pytest.FixtureRequest, tools: Tools, tmp_path: Path
) -> None:
    audio: Path = request.getfixturevalue(fixture)
    path = tmp_path / f"untagged{audio.suffix}"
    shutil.copyfile(audio, path)
    if audio.suffix == ".mp3":
        id3_delete(path)
    pipeline.retag(prepared_for(path, tools), new_tags(), ArtworkChoice(), Settings())
    tags = tagger.read(path)[0]
    assert (tags.title, tags.artist) == ("Sunday Mornings", "Teo Marr")
