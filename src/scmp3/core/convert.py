"""Turning a downloaded file into the saved format with ffmpeg, and checking the result.

Tags are written afterwards with mutagen, so every conversion drops the source's tags and cover.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from scmp3.core import paths, runner
from scmp3.core.errors import ErrorKind, ScmpError

# A three-hour set converts in a few minutes, so this only stops an ffmpeg that hangs.
FFMPEG_TIMEOUT = 20 * 60  # seconds

# The ffmpeg options that pick the output codec for each conversion (TECH_SPEC §9).
MP3_320_CODEC = ("-c:a", "libmp3lame", "-b:a", "320k")
# -q:a 0 is LAME's V0, its highest VBR quality.
MP3_V0_CODEC = ("-c:a", "libmp3lame", "-q:a", "0")
# faststart puts the index before the audio.
M4A_COPY_CODEC = ("-c:a", "copy", "-movflags", "+faststart")

DURATION_TOLERANCE = 2.0  # seconds
PREVIEW_SECONDS = 30.0  # the length of SoundCloud's previews of Go+ tracks
# Anything shorter isn't a track, even when the source didn't say how long it is.
MIN_SECONDS = 1.0

# The codec that each mutagen file type holds. MP4 files name their own codec instead.
CODECS: dict[type[mutagen.FileType], str] = {
    MP3: "mp3",
    FLAC: "flac",
    OggFLAC: "flac",
    OggOpus: "opus",
    OggVorbis: "vorbis",
    WAVE: "pcm",
    AIFF: "pcm",
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioInfo:
    """What an audio file holds, as mutagen reads it."""

    duration: float  # seconds
    bitrate: int | None  # kbps; None when the file doesn't say
    codec: str | None  # mp3, aac, opus and so on; None for a type not in CODECS
    sample_rate: int | None  # Hz; None when the file doesn't say, as with Opus


def to_mp3_320(
    src: Path, dst: Path, *, cancel: threading.Event | None = None, timeout: float = FFMPEG_TIMEOUT
) -> Path:
    """Encode the first audio stream of `src` as MP3 320 kbps CBR, without tags or cover.

    `dst` must end in .mp3, since ffmpeg picks the output format from the extension.
    """
    return _convert(src, dst, MP3_320_CODEC, cancel=cancel, timeout=timeout)


def to_mp3_v0(
    src: Path, dst: Path, *, cancel: threading.Event | None = None, timeout: float = FFMPEG_TIMEOUT
) -> Path:
    """Encode the first audio stream of `src` as MP3 V0, without tags or cover.

    `dst` must end in .mp3, since ffmpeg picks the output format from the extension.
    """
    return _convert(src, dst, MP3_V0_CODEC, cancel=cancel, timeout=timeout)


def remux_m4a(
    src: Path, dst: Path, *, cancel: threading.Event | None = None, timeout: float = FFMPEG_TIMEOUT
) -> Path:
    """Copy the first audio stream of `src`, unchanged, into an M4A without tags or cover.

    `dst` must end in .m4a, since ffmpeg picks the output format from the extension.
    """
    return _convert(src, dst, M4A_COPY_CODEC, cancel=cancel, timeout=timeout)


def copy_mp3(src: Path, dst: Path) -> Path:
    """Copy an MP3 byte for byte, since encoding it again would lose quality."""
    try:
        shutil.copyfile(src, dst)
    except shutil.SameFileError as error:
        # dst is the source itself here, so it mustn't be deleted as a partial copy.
        raise ScmpError(ErrorKind.internal, f"can't copy {src.name} onto itself") from error
    except OSError as error:
        _remove_output(dst)
        reason = error.strerror or error
        raise ScmpError(ErrorKind.disk, f"can't copy {src.name}: {reason}") from error
    return dst


def ffmpeg_command(ffmpeg: Path, src: Path, dst: Path, codec: Sequence[str]) -> list[str]:
    """The command for one conversion. Only the first audio stream is kept, with no metadata."""
    return [
        str(ffmpeg),
        *("-nostdin", "-y", "-i", str(src)),
        *("-map", "0:a:0", "-vn", "-map_metadata", "-1"),
        *codec,
        str(dst),
    ]


def _convert(
    src: Path, dst: Path, codec: Sequence[str], *, cancel: threading.Event | None, timeout: float
) -> Path:
    """Run one conversion. A cancelled or failed one leaves no output file behind."""
    command = ffmpeg_command(paths.ffmpeg_exe(), src, dst, codec)
    try:
        # The runner also raises Cancelled when the cancel comes just before or after ffmpeg runs.
        runner.run(command, timeout=timeout, error_kind=ErrorKind.convert_failed, cancel=cancel)
    except BaseException:
        _remove_output(dst)
        raise
    return dst


def _remove_output(dst: Path) -> None:
    """Delete a partial output file. A failure is only logged, so the first error is raised."""
    try:
        dst.unlink(missing_ok=True)
    except OSError as error:
        log.warning("Couldn't delete the partial file %s: %s", dst.name, error.strerror or error)


def probe(path: Path) -> AudioInfo:
    """Read an audio file's duration, bitrate, codec and sample rate with mutagen."""
    audio = _open_audio(path)
    info = audio.info
    return AudioInfo(
        duration=float(info.length),
        bitrate=_kbps(getattr(info, "bitrate", None)),
        codec=_codec(audio),
        sample_rate=getattr(info, "sample_rate", None) or None,
    )


def _open_audio(path: Path) -> mutagen.FileType:
    """Open a file with mutagen, or raise ScmpError(convert_failed) when it can't."""
    try:
        audio = mutagen.File(path)
    except mutagen.MutagenError as error:
        # mutagen's message can hold the full path, so it only goes in the debug log.
        log.debug("mutagen couldn't read %s: %s", path, error)
        raise ScmpError(ErrorKind.convert_failed, f"mutagen can't read {path.name}") from error
    if audio is None:
        raise ScmpError(
            ErrorKind.convert_failed, f"{path.name} isn't a type of audio mutagen knows"
        )
    return audio


def _kbps(bitrate: int | None) -> int | None:
    """mutagen's bits per second as kbps, or None when the file doesn't say."""
    return round(bitrate / 1000) if bitrate else None


def _codec(audio: mutagen.FileType) -> str | None:
    """The codec name for a file mutagen opened."""
    if isinstance(audio, MP4):
        return _mp4_codec(audio.info.codec)
    return CODECS.get(type(audio))


def _mp4_codec(codec: str) -> str | None:
    """A short codec name for an MP4 codec id."""
    # Every AAC profile is mp4a.40.<n>, such as mp4a.40.2 for AAC-LC.
    if codec.startswith("mp4a.40"):
        return "aac"
    return codec or None


def validate(path: Path, expected_duration: float | None) -> AudioInfo:
    """Check a finished file before it goes into the library, and return what it holds.

    A file of about 30 s from a longer track is a preview and raises preview_only. Every other
    failed check raises convert_failed.
    """
    _check_size(path)
    info = probe(path)
    if info.duration < MIN_SECONDS:
        raise ScmpError(
            ErrorKind.convert_failed,
            f"{path.name} is {info.duration:.1f} s, too short to be a track",
        )
    # None or 0 means the source didn't say how long the track is.
    if not expected_duration:
        return info
    if _is_preview(info.duration, expected_duration):
        raise ScmpError(
            ErrorKind.preview_only,
            f"{path.name} is {info.duration:.0f} s, a preview of a {expected_duration:.0f} s track",
        )
    if abs(info.duration - expected_duration) > DURATION_TOLERANCE:
        raise ScmpError(
            ErrorKind.convert_failed,
            f"{path.name} is {info.duration:.1f} s, but the track is {expected_duration:.1f} s",
        )
    return info


def _check_size(path: Path) -> None:
    """Raise ScmpError(convert_failed) when the file is missing or empty."""
    try:
        size = path.stat().st_size
    except FileNotFoundError as error:
        raise ScmpError(ErrorKind.convert_failed, f"{path.name} is missing") from error
    if size == 0:
        raise ScmpError(ErrorKind.convert_failed, f"{path.name} is empty")


def _is_preview(duration: float, expected_duration: float) -> bool:
    """Whether a file is about 30 s long while the track is longer."""
    about_30_seconds = abs(duration - PREVIEW_SECONDS) <= DURATION_TOLERANCE
    return about_30_seconds and expected_duration > PREVIEW_SECONDS + DURATION_TOLERANCE
