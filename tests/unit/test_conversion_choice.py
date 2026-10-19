"""Tests for picking a conversion, and for the bitrate check on lossy originals."""

from pathlib import Path

import pytest

from scmp3.core import conversion_choice, convert
from scmp3.core.conversion_choice import (
    COPY_MP3,
    MP3_320,
    REMUX_M4A,
    Conversion,
    choose_conversion,
    original_holds_up,
)
from scmp3.core.convert import AudioInfo
from scmp3.core.models import AudioFormat, FormatOption

STREAM_160 = FormatOption("hls_aac_160k", "m4a", codec="aac", bitrate=160.0)


@pytest.mark.parametrize(
    ("audio_format", "codec", "expected"),
    [
        ("mp3_320", "aac", MP3_320),
        ("mp3_320", "mp3", COPY_MP3),
        ("mp3_320", "pcm", MP3_320),
        ("mp3_v0", "mp3", COPY_MP3),
        ("m4a", "aac", REMUX_M4A),
        ("m4a", "mp3", COPY_MP3),
        # Only AAC stays in an M4A.
        ("m4a", "flac", MP3_320),
        ("m4a", None, MP3_320),
    ],
)
def test_the_codec_and_the_audio_format_pick_the_conversion(
    audio_format: AudioFormat, codec: str | None, expected: Conversion
) -> None:
    assert choose_conversion(audio_format, codec) == expected


def test_mp3_v0_encodes_anything_but_mp3_with_v0() -> None:
    chosen = choose_conversion("mp3_v0", "aac")
    assert (chosen.fmt, chosen.run) == ("mp3", convert.to_mp3_v0)


def test_copying_an_mp3_keeps_it_byte_for_byte(tiny_mp3: Path, tmp_path: Path) -> None:
    copied = COPY_MP3.run(tiny_mp3, tmp_path / "audio.mp3")
    assert copied.read_bytes() == tiny_mp3.read_bytes()


def test_an_original_at_least_as_good_as_the_stream_holds_up(tiny_mp3: Path) -> None:
    # tiny_mp3 is 320 kbps.
    assert original_holds_up(tiny_mp3, STREAM_160)


def test_an_original_below_the_stream_doesnt_hold_up(tiny_mp3: Path) -> None:
    stream_400 = FormatOption("hls_aac_400k", "m4a", codec="aac", bitrate=400.0)
    assert not original_holds_up(tiny_mp3, stream_400)


def test_a_stream_without_a_bitrate_never_beats_the_original(tiny_mp3: Path) -> None:
    assert original_holds_up(tiny_mp3, FormatOption("hls_aac", "m4a", codec="aac"))


def test_an_original_without_a_known_bitrate_gives_way_to_the_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = AudioInfo(duration=200.0, bitrate=None, codec="vorbis", sample_rate=44100)
    monkeypatch.setattr(conversion_choice.convert, "probe", lambda path: unknown)
    assert not original_holds_up(tmp_path / "source.ogg", STREAM_160)


def test_an_unreadable_original_gives_way_to_the_stream(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    junk = tmp_path / "source.mp3"
    junk.write_bytes(b"not audio at all")
    assert not original_holds_up(junk, STREAM_160)
    assert "Couldn't read the original's bitrate" in caplog.text
