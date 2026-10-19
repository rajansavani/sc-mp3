"""Tests for the ffmpeg conversions and for checking files with mutagen.

The audio inputs are generated with ffmpeg here, and those tests skip when it's missing.
"""

import io
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp3 import MP3, BitrateMode
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from scmp3.core import convert
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError

SECONDS = 3
SINE = "sine=frequency=440:sample_rate=44100"
SILENCE = "anullsrc=r=44100:cl=stereo"

# The sources a download can give, and the ffmpeg options that make each one.
SOURCE_CODECS: dict[str, list[str]] = {
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "opus": ["-c:a", "libopus", "-b:a", "96k"],
}
# ffmpeg's mp3 muxer always names itself in TSSE. Nothing else may be in a converted file.
ENCODER_FRAMES = {"TSSE"}


def make_audio(
    ffmpeg: Path, dest: Path, codec_args: list[str], *, seconds: float = SECONDS, source: str = SINE
) -> Path:
    """Write `seconds` of stereo audio with a title and artist, encoded with `codec_args`."""
    command = [
        str(ffmpeg),
        *("-hide_banner", "-loglevel", "error", "-nostdin", "-y"),
        *("-f", "lavfi", "-i", source, "-t", str(seconds), "-ac", "2"),
        *("-metadata", "title=Source title", "-metadata", "artist=Source artist"),
        *codec_args,
        str(dest),
    ]
    subprocess.run(command, check=True)
    return dest


def png_bytes() -> bytes:
    """A small PNG to use as a cover."""
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, "PNG")
    return buffer.getvalue()


def add_cover(path: Path) -> None:
    """Embed a cover the way each format stores one, so ffmpeg sees it as a video stream."""
    if path.suffix == ".m4a":
        mp4 = MP4(path)
        mp4["covr"] = [MP4Cover(png_bytes(), imageformat=MP4Cover.FORMAT_PNG)]
        mp4.save()
    elif path.suffix == ".flac":
        flac = FLAC(path)
        picture = Picture()
        picture.type = 3  # front cover
        picture.mime = "image/png"
        picture.width = picture.height = 16
        picture.depth = 24
        picture.data = png_bytes()
        flac.add_picture(picture)
        flac.save()


@pytest.fixture(scope="module")
def sources(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One tagged source file per format, with a cover where the format holds one."""
    folder = tmp_path_factory.mktemp("sources")
    made = {}
    for name, codec_args in SOURCE_CODECS.items():
        path = make_audio(ffmpeg_exe, folder / f"source.{name}", codec_args)
        add_cover(path)
        made[name] = path
    return made


@pytest.fixture(scope="module")
def tagged_mp3(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 128 kbps MP3 with a title, an artist and a cover, like a legacy SoundCloud stream."""
    path = tmp_path_factory.mktemp("mp3") / "source.mp3"
    make_audio(ffmpeg_exe, path, ["-c:a", "libmp3lame", "-b:a", "128k"])
    mp3 = MP3(path)
    assert mp3.tags is not None
    mp3.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=png_bytes()))
    mp3.save()
    return path


@pytest.fixture(scope="module")
def long_source(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Half an hour of silent FLAC: small, but it takes ffmpeg seconds to convert."""
    path = tmp_path_factory.mktemp("long") / "long.flac"
    return make_audio(ffmpeg_exe, path, ["-c:a", "flac"], seconds=1800, source=SILENCE)


def frame_ids(path: Path) -> set[str]:
    """The ID3 frames in an MP3."""
    tags = MP3(path).tags
    return set() if tags is None else {key.split(":")[0] for key in tags}


@pytest.mark.parametrize("name", SOURCE_CODECS)
def test_every_source_becomes_a_320_kbps_cbr_mp3(
    name: str, sources: dict[str, Path], tmp_path: Path
) -> None:
    out = convert.to_mp3_320(sources[name], tmp_path / "out.mp3")
    info = MP3(out).info
    assert info.bitrate == 320_000
    assert info.bitrate_mode is BitrateMode.CBR
    assert info.length == pytest.approx(SECONDS, abs=0.1)


@pytest.mark.parametrize("name", SOURCE_CODECS)
def test_a_converted_mp3_carries_no_tags_or_cover_from_the_source(
    name: str, sources: dict[str, Path], tmp_path: Path
) -> None:
    out = convert.to_mp3_320(sources[name], tmp_path / "out.mp3")
    assert frame_ids(out) <= ENCODER_FRAMES


def test_mp3_v0_is_lame_vbr_without_tags(sources: dict[str, Path], tmp_path: Path) -> None:
    out = convert.to_mp3_v0(sources["flac"], tmp_path / "out.mp3")
    info = MP3(out).info
    assert info.bitrate_mode is BitrateMode.VBR
    assert info.length == pytest.approx(SECONDS, abs=0.1)
    assert frame_ids(out) <= ENCODER_FRAMES


def top_level_boxes(path: Path) -> list[str]:
    """The names of an MP4 file's top-level boxes, in order."""
    data = path.read_bytes()
    names: list[str] = []
    offset = 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        names.append(data[offset + 4 : offset + 8].decode("latin-1"))
        if size == 1:
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        if size == 0:
            break
        offset += size
    return names


def test_remux_m4a_copies_the_aac_without_tags_or_cover(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    src = sources["m4a"]
    out = convert.remux_m4a(src, tmp_path / "out.m4a")
    mp4 = MP4(out)
    assert mp4.info.codec == "mp4a.40.2"
    # Copied, not encoded again, so the bitrate stays the source's.
    assert convert.probe(out).bitrate == pytest.approx(convert.probe(src).bitrate, abs=2)
    # ffmpeg names itself in ©too. Nothing else may be in the file.
    assert set(mp4.tags or {}) <= {"©too"}
    boxes = top_level_boxes(out)
    assert boxes.index("moov") < boxes.index("mdat")


def test_copy_mp3_output_is_byte_identical(tagged_mp3: Path, tmp_path: Path) -> None:
    out = convert.copy_mp3(tagged_mp3, tmp_path / "out.mp3")
    assert out.read_bytes() == tagged_mp3.read_bytes()


def test_copy_mp3_of_a_missing_file_is_a_disk_error(tmp_path: Path) -> None:
    with pytest.raises(ScmpError) as error_info:
        convert.copy_mp3(tmp_path / "gone.mp3", tmp_path / "out.mp3")
    assert error_info.value.kind is ErrorKind.disk
    assert not (tmp_path / "out.mp3").exists()


def test_copy_mp3_onto_itself_fails_and_keeps_the_file(tagged_mp3: Path, tmp_path: Path) -> None:
    path = tmp_path / "source.mp3"
    shutil.copyfile(tagged_mp3, path)
    with pytest.raises(ScmpError) as error_info:
        convert.copy_mp3(path, path)
    assert error_info.value.kind is ErrorKind.internal
    assert error_info.value.detail == "can't copy source.mp3 onto itself"
    assert path.read_bytes() == tagged_mp3.read_bytes()


@pytest.mark.skipif(
    sys.platform != "win32", reason="only Windows always treats names that differ in case as one"
)
def test_copy_mp3_onto_its_own_name_in_capitals_keeps_the_file(
    tagged_mp3: Path, tmp_path: Path
) -> None:
    path = tmp_path / "source.mp3"
    shutil.copyfile(tagged_mp3, path)
    with pytest.raises(ScmpError) as error_info:
        convert.copy_mp3(path, tmp_path / "SOURCE.MP3")
    assert error_info.value.kind is ErrorKind.internal
    assert path.read_bytes() == tagged_mp3.read_bytes()


@pytest.mark.parametrize(
    ("conversion", "codec_args"),
    [
        (convert.to_mp3_320, ["-c:a", "libmp3lame", "-b:a", "320k"]),
        (convert.to_mp3_v0, ["-c:a", "libmp3lame", "-q:a", "0"]),
        (convert.remux_m4a, ["-c:a", "copy", "-movflags", "+faststart"]),
    ],
)
def test_conversions_run_the_commands_in_the_spec(
    conversion: Callable[..., Path],
    codec_args: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    commands: list[list[str]] = []

    def record(command: list[str], **kwargs: Any) -> None:
        commands.append(list(command))

    monkeypatch.setattr(convert.paths, "ffmpeg_exe", lambda: ffmpeg)
    monkeypatch.setattr(convert.runner, "run", record)
    src, dst = tmp_path / "source.wav", tmp_path / "out.mp3"
    conversion(src, dst)
    assert commands == [
        [
            str(ffmpeg),
            *("-nostdin", "-y", "-i", str(src)),
            *("-map", "0:a:0", "-vn", "-map_metadata", "-1"),
            *codec_args,
            str(dst),
        ]
    ]


def cancel_when_written(path: Path, cancel: threading.Event) -> threading.Thread:
    """Set `cancel` as soon as `path` has data in it, from a background thread."""

    def watch() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size > 0:
                cancel.set()
                return
            time.sleep(0.01)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def test_cancelling_mid_conversion_stops_ffmpeg_and_leaves_no_output(
    long_source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.mp3"
    cancel = threading.Event()
    watcher = cancel_when_written(out, cancel)
    with pytest.raises(Cancelled):
        convert.to_mp3_320(long_source, out, cancel=cancel)
    watcher.join(timeout=5)
    assert not out.exists()
    # A running ffmpeg would write the file again.
    time.sleep(0.5)
    assert not out.exists()


def test_a_conversion_past_its_timeout_is_stopped_and_leaves_no_output(
    long_source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.mp3"
    with pytest.raises(ScmpError) as error_info:
        convert.to_mp3_320(long_source, out, timeout=0.3)
    assert error_info.value.kind is ErrorKind.convert_failed
    assert error_info.value.detail == "ffmpeg didn't finish within 0.3 s"
    assert not out.exists()


def test_ffmpeg_failing_is_convert_failed_with_its_reason_and_no_folders(
    ffmpeg_exe: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_text("not audio")
    out = tmp_path / "out.mp3"
    with pytest.raises(ScmpError) as error_info:
        convert.to_mp3_320(bad, out)
    error = error_info.value
    assert error.kind is ErrorKind.convert_failed
    assert error.detail is not None
    assert "Invalid data found when processing input" in error.detail
    assert "bad.wav" in error.detail
    assert str(tmp_path) not in error.detail
    assert not out.exists()


def test_a_conversion_cancelled_before_it_starts_never_runs_ffmpeg(
    sources: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_start(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("ffmpeg was started")

    monkeypatch.setattr(convert.runner.subprocess, "Popen", never_start)
    out = tmp_path / "out.mp3"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        convert.to_mp3_320(sources["wav"], out, cancel=cancel)
    assert not out.exists()


class SetAfterFirstCheck(threading.Event):
    """Reads as not set on its first check and as set after that, like a cancel during a run."""

    def __init__(self) -> None:
        super().__init__()
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > 1


def test_a_cancel_during_a_remux_too_fast_to_poll_leaves_no_output(
    sources: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A long poll means the only cancel check after the start is the one after ffmpeg exits.
    monkeypatch.setattr(convert.runner, "POLL_INTERVAL", 30)
    out = tmp_path / "out.m4a"
    cancel = SetAfterFirstCheck()
    with pytest.raises(Cancelled):
        convert.remux_m4a(sources["m4a"], out, cancel=cancel)
    assert cancel.checks == 2
    assert not out.exists()


@pytest.mark.parametrize(
    ("name", "codec", "sample_rate"),
    [("wav", "pcm", 44100), ("flac", "flac", 44100), ("m4a", "aac", 44100), ("opus", "opus", None)],
)
def test_probe_reads_duration_bitrate_codec_and_sample_rate(
    name: str, codec: str, sample_rate: int | None, sources: dict[str, Path]
) -> None:
    info = convert.probe(sources[name])
    assert info.codec == codec
    assert info.sample_rate == sample_rate
    assert info.duration == pytest.approx(SECONDS, abs=0.1)
    assert info.bitrate is not None and info.bitrate > 0


def test_probe_reads_an_mp3s_bitrate_in_kbps(tagged_mp3: Path) -> None:
    info = convert.probe(tagged_mp3)
    assert info.codec == "mp3"
    assert info.bitrate == 128
    assert info.sample_rate == 44100


def test_probe_reads_pcm_bitrate_in_kbps(sources: dict[str, Path]) -> None:
    # 44100 samples a second, of 16 bits, in 2 channels.
    assert convert.probe(sources["wav"]).bitrate == 1411


@pytest.mark.parametrize(
    ("name", "content", "detail"),
    [
        ("broken.mp3", b"not audio", "mutagen can't read broken.mp3"),
        ("notes.txt", b"not audio", "notes.txt isn't a type of audio mutagen knows"),
    ],
)
def test_probe_of_a_file_mutagen_cant_read_is_convert_failed(
    name: str, content: bytes, detail: str, tmp_path: Path
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(ScmpError) as error_info:
        convert.probe(path)
    assert error_info.value.kind is ErrorKind.convert_failed
    assert error_info.value.detail == detail


@pytest.fixture(scope="module")
def thirty_seconds(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 30-second MP3, the length of a SoundCloud preview."""
    path = tmp_path_factory.mktemp("preview") / "preview.mp3"
    return make_audio(ffmpeg_exe, path, ["-c:a", "libmp3lame", "-b:a", "64k"], seconds=30)


@pytest.fixture(scope="module")
def three_seconds(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A three-second 320 kbps MP3."""
    path = tmp_path_factory.mktemp("short") / "short.mp3"
    return make_audio(ffmpeg_exe, path, ["-c:a", "libmp3lame", "-b:a", "320k"])


def validate_error(path: Path, expected: float | None) -> ScmpError:
    """The ScmpError that validate raises."""
    with pytest.raises(ScmpError) as error_info:
        convert.validate(path, expected)
    return error_info.value


def test_validate_rejects_a_30_second_file_when_180_seconds_was_expected(
    thirty_seconds: Path,
) -> None:
    error = validate_error(thirty_seconds, 180)
    assert error.kind is ErrorKind.preview_only
    assert error.detail == "preview.mp3 is 30 s, a preview of a 180 s track"


def test_validate_accepts_a_track_that_really_is_about_30_seconds(thirty_seconds: Path) -> None:
    assert convert.validate(thirty_seconds, 31).duration == pytest.approx(30, abs=0.1)


def test_validate_rejects_a_zero_byte_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    error = validate_error(empty, 180)
    assert error.kind is ErrorKind.convert_failed
    assert error.detail == "empty.mp3 is empty"


def test_validate_rejects_a_missing_file(tmp_path: Path) -> None:
    error = validate_error(tmp_path / "gone.mp3", 180)
    assert error.kind is ErrorKind.convert_failed
    assert error.detail == "gone.mp3 is missing"


def test_validate_rejects_a_file_mutagen_cant_open(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"\xff" * 64)
    assert validate_error(broken, 180).kind is ErrorKind.convert_failed


@pytest.mark.parametrize("expected", [SECONDS - 1.9, SECONDS + 1.9])
def test_validate_accepts_a_length_within_two_seconds(three_seconds: Path, expected: float) -> None:
    info = convert.validate(three_seconds, expected)
    assert info.codec == "mp3"
    assert info.bitrate == 320


@pytest.mark.parametrize("expected", [SECONDS + 2.5, 60.0])
def test_validate_rejects_a_length_more_than_two_seconds_off(
    three_seconds: Path, expected: float
) -> None:
    error = validate_error(three_seconds, expected)
    assert error.kind is ErrorKind.convert_failed
    assert error.detail is not None
    assert error.detail.startswith("short.mp3 is 3.0 s, but the track is")


@pytest.fixture(scope="module")
def half_second(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A half-second MP3, too short to be a track."""
    path = tmp_path_factory.mktemp("blip") / "blip.mp3"
    return make_audio(ffmpeg_exe, path, ["-c:a", "libmp3lame", "-b:a", "320k"], seconds=0.5)


@pytest.mark.parametrize("expected", [None, 0, 0.5])
def test_validate_rejects_a_file_under_a_second_whatever_was_expected(
    half_second: Path, expected: float | None
) -> None:
    error = validate_error(half_second, expected)
    assert error.kind is ErrorKind.convert_failed
    assert error.detail is not None
    assert error.detail.startswith("blip.mp3 is 0.")
    assert error.detail.endswith(" s, too short to be a track")


@pytest.mark.parametrize("expected", [None, 0])
def test_validate_without_a_known_length_checks_only_the_file(
    three_seconds: Path, expected: float | None
) -> None:
    assert convert.validate(three_seconds, expected).duration == pytest.approx(SECONDS, abs=0.1)
